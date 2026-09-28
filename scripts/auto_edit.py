#!/usr/bin/env python3
"""From a folder of footage to a Final Cut Pro project, in one go and without a conversation: transcription, image
analysis, the editing choices (asked to the brain, see brain.py), dialogue levels, zooms, J and L cuts, B-roll, music,
vertical Shorts with animated captions, and the logged clips, all in ONE FCPXML (one event, so Final Cut Pro asks for
a library once), with names made unique by the date and time. It opens the result in Final Cut Pro and sends a
notification. The only question it may ask is which music to use, at the start, so it can then run unattended.

Everything except the judgement calls runs locally. When no brain answers at all, the edit falls back to keeping all
the speech in order, and says so.
A new version of an edit re-uses the transcripts and the analysis, and can start from the choices of a previous
version edited by hand or by an agent (--decisions): every version gets its own dated name.
With --progress (for an app), the steps come as lines "@step {json}" on stdout, and a SIGTERM to the process group
stops everything and removes the unfinished project.
Usage: auto_edit.py footage_folder [--work-dir dir] [--music ask|none|auto|file] [--decisions file.json]
                    [--no-dialogs] [--no-open] [--progress]"""
import argparse, datetime, errno, fcntl, json, os, re, shutil, signal, subprocess, sys, tempfile, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brain, dressing, fcp, review, settings, timeline
from timeline import disk_path, fd
from words import VERSION as WORDS_VERSION, with_corrections

VIDEO = ('.mp4', '.mov', '.m4v', '.mts', '.mxf')
LOG = 'montage.log'  # the log of an edit, in its project folder
AUDIO = ('.mp3', '.m4a', '.wav', '.aif', '.aiff', '.flac', '.aac')
UI = {
    'en': {'start': 'Editing {name}: {n} clips, {dur}. Notification when ready.',
           'music_q': 'Music for "{name}"?', 'music_pick': 'Choose for me', 'music_none': 'No music',
           'music_folder_q': 'Choose your folder of royalty-free music (asked once).',
           'ready': '{name} is ready ({dur}{short}).', 'asking': ' Final Cut Pro is asking for a library{lib}.',
           'short': ', Short {dur}', 'shorts': ', {n} Shorts ({durs})', 'lib': ': choose "{lib}"',
           'fcp_old': 'Final Cut Pro {version} is too old to import the project: update it (10.6 or later).',
           'no_speech': 'No speech in the clips of "{name}": Roughcut edits from what is said.',
           'icloud': '{n} clip(s) of "{name}" are in iCloud Drive but not yet on this Mac: in the Finder, right-click the folder › Download Now, then start again.',
           'no_brain': 'no brain answered: all the speech is kept, in order', 'no_clips': 'No video in the folder "{name}".',
           'failed_step': 'The edit of {name} stopped during {step}. The details are in montage.log, in the project folder.',
           'disk_full': 'Not enough disk space to finish the edit of {name}: free some space, then start it again.',
           'steps': {'transcribe': 'the transcription', 'analyze': 'the look at the footage', 'choose': 'the choice of the cuts',
                     'build': 'the building of the project', None: 'its preparation'},
           'music_off': {'chosen': 'No music (your choice).', 'no_folder': 'No music: no music folder chosen.',
                         'no_tracks': 'No music: no track in {folder}.',
                         'dialog': 'No music: the question could not be shown (details in montage.log).'},
           'no_music': ', no music', 'review': 'Review',
           'listen_title': 'Hesitations and cuts checked (times in each video).', 'listen_edit': 'Main edit',
           'listen_counts': '{name}: {cut} hesitation(s) cut ({dip} of them touching the words, their joins checked), {left} left in, {fixed} cut(s) fixed after listening again ({rounds} listening(s))',
           'listen_cut': 'hesitation cut, in silences', 'listen_dip': 'hesitation touching the words, cut in the quietest moment (join checked)',
           'listen_left': 'hesitation left in (cutting it broke the sentence, or its join failed the checks)',
           'listen_fixed': {'cut': 'hesitation cut after listening again', 'extend': 'word no longer cut', 'trim': 'scrap of a word removed',
                            'rejoin': 'hesitation put back: the words around its join were not heard whole'},
           'listen_kept': 'left as it is', 'listen_why': {}, 'listen_sep': ': '},
    'fr': {'start': 'Montage de {name} lancé : {n} rushs, {dur}. Notification quand c\'est prêt.',
           'music_q': 'Quelle musique pour « {name} » ?', 'music_pick': 'Laisser Roughcut choisir', 'music_none': 'Pas de musique',
           'music_folder_q': 'Choisissez votre dossier de musiques libres de droits (demandé une seule fois).',
           'ready': '{name} est prêt ({dur}{short}).', 'asking': ' Final Cut Pro demande une bibliothèque{lib}.',
           'short': ', Short {dur}', 'shorts': ', {n} Shorts ({durs})', 'lib': ' : choisissez « {lib} »',
           'fcp_old': 'Final Cut Pro {version} est trop ancien pour importer le projet : mettez-le à jour (10.6 ou plus récent).',
           'no_speech': 'Aucune parole dans les rushs de « {name} » : Roughcut monte à partir de ce qui est dit.',
           'icloud': '{n} rush(s) de « {name} » sont dans iCloud Drive mais pas encore sur ce Mac : dans le Finder, clic droit sur le dossier › Télécharger maintenant, puis relancez.',
           'no_brain': 'aucun cerveau n\'a répondu : toute la parole est gardée, dans l\'ordre', 'no_clips': 'Aucune vidéo dans le dossier « {name} ».',
           'failed_step': 'Le montage de {name} s\'est arrêté pendant {step}. Les détails sont dans montage.log, dans le dossier du projet.',
           'disk_full': 'Plus assez d\'espace disque pour finir le montage de {name} : libérez de la place, puis relancez-le.',
           'steps': {'transcribe': 'la transcription', 'analyze': 'l\'analyse des images', 'choose': 'le choix des coupes',
                     'build': 'la génération du projet', None: 'sa préparation'},
           'music_off': {'chosen': 'Sans musique (votre choix).', 'no_folder': 'Sans musique : aucun dossier de musiques choisi.',
                         'no_tracks': 'Sans musique : aucun morceau dans {folder}.',
                         'dialog': 'Sans musique : la question n\'a pas pu s\'afficher (détails dans montage.log).'},
           'no_music': ', sans musique', 'review': 'Relecture',
           'listen_title': 'Hésitations et coupes contrôlées (minutage dans chaque vidéo).', 'listen_edit': 'Montage principal',
           'listen_counts': '{name} : {cut} hésitation(s) coupée(s) (dont {dip} collée(s) aux mots, raccord contrôlé), {left} laissée(s), {fixed} coupe(s) corrigée(s) après réécoute ({rounds} écoute(s))',
           'listen_cut': 'hésitation coupée, dans des silences', 'listen_dip': 'hésitation collée aux mots, coupée au creux du son (raccord contrôlé)',
           'listen_left': 'hésitation laissée (la couper cassait la phrase, ou son raccord ratait le contrôle)',
           'listen_fixed': {'cut': 'hésitation coupée après réécoute', 'extend': 'mot qui n\'est plus coupé', 'trim': 'bout de mot retiré',
                            'rejoin': 'hésitation remise : les mots autour de son raccord ne s\'entendaient pas en entier'},
           'listen_kept': 'laissé tel quel', 'listen_sep': ' : ',
           'listen_why': {'cutting it would leave a word alone': 'la couper laissait un mot seul',
                          'its join would jump in level or sound': 'son raccord sautait en volume ou en timbre'}},
}

class Plain(Exception):
    """A failure the user is told as it is (the message is already in plain words)."""

class Cancelled(BaseException):
    """The edit was stopped (SIGTERM from the app)."""

class Run:
    """The log of one edit, and the scripts it calls."""
    def __init__(self, log_path, progress=False):
        self.log = open(log_path, 'a', encoding='utf-8')
        self.progress = progress
        self.step, self.began = None, datetime.datetime.now()
        self.background = []

    def event(self, kind, **data):
        """A line for the app that follows the edit (with --progress): @kind {json}. The steps' lengths go to the log."""
        if kind == 'step':
            if data['state'] == 'start':
                self.step, self.began = data['name'], datetime.datetime.now()
            else:
                self.say(f'step {data["name"]}: {(datetime.datetime.now() - self.began).total_seconds():.0f} s')
        if self.progress:
            print(f'@{kind} ' + json.dumps(data, ensure_ascii=False), flush=True)

    def say(self, text):
        print(text, flush=True)
        self.log.write(text + '\n')
        self.log.flush()

    def command(self, name, args, python=sys.executable):
        cmd = (['bash', os.path.join(HERE, name)] if name.endswith('.sh') else [python, os.path.join(HERE, name)]) + [str(a) for a in args]
        self.say('$ ' + ' '.join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)))
        return cmd

    def done(self, name, out, code, began):
        self.log.write(out + f'({name}: {(datetime.datetime.now() - began).total_seconds():.1f} s)\n')
        self.log.flush()
        if code:  # a negative code: stopped by the system (signal), with nothing to say for itself
            last = out.strip().splitlines()[-1] if out.strip() and code > 0 else f'stopped by signal {-code}' if code < 0 else code
            raise RuntimeError(f'{name}: {last}')
        return out

    def script(self, name, *args):
        began = datetime.datetime.now()
        r = subprocess.run(self.command(name, args), capture_output=True, text=True)
        return self.done(name, r.stdout + r.stderr, r.returncode, began)

    def start(self, name, *args):
        """A script running in the background while the edit goes on; wait() gives its output."""
        out = tempfile.TemporaryFile('w+', encoding='utf-8')
        p = subprocess.Popen(self.command(name, args), stdout=out, stderr=subprocess.STDOUT, text=True)
        self.background.append((p, name, out, datetime.datetime.now()))
        return p

    def wait(self, p):
        for item in list(self.background):
            if item[0] is p:
                self.background.remove(item)
                _, name, out, began = item
                code = p.wait()
                out.seek(0)
                text = out.read()
                out.close()
                return self.done(name, text, code, began)

    def stop_background(self):
        for p, *_ in self.background:
            if p.poll() is None:
                p.terminate()

def notify(title, message):
    print(f'[{title}] {message}', flush=True)
    if sys.platform == 'darwin' and not os.environ.get('ROUGHCUT_NO_NOTIFY'):
        subprocess.run(['osascript', '-e', f'display notification {brain.applescript(message)} with title {brain.applescript(title)}'],
                       capture_output=True)

def mmss(t):
    m, s = divmod(int(round(t)), 60)
    return f'{m}:{s:02d}'

DATALESS = 0x40000000  # SF_DATALESS: a file whose content is still in iCloud (or another file provider)

def not_downloaded(files, stat=os.stat):
    """The files whose content is not on this Mac yet: reading them would wait for a download, with nothing said."""
    return [f for f in files if getattr(stat(f), 'st_flags', 0) & DATALESS]

def slug(name):
    return re.sub(r'[^\w\- ]+', '', name).strip() or 'video'

# ---------------------------------------------------------------- what the brain reads

CONNECTORS = {'donc', 'mais', 'et', 'alors', 'puis', 'parce', 'bon', 'enfin', 'ensuite', 'sinon', 'bref',
              'so', 'but', 'and', 'then', 'because', 'anyway', 'now', 'well'}

def sentences(words, max_seconds=12.0):
    """[(start, end, text)] from timestamped words: a sentence ends on . ! ? or a pause of 0.7 s, and one longer than
    max_seconds is cut where the speaker breathes, so the brain can keep part of a long stretch."""
    out, cur = [], []
    for w in words:
        if w.get('suspect'):
            continue
        if cur and w['start'] - cur[-1]['end'] >= 0.7:
            out.append(cur); cur = []
        cur.append(w)
        if re.search(r'[.!?…]$', w['w']):
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    out = [p for s in out for p in _split(s, max_seconds)]
    return [(round(s[0]['start'], 2), round(s[-1]['end'], 2),
             ' '.join(('[' + w['w'] + ']') if w.get('filler') or w.get('repeat') else w['w'] for w in s)) for s in out]

def _split(s, max_seconds, shortest=2.0):
    """A sentence too long, cut in two between words and again until every piece fits: at the longest gap between
    words (a breath), a little rather after a comma or before "so", "but"..., and near the middle when all else is
    equal. No piece under `shortest` seconds."""
    if s[-1]['end'] - s[0]['start'] <= max_seconds:
        return [s]
    best, at = None, None
    for i in range(1, len(s)):
        left, right = s[i - 1]['end'] - s[0]['start'], s[-1]['end'] - s[i]['start']
        if left < shortest or right < shortest:
            continue
        score = (s[i]['start'] - s[i - 1]['end'] + (0.15 if re.search(r'[,;:]$', s[i - 1]['w']) else 0)
                 + (0.05 if re.sub(r'\W', '', s[i]['w'].lower()) in CONNECTORS else 0) - 0.1 * abs(left - right) / (left + right))
        if best is None or score > best:
            best, at = score, i
    if at is None:
        return [s]
    return _split(s[:at], max_seconds, shortest) + _split(s[at:], max_seconds, shortest)

PROMPT = """You are the editor of a YouTube channel. From the transcripts of the camera clips below, decide the edit.
Answer with ONE JSON object only, no text around it.

The clips are named R1, R2... Every line is a sentence or part of one, with its start and end in seconds in that clip:
[start-end] text. Words in [brackets] are hesitations and words said twice: they are cut out anyway.
{cut}
Ranges are in seconds of a clip; copy line start and end times exactly, a range can span consecutive lines.
{rejects}
Return:
{{
  "hook": {{"rush": "R1", "from": 0.0, "to": 0.0}},     // 5-15 s played first and NOT repeated in "main": the most
                                                        // striking line of the video (a surprising fact, a strong
                                                        // opinion, a funny or moving moment) that makes sense without
                                                        // any context and makes one want the rest; never a greeting,
                                                        // an introduction or the answer to a question not heard (null if none)
  "main": [{{"rush": "R1", "from": 0.0, "to": 0.0, "chapter": "optional chapter title"}}],
  "broll": [{{"shot": "B001", "rush": "R1", "at": 0.0, "note": "why here"}}],   // shots from the list below, laid over
                                                        // the moment of clip "rush" at "at" seconds where it is talked about
{irl}
  "interviews": [{{"rush": "R1", "at": 0.0, "who": "who, in a few words"}}],  // each person interviewed: the clip and the
                                                        // time where they start answering the first question (a
                                                        // lower third is placed there); [] if none
{shorts}
  "title_choices": ["", "", ""],
  "description": "2 to 4 sentences",
  "tags": ["10 to 15 tags"]
}}
Chapters: only if the edit lasts over 3 minutes; the first is on the first range after the hook, at least 3, each
at least 10 s; short titles (a place or what happens), as they would show on screen. Write titles, description and tags in the language of the video.

{clips}
{catalogue}"""

def load_sentences(cache, path, max_seconds):
    p = timeline.transcript_path(os.path.join(cache, 'transcripts'), path)
    with open(p, encoding='utf-8') as f:
        return sentences(with_corrections(p, json.load(f)['words']), max_seconds)

SHORTS = """  "shorts": [[{{"rush": "R1", "from": 0.0, "to": 0.0}}]],  // up to {n} vertical Short{s} of 30-60 s, from
                                                        // different moments, the best first (fewer if the video has
                                                        // fewer strong moments). Each one is ONE subject told by ONE
                                                        // person: one passage, or two passages of the same speaker on
                                                        // the same subject with a digression cut between them. It
                                                        // starts on a line that grabs attention on its own (not "and",
                                                        // "so", "but", not the answer to a question not heard) and ends
                                                        // on a punchline or a conclusion; never the opening or greeting"""
NO_SHORT = '  "shorts": [],'

# How hard the brain cuts (auto.cut). "normal" tells it the length to aim at (about half of what was said: on a test
# video of about 30 minutes, 14-15 minutes instead of 18-20 with "light", the wording of 0.2.0); "tight" about a third.
KEEP = {'light': 0.7, 'normal': 0.5, 'tight': 0.35}
DROP = """Drop false starts and repeated takes (keep the last good one), an idea already said (keep its best
wording), comments on what the camera shows that add nothing ("look", "it is really nice here"), remarks about the
filming itself, small talk, rambling and dead ends."""
VLOG_DROP = """Drop false starts and repeated takes (keep the last good one), an idea already said (keep its best
wording), rambling, dead ends, and remarks about the camera itself ("I cut the sound"). The spontaneous talk while
filming stays, even short or unpolished: reactions, what is being discovered ("a waterfall!"), exchanges with the people
met; it is what makes a vlog."""
CUT_RULES = """Every line is kept or dropped on its own, inside long passages too. Keep a line only if it brings something new
(a fact, an opinion, a joke, an emotion) or is needed to follow the story. {drop} But keep
the jokes and the lines that show the personality of the person filming: they are why people watch. An answer kept
needs the question that leads to it, unless it makes sense alone. Keep the announcement of what the video is about in
full (its last good take). Interviews are the heart of a video that has them: everyone interviewed appears, with their
best answers, and it is the comments of the person filming that are cut rather than the answers; when the video
announces a number of interviews, all of them are in the edit. A question asked in the language of the video and then
again in another language for the person interviewed: keep only the one in the language of the video, unless the
answer cannot be understood without the other. Keep the story understandable and in chronological order after the
hook, and end on a line that closes it (a conclusion or a goodbye), not in the middle of a subject."""
CUT = {
    'light': """Cut lightly, for a vlog: the clips hold {speech} of speech, and a good edit of them lasts about {target}. Keep the
moments where the person filming talks while doing something, even short ones (arriving somewhere, discovering a place,
a "let's go!"): they carry the vlog. """ + CUT_RULES.replace('{drop}', VLOG_DROP),
    'normal': """Cut hard: the clips hold {speech} of speech, and a good edit of them lasts about {target}, shorter if much of it is
weak. """ + CUT_RULES.replace('{drop}', DROP),
    'tight': """Cut very hard, for a fast edit: the clips hold {speech} of speech, and the edit lasts about {target}, shorter if
much of it is weak. Keep only the strongest lines of each passage, the ones that carry it. """ + CUT_RULES.replace('{drop}', DROP),
}
STYLE_CUT = {'vlog': 'light', 'discussion': 'normal'}  # auto.style, when auto.cut does not say otherwise
IRL = """  "irl": [{{"shot": "B001", "note": "what it shows"}}],  // shots from the list below played in the edit itself, between
                                                        // the spoken passages, with their own sound: the best moments
                                                        // without speech (the atmosphere, the way there, an action),
                                                        // about {n} of them, more where nothing is said for a long time
                                                        // (a morning filmed without talking); they go in the order of
                                                        // the day, and are not used again as B-roll"""
NO_IRL = '  "irl": [],'
IRL_SECONDS = 4.0  # an IRL moment: this long, around the best-looking moment of its shot

def build_prompt(rushes, cache, catalogue, rejects, max_seconds=12.0, shorts=1, cut='normal', irl=False):
    """`shorts`: how many Shorts to ask for at most; one per 2.5 minutes of speech, so a short video is not cut into
    Shorts that cover all of it."""
    clips, speech = [], 0.0
    for rid, path in rushes.items():
        sents = load_sentences(cache, path, max_seconds)
        speech += sum(b - a for a, b, _ in sents)
        lines = '\n'.join(f'[{a}-{b}] {t}' for a, b, t in sents)
        clips.append(f'### {rid} ({os.path.basename(path)})\n{lines or "(no speech)"}')
    n = min(shorts, max(1, int(speech // 150)))
    minutes = lambda x: f'{round(x / 60)} minutes' if x >= 180 else f'{int(x)} seconds'  # noqa: E731
    bad = [f'{rid} {r["from"]}-{r["to"]} s ({", ".join(r["why"])})' for rid, rs in rejects.items() for r in rs]
    shots = [f'{s["id"]}: {s["rush"]} {s["from"]}-{s["to"]} s, {", ".join(s["labels"]) or "no label"}' for s in catalogue]
    return PROMPT.format(
        rejects=('Avoid these unusable stretches: ' + '; '.join(bad) + '\n') if bad else '',
        shorts=SHORTS.format(n=n, s='s' if n > 1 else '') if shorts else NO_SHORT,
        cut=CUT.get(cut, CUT['normal']).format(speech=minutes(speech), target=minutes(speech * KEEP.get(cut, 0.5))),
        irl=IRL.format(n=max(3, min(len(catalogue), round(speech * KEEP.get(cut, 0.5) / 40)))) if irl and catalogue else NO_IRL,
        clips='\n\n'.join(clips),
        catalogue=('B-roll shots (no speech, usable):\n' + '\n'.join(shots)) if shots else 'No B-roll shot.')

def clean(decisions, rushes, durations):
    """The brain's answer, keeping only ranges that exist."""
    def ok_range(r):
        try:
            rid, a, b = r['rush'], float(r['from']), float(r['to'])
        except (KeyError, TypeError, ValueError):
            return None
        # Whisper can time the last word of a clip past its end (32.98 s in a clip of 32.38 s): the brain copies
        # that time, and the range, the hook of a real edit, was dropped; it now ends with its clip
        if rid not in rushes or not 0 <= a < min(b, durations[rid]) or b > durations[rid] + 2.0:
            return None
        out = {'rush': rid, 'from': a, 'to': min(b, durations[rid])}
        if isinstance(r.get('chapter'), str) and r['chapter'].strip():
            out['chapter'] = r['chapter'].strip()[:60]
        return out
    d = decisions if isinstance(decisions, dict) else {}
    hook = ok_range(d['hook']) if isinstance(d.get('hook'), dict) else None
    main = [x for x in (ok_range(r) for r in d.get('main') or []) if x]
    if hook:  # the hook plays once, at the start: out of the story it came from
        main = [p for r in main for p in _minus(r, hook)]
    shorts = d.get('shorts') if isinstance(d.get('shorts'), list) else []
    if shorts and all(isinstance(r, dict) for r in shorts):  # one Short, given as its ranges
        shorts = [shorts]
    if not shorts and isinstance(d.get('short'), list):  # one Short, as asked before several could be
        shorts = [d['short']]
    shorts = [s for s in ([x for x in (ok_range(r) for r in s if isinstance(r, dict)) if x] for s in shorts if isinstance(s, list)) if s]
    broll = [b for b in d.get('broll') or [] if isinstance(b, dict) and b.get('rush') in rushes and b.get('shot')]
    irl = [b for b in d.get('irl') or [] if isinstance(b, dict) and b.get('shot')]
    interviews = []
    for iv in d.get('interviews') or []:
        try:
            if iv['rush'] in rushes and 0 <= float(iv['at']) <= durations[iv['rush']]:
                interviews.append({'rush': iv['rush'], 'at': float(iv['at']), 'who': str(iv.get('who') or '')[:60]})
        except (KeyError, TypeError, ValueError):
            continue
    return {'hook': hook, 'main': main, 'shorts': shorts, 'broll': broll, 'irl': irl, 'interviews': interviews,
            'title_choices': [t for t in d.get('title_choices') or [] if isinstance(t, str)][:3],
            'description': d.get('description') if isinstance(d.get('description'), str) else '',
            'tags': [t for t in d.get('tags') or [] if isinstance(t, str)][:15]}

def resolve_shots(items, catalogue, older=None):
    """Each B-roll choice with the shot it names: the file and times it carries (a version's brain-answer.json has
    them), else the catalogue it was chosen from (`older`: the broll.json next to the choices given), else this run's.
    Shot numbers change with the catalogue (transcripts refreshed, a setting changed): an older choice must never land
    on another shot, so one whose shot is not found is left out."""
    now, then = {s['id']: s for s in catalogue}, {s['id']: s for s in older or []}
    out = []
    for b in items:
        if all(k in b for k in ('file', 'from', 'to')):
            shot = b
        else:
            shot = then.get(b['shot']) if older is not None else now.get(b['shot'])
        if shot:
            out.append(dict(b, file=shot['file'], **{'from': shot['from'], 'to': shot['to']},
                            **({'best': shot['best']} if 'best' in shot else {})))
    return out

def with_irl(ranges, irl, order):
    """The ranges of the edit with the IRL moments put in, in the order of the day: each before the first passage of
    the story filmed after it (the last passage, the ending, stays last), the hook staying first. `irl`: shots
    {'file', 'from', 'to', 'best', 'note'}; `order`: {file: its place among the clips, as filmed}."""
    key = lambda r: (order.get(r['file'], 0), r['from'])  # noqa: E731
    head = [r for r in ranges if r.get('note') == 'hook']
    story = [r for r in ranges if r.get('note') != 'hook']
    for shot in sorted(irl, key=key):
        moment = irl_moment(shot)
        if not moment:
            continue
        i = next((k for k, r in enumerate(story) if key(r) > key(moment)), len(story))
        if story and i == len(story):
            i -= 1  # the ending stays last
        story.insert(i, moment)
    return head + story

def irl_moment(shot, marker=None):
    """The range that plays a shot in the edit with its own sound: IRL_SECONDS around its best moment (None if the
    shot is too short)."""
    best = shot.get('best', (shot['from'] + shot['to']) / 2)
    a = max(shot['from'], min(best - IRL_SECONDS / 2, shot['to'] - IRL_SECONDS))
    b = min(shot['to'], a + IRL_SECONDS)
    if b - a < 2.0:
        return None
    return {'file': shot['file'], 'from': round(a, 2), 'to': round(b, 2), 'whole': True, 'role': 'effects',
            'marker': (marker or ('IRL ' + shot.get('note', '')).strip())[:80]}

def reviewed(run, ranges, rushes, durations, cache, catalogue, used, cfg, label, given=None, project=None):
    """The edit read again as a viewer (review.py): its ranges with the fixes, and the brain's answer (None when
    there was no review). `used`: the shots already in the edit, as (file, from); `given`: an answer to re-use."""
    sentences = {f: load_sentences(cache, f, cfg['cuts']['max_sentence_seconds']) for f in rushes.values()}
    rush_of = {f: rid for rid, f in rushes.items()}
    lines = review.lines_of(ranges, sentences, rush_of)
    free = [s for s in catalogue if (s['file'], s['from']) not in used]
    if given is None:
        prompt = review.build_prompt(ranges, lines, sentences, rush_of, free)
        if project:
            with open(os.path.join(project, 'review-prompt.txt'), 'w', encoding='utf-8') as f:
                f.write(prompt)
        try:
            given, engine, _ = brain.ask_json(prompt, cfg)
        except brain.BrainError as e:
            run.say(f'review: none ({e})')
            return ranges, None
        run.say(f'review: {engine}')
    fixed, done = review.apply(ranges, lines, given, rushes, durations, {s['id']: s for s in free}, irl_moment, label)
    for kind, why, detail in done:
        run.say(f'review: {kind} {detail}' + (f' ({why})' if why else ''))
    if not done:
        run.say('review: nothing to fix')
    return fixed, given

def chapters_first(ranges, first_title, lead=2):
    """A chapter starts on the IRL moments that lead into it (the way to the place, `lead` of them at most: a
    morning filmed without a word is not the scene it leads to), not after them; when the story has chapters, the
    first range after the hook starts one (YouTube wants the first at 0:00), named `first_title` if the brain gave it
    none."""
    for k, r in enumerate(ranges):
        j = k
        while r.get('chapter') and j > max(0, k - lead) and ranges[j - 1].get('whole') and not ranges[j - 1].get('chapter'):
            j -= 1
        if j < k:
            ranges[j]['chapter'] = r.pop('chapter')
    story = [r for r in ranges if r.get('note') != 'hook']
    if story and any(r.get('chapter') for r in story) and not story[0].get('chapter'):
        story[0]['chapter'] = first_title
    return ranges

def _minus(r, cut):
    """Range r without the part covered by range `cut` (same clip): zero, one or two pieces of at least 1 s."""
    if r['rush'] != cut['rush'] or r['to'] <= cut['from'] or r['from'] >= cut['to']:
        return [r]
    pieces = [dict(r, to=cut['from']), dict({k: v for k, v in r.items() if k != 'chapter'}, **{'from': cut['to']})]
    return [p for p in pieces if p['to'] - p['from'] >= 1.0]

def without_brain(rushes, cache, durations, max_seconds=12.0, opening=30.0):
    """No brain answered: all the speech in order, and a Short from the densest minute after the opening."""
    main = []
    for rid, path in rushes.items():
        sents = load_sentences(cache, path, max_seconds)
        if sents:
            main.append({'rush': rid, 'from': sents[0][0], 'to': sents[-1][1]})
    short = densest(rushes, cache, max_seconds, opening)
    return {'hook': None, 'main': main, 'broll': [], 'irl': [], 'title_choices': [], 'description': '', 'tags': [],
            'shorts': [short] if short else []}

def densest(rushes, cache, max_seconds, opening, around=None, avoid=()):
    """A Short in one clip: the 30-60 s run of sentences with the most speech, outside the opening of the video (the
    first `opening` seconds of the first clip) and out of the Shorts in `avoid`. With `around` (a range), the run must
    hold it."""
    best, first = None, next(iter(rushes))
    for rid, path in rushes.items():
        if around and rid != around['rush']:
            continue
        sents = load_sentences(cache, path, max_seconds)
        start = sents[0][0] + opening if sents and rid == first else float('-inf')
        for i, (a, _, _) in enumerate(sents):
            if a < start:
                continue
            j, spoken = i, 0.0
            while j < len(sents) and sents[j][1] - a <= 60:
                spoken += sents[j][1] - sents[j][0]
                j += 1
            end = sents[j - 1][1] if j > i else a
            if around and not (a <= around['from'] + 0.5 and end >= around['to'] - 0.5):
                continue
            if any(_shared([{'rush': rid, 'from': a, 'to': end}], s) for s in avoid):
                continue
            key = (end - a >= 30, spoken)  # 30 s or more first, then the most speech
            if j > i and (best is None or key > best[0]):
                best = (key, rid, a, end)
    return [{'rush': best[1], 'from': best[2], 'to': best[3]}] if best else []

def _shared(x, y):
    """Seconds that two lists of ranges have in common."""
    return sum(max(0.0, min(a['to'], b['to']) - max(a['from'], b['from'])) for a in x for b in y if a['rush'] == b['rush'])

def shorts_count(value):
    """The setting auto.short: how many Shorts at most (true: 3, false: none)."""
    if isinstance(value, bool):
        return 3 if value else 0
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 3

def check_shorts(d, rushes, cache, max_seconds, opening, count):
    """The brain's Shorts, `count` at most, each from a moment of its own (one sharing over a third of its length with
    an earlier one is dropped). A Short that takes from the opening of the video (the introduction, rarely the
    strongest part) is built instead around the hook, or from the densest minute after the opening that no other Short
    holds. Returns (shorts, what the replaced ones were built from)."""
    first = next(iter(rushes))
    sents = load_sentences(cache, rushes[first], max_seconds)
    end = (sents[0][0] if sents else 0.0) + opening
    in_opening = lambda r: r['rush'] == first and r['from'] < end  # noqa: E731
    kept, changed = [], []
    for short in d['shorts']:
        if len(kept) >= count:
            break
        if any(in_opening(r) for r in short):
            hook, new = d['hook'], []
            if hook and hook['to'] - hook['from'] > 0 and not in_opening(hook) and not any(_shared([hook], s) for s in kept):
                new, why = densest(rushes, cache, max_seconds, opening, around=hook, avoid=kept), 'around the hook'
            if not new:
                new, why = densest(rushes, cache, max_seconds, opening, avoid=kept), 'the densest minute after the opening'
            if not new and kept:
                continue
            short = new or short
            changed.append(why)
        length = sum(r['to'] - r['from'] for r in short)
        if all(_shared(short, s) <= length / 3 for s in kept):
            kept.append(short)
    return kept, changed

# ---------------------------------------------------------------- the music question

def pick_music(mode, cfg, config_dir, name, t, dialogs, estimate):
    """(track, None), or (None, why there is no music: 'chosen', 'no_folder', 'no_tracks' or 'dialog')."""
    if mode == 'none':
        return None, 'chosen'
    if mode not in ('ask', 'auto'):
        return disk_path(os.path.abspath(os.path.expanduser(mode))), None
    folder = cfg['auto']['music_folder']
    if not folder or not os.path.isdir(os.path.expanduser(folder)):
        if not dialogs:
            return None, 'no_folder'
        status, folder = brain.dialog_folder(t['music_folder_q'], timeout=600)
        if status == 'error':
            return None, 'dialog'
        if not folder:
            return None, 'no_folder'
        brain.save_setting(config_dir, 'auto', {'music_folder': folder})
    folder = os.path.expanduser(folder)
    tracks = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(AUDIO):
            try:
                tracks.append((timeline.probe_audio(os.path.join(folder, f))['duration'], f))
            except SystemExit:
                continue
    if not tracks:
        return None, 'no_tracks'
    # long enough first, then the closest to the edit's likely length
    tracks.sort(key=lambda x: (x[0] < estimate, abs(x[0] - estimate)))
    if mode == 'auto' or not dialogs or not cfg['auto']['ask_music']:
        return os.path.join(folder, tracks[0][1]), None
    items = [t['music_pick']] + [f'{f} ({mmss(d)})' for d, f in tracks[:8]] + [t['music_none']]
    errors = len(brain.DIALOG_ERRORS)
    choice = brain.dialog_choose(items, t['music_q'].format(name=name), items[0], timeout=600)
    if not choice or choice == t['music_none']:
        return None, 'dialog' if len(brain.DIALOG_ERRORS) > errors else 'chosen'
    if choice == t['music_pick']:
        return os.path.join(folder, tracks[0][1]), None
    return os.path.join(folder, choice.rsplit(' (', 1)[0]), None

# ---------------------------------------------------------------- the run

def main():
    parser = argparse.ArgumentParser(description='From a folder of footage to a Final Cut Pro project, without a conversation.')
    parser.add_argument('source', help='folder of camera clips')
    parser.add_argument('--work-dir', default=os.environ.get('ROUGHCUT_PROJECTS') or os.path.expanduser('~/Movies/Roughcut'),
                        help="where the projects go; its roughcut.json holds the settings (default: Roughcut's projects folder, ~/Movies/Roughcut)")
    parser.add_argument('--music', default='ask', help='ask (default), auto, none, or a track')
    parser.add_argument('--decisions', help="the editing choices to use instead of asking the brain (a version's brain-answer.json, edited)")
    parser.add_argument('--no-dialogs', action='store_true', help='never ask anything (first choices by default)')
    parser.add_argument('--no-open', action='store_true', help='do not open the result in Final Cut Pro')
    parser.add_argument('--progress', action='store_true', help='print the steps as "@step {json}" lines, for an app')
    args = parser.parse_args()
    if args.progress:  # the app stops the whole group (whisper, ffmpeg...) with one signal
        os.setpgrp()
        def stop(*_):
            raise Cancelled()
        signal.signal(signal.SIGTERM, stop)
    work = os.path.abspath(os.path.expanduser(args.work_dir))
    os.makedirs(work, exist_ok=True)
    cfg = settings.load(work)
    cfg['ui']['language'] = os.environ.get('ROUGHCUT_LANG') or cfg['ui']['language']  # the app's language wins
    t = UI.get(cfg['ui']['language'], UI['en'])
    source = disk_path(os.path.abspath(os.path.expanduser(args.source)))
    name = os.path.basename(source.rstrip('/'))
    title = 'Rough cut' if cfg['ui']['language'] != 'fr' else 'Montage'
    files = sorted(os.path.join(source, f) for f in os.listdir(source)
                   if f.lower().endswith(VIDEO) and not f.startswith('.')) if os.path.isdir(source) else []
    version, app = fcp.best_version()  # the FCPXML the installed Final Cut Pro imports
    cloud = not_downloaded(files)
    if not files or not version or cloud:  # nothing to edit, or not yet, or nothing to edit it with: said at once
        message = (t['no_clips'].format(name=name) if not files else t['icloud'].format(n=len(cloud), name=name) if cloud
                   else t['fcp_old'].format(version=fcp.app_version(app) or ''))
        if args.progress:
            print('@failed ' + json.dumps({'why': message}, ensure_ascii=False), flush=True)
        notify(title, message)
        raise SystemExit(message)
    now = datetime.datetime.now()
    for stamp in [now.strftime('%Y-%m-%d %Hh%M'), now.strftime('%Y-%m-%d %Hh%M%S')] + [now.strftime('%Y-%m-%d %Hh%M%S') + f'-{n}' for n in range(2, 100)]:
        project = os.path.join(work, f'{slug(name)} {stamp}')
        try:  # created or not in one step: two edits started together never get the same folder
            os.makedirs(project)
            break
        except FileExistsError:
            continue
    run = Run(os.path.join(project, LOG), args.progress)
    if shutil.which('caffeinate'):  # the Mac stays awake until the edit ends (idle sleep would pause an hour-long edit)
        subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lock = None
    try:
        infos = {f: timeline.probe(f) for f in files}
        rushes = {f'R{i}': f for i, f in enumerate(files, 1)}
        durations = {rid: infos[f]['duration'] for rid, f in rushes.items()}
        total = sum(durations.values())
        dialogs = not args.no_dialogs
        if dialogs and not cfg['brain']['engine']:  # the first launch: which brain
            if brain.choose(work, cfg['ui']['language']):
                cfg = settings.load(work)
        song, no_music = pick_music(args.music, cfg, work, name, t, dialogs, estimate=0.5 * total)
        music_note = t['music_off'][no_music].format(folder=cfg['auto']['music_folder']) if no_music else ''
        notify(title, t['start'].format(name=name, n=len(files), dur=mmss(total)) + (' ' + music_note if music_note else ''))
        if os.environ.get('ROUGHCUT_VERSION'):  # which engine made this edit
            run.say(f'Roughcut {os.environ["ROUGHCUT_VERSION"]}')
        run.say(f'{name}: {len(files)} clips, {mmss(total)}; music: {song and os.path.basename(song)}'
                + (f' ({no_music})' if no_music else '') + f'; brain: {cfg["brain"]["engine"] or "claude"}')
        for e in brain.DIALOG_ERRORS:
            run.say(f'dialog error: {e}')
        cache = os.path.join(work, '.cache', slug(name))
        tdir, adir = os.path.join(cache, 'transcripts'), os.path.join(cache, 'analysis')
        os.makedirs(tdir, exist_ok=True)
        lock = open(os.path.join(cache, '.lock'), 'w')  # two edits of the same footage: the second waits for the cache
        fcntl.flock(lock, fcntl.LOCK_EX)
        cuts = cfg['cuts']
        hes = {True: 'all', False: 'none'}.get(cuts['hesitations'], cuts['hesitations'])  # none, clean or all
        cuts = dict(cuts, hesitations=hes)
        hes = hes != 'none'  # Whisper started on hesitations, and the ones heard in the sound put among the words
        if hes:
            os.environ['ROUGHCUT_HESITATIONS'] = '1'  # for transcribe.sh
        whispers = {f: os.path.join(tdir, os.path.splitext(os.path.basename(f))[0] + '.json') for f in files}
        new = [f for f in files if not os.path.exists(whispers[f]) and not os.path.exists(timeline.transcript_path(tdir, f))]
        if hes:  # a transcript made before, without the hesitations: made again
            new += [f for f in files if f not in new and os.path.exists(whispers[f]) and not _primed(whispers[f])]
        for f in [f for f in new if not infos[f]['audio_channels']]:  # no sound, nothing to transcribe: B-roll only
            with open(timeline.transcript_path(tdir, f), 'w', encoding='utf-8') as fh:
                json.dump({'words': [], 'fitted_to_sound': True}, fh)
            new.remove(f)
        run.event('info', name=name, project=project, clips=len(files), duration=round(total, 1),
                  to_transcribe=round(sum(infos[f]['duration'] for f in new), 1), music=song and os.path.basename(song),
                  no_music=music_note or None, engine=cfg['brain']['engine'] or 'claude')
        run.event('step', name='transcribe', state='start')
        # the look at the footage starts now, while the sound is transcribed: the video decoder and the GPU work side
        # by side; its measurements are kept, and it sees the transcripts in a moment
        measuring = run.start('analyze.py', *files, '-o', adir)
        done = 0.0
        for f in files:  # transcripts are kept: a new version of the same footage starts here
            if f in new:
                run.script('transcribe.sh', f, tdir, cfg['auto']['transcription_language'])
                done += infos[f]['duration']
                run.event('transcribed', seconds=round(done, 1))
            if os.path.exists(whispers[f]) and not _fitted(timeline.transcript_path(tdir, f), hes):  # fitted to the pauses heard
                run.script('words.py', whispers[f], '--audio', f, *(['--hesitations'] if hes else []))
        run.event('step', name='transcribe', state='end')
        spoken = 0
        for f in files:
            with open(timeline.transcript_path(tdir, f), encoding='utf-8') as fh:
                spoken += sum(not (w.get('suspect') or w.get('filler')) for w in json.load(fh)['words'])
        if not spoken:  # only B-roll: nothing to cut from
            raise Plain(t['no_speech'].format(name=name))
        run.event('step', name='analyze', state='start')
        run.wait(measuring)
        run.script('analyze.py', *files, '-o', adir, '--transcripts', tdir)  # from the measurements kept: only the judgements
        for link, target in (('transcripts', tdir), ('analysis', adir)):
            os.symlink(target, os.path.join(project, link))
        run.script('broll.py', *files, '-o', os.path.join(project, 'broll.json'))
        with open(os.path.join(project, 'broll.json'), encoding='utf-8') as f:
            catalogue = json.load(f)['shots']
        by_file = {v: k for k, v in rushes.items()}
        for s in catalogue:
            s['rush'] = by_file.get(s['file'], '?')
        rejects = {}
        for rid, f in rushes.items():
            with open(os.path.join(adir, os.path.splitext(os.path.basename(f))[0] + '.analysis.json'), encoding='utf-8') as fh:
                rejects[rid] = json.load(fh)['rejects']
        fcntl.flock(lock, fcntl.LOCK_UN)
        run.event('step', name='analyze', state='end')
        run.event('step', name='choose', state='start')
        count = shorts_count(cfg['auto']['short'])
        style = cfg['auto']['style']
        prompt = build_prompt(rushes, cache, catalogue, rejects, cuts['max_sentence_seconds'], count,
                              cfg['auto']['cut'] or STYLE_CUT.get(style, 'light'), irl=style == 'vlog')
        with open(os.path.join(project, 'brain-prompt.txt'), 'w', encoding='utf-8') as f:
            f.write(prompt)
        note = None
        try:
            if args.decisions:
                with open(args.decisions, encoding='utf-8') as f:
                    answer, engine = json.load(f), 'the choices given'
            else:
                answer, engine, note = brain.ask_json(prompt, cfg)
            with open(os.path.join(project, 'brain-answer.json'), 'w', encoding='utf-8') as f:
                json.dump(answer, f, ensure_ascii=False, indent=1)
            d = clean(answer, rushes, durations)
            older = os.path.join(os.path.dirname(os.path.abspath(args.decisions)), 'broll.json') if args.decisions else None
            if older and os.path.exists(older) and not os.path.samefile(older, os.path.join(project, 'broll.json')):
                with open(older, encoding='utf-8') as f:
                    older = json.load(f)['shots']
            else:
                older = None
            d['broll'] = resolve_shots(d['broll'], catalogue, older)
            d['irl'] = resolve_shots(d['irl'], catalogue, older) if style == 'vlog' else []
            played = {(b['file'], b['from']) for b in d['irl']}  # a shot played in the edit is not laid over it too
            d['broll'] = [b for b in d['broll'] if (b['file'], b['from']) not in played]
            with open(os.path.join(project, 'brain-answer.json'), 'w', encoding='utf-8') as f:  # the shots themselves kept
                json.dump(dict(answer, broll=d['broll'], irl=d['irl']), f, ensure_ascii=False, indent=1)
            run.say(f'brain: {engine}' + (f' ({note})' if note else ''))
            if not d['main']:
                raise brain.BrainError('no usable range in the answer')
        except brain.BrainError as e:
            run.say(f'brain: none ({e})')
            d, note = without_brain(rushes, cache, durations, cuts['max_sentence_seconds'], cfg['auto']['short_skip_opening_seconds']), t['no_brain']
        d['shorts'], changed = check_shorts(d, rushes, cache, cuts['max_sentence_seconds'], cfg['auto']['short_skip_opening_seconds'], count)
        for why in changed:
            run.say(f'short: one chosen took from the opening of the video; built instead from {why}')
        # the edit: the hook, then the story with its IRL moments in the order of the day, read again as a viewer;
        # B-roll where the brain placed it; the music under it
        ranges = ([dict(d['hook'], note='hook')] if d['hook'] else []) + d['main']
        ranges = [{k: v for k, v in dict(r, file=rushes[r['rush']]).items() if k != 'rush'} for r in ranges]
        ranges = with_irl(ranges, d.get('irl', []), {f: i for i, f in enumerate(rushes.values())})
        given = answer.get('review') if note != t['no_brain'] and isinstance(answer, dict) else None
        if cfg['auto']['review'] and note != t['no_brain'] and (given is not None or not args.decisions):
            # choices given (--decisions) ask the brain nothing: their review is re-used, if they have one
            used = {(b['file'], b['from']) for b in d['broll'] + d.get('irl', [])}
            ranges, d['review'] = reviewed(run, ranges, rushes, durations, cache, catalogue, used, cfg, t['review'],
                                           given=given, project=project)
            if d['review'] is not None:
                with open(os.path.join(project, 'brain-answer.json'), 'w', encoding='utf-8') as f:
                    json.dump(dict(answer, broll=d['broll'], irl=d['irl'], review=d['review']), f, ensure_ascii=False, indent=1)
        ranges = chapters_first(ranges, 'Introduction')
        run.event('step', name='choose', state='end')
        run.event('step', name='build', state='start')
        spec = dict(cut_spec(cuts), project=f'{name} – {stamp}', ranges=ranges)
        with open(os.path.join(project, 'ranges.json'), 'w', encoding='utf-8') as f:
            json.dump(spec, f, ensure_ascii=False, indent=1)
        run.script('edl_from_ranges.py', os.path.join(project, 'ranges.json'), '-o', os.path.join(project, 'edl.json'))
        with open(os.path.join(project, 'edl.json'), encoding='utf-8') as f:
            edl = json.load(f)
        edl['broll'] = [{'file': b['file'], 'in': b['from'], 'out': min(b['to'], b['from'] + 5),
                         'at_source': float(b['at']), 'source': rushes[b['rush']], 'note': f'{b["shot"]} {b.get("note", "")}'.strip()}
                        for b in d['broll'] if _said(edl, rushes[b['rush']], b.get('at'))]
        if song:
            edl['music'] = {'file': song, 'snap_broll': True}
        edl['interviews'] = [{'file': rushes[iv['rush']], 'at': iv['at'], 'who': iv['who']} for iv in d.get('interviews', [])]
        with open(os.path.join(project, 'edl.json'), 'w', encoding='utf-8') as f:
            json.dump(edl, f, ensure_ascii=False, indent=1)
        videos = [project]
        for i, short in enumerate(d['shorts'], 1):  # each Short: its own folder, captions and project
            sdir = os.path.join(project, 'short' if len(d['shorts']) == 1 else f'short-{i}')
            os.makedirs(sdir)
            for link, target in (('transcripts', tdir), ('analysis', adir)):
                os.symlink(target, os.path.join(sdir, link))
            title = f'{name} – Short – {stamp}' if len(d['shorts']) == 1 else f'{name} – Short {i} – {stamp}'
            with open(os.path.join(sdir, 'ranges.json'), 'w', encoding='utf-8') as f:
                json.dump({**cut_spec(cuts), 'project': title, 'vertical': True, 'ranges': [
                    {'file': rushes[r['rush']], 'from': r['from'], 'to': r['to']} for r in short]}, f, ensure_ascii=False, indent=1)
            run.script('edl_from_ranges.py', os.path.join(sdir, 'ranges.json'), '-o', os.path.join(sdir, 'edl.json'))
            videos.append(sdir)
        # each video listened to once assembled: hesitations left, words cut at an edge, scraps of words (verify_edit.py)
        if cuts['verify']:
            for v in videos:
                try:
                    run.script('verify_edit.py', os.path.join(v, 'edl.json'), '--language', cfg['auto']['transcription_language'],
                               '--rounds', cuts['verify_rounds'], '--hesitations', cuts['hesitations'],
                               '--splice-level', cuts['splice_level_db'], '--splice-timbre', cuts['splice_timbre'])
                except RuntimeError as e:
                    run.say(f'verify: none ({e})')
            write_listen(project, videos, t)
        # the subtitles of every video, in two languages, and the words to check, marked in Final Cut Pro
        try:  # the edit is worth more than its subtitles: without them, it goes on
            run.script('subtitles.py', project)
        except RuntimeError as e:
            run.say(f'subtitles: none ({e})')
        for v in videos:
            try:
                with open(os.path.join(v, 'checks.json'), encoding='utf-8') as f:
                    found = json.load(f)
            except (OSError, ValueError):
                continue
            with open(os.path.join(v, 'edl.json'), encoding='utf-8') as f:
                vedl = json.load(f)
            vedl['markers'] = vedl.get('markers', []) + [{'at': c['at'], 'text': found['marker'].format(word=c['word']),
                                                          'todo': True} for c in found['checks']]
            with open(os.path.join(v, 'edl.json'), 'w', encoding='utf-8') as f:
                json.dump(vedl, f, ensure_ascii=False, indent=1)
        dress = cfg['auto']['dressing']
        overlay = []
        if 'captions' in dressing.features(dress):
            try:
                run.script('captions.py', os.path.join(project, 'edl.json'), '-o', os.path.join(project, 'captions.mov'))
                made = os.path.join(project, 'captions.json')
                overlay = ['--overlay', made if os.path.exists(made) else os.path.join(project, 'captions.mov')]
            except RuntimeError as e:
                run.say(f'captions of the edit: none ({e})')
        parts = [os.path.join(project, 'logged.fcpxml'), os.path.join(project, 'edit.fcpxml')]
        run.script('organize.py', *files, '-o', parts[0], '--analysis', adir, '--transcripts', tdir, '--event', 'logged',
                   '--fcpxml-version', version)
        edit_args = [os.path.join(project, 'edl.json'), '-o', parts[1], '--level-audio', '--fades', '--split-edits',
                     '--fcpxml-version', version, '--language', cfg['ui']['language'], *overlay,
                     *(['--zoom-jump-cuts'] if cfg['auto']['zooms'] else [])]
        try:
            run.script('make_fcpxml.py', *edit_args, '--dressing', dress)
        except RuntimeError as e:  # the edit is worth more than its dressing: without it, once more
            if dress == 'none':
                raise
            run.say(f'dressing: left out, the project did not pass the check with it ({e})')
            run.script('make_fcpxml.py', *edit_args, '--dressing', 'none')
        _, tl, _, clips = timeline.build(os.path.join(project, 'edl.json'))
        length = float(tl['total_f'] * fd(tl['fps']))
        shorts_len = []
        for sdir in videos[1:]:
            run.script('captions.py', os.path.join(sdir, 'edl.json'), '-o', os.path.join(sdir, 'captions.mov'))
            parts.append(os.path.join(sdir, 'short.fcpxml'))
            run.script('make_fcpxml.py', os.path.join(sdir, 'edl.json'), '-o', parts[-1], '--level-audio', '--fades', '--follow-face',
                       '--fcpxml-version', version,
                       '--overlay', os.path.join(sdir, 'captions.mov'))
            _, stl, _, _ = timeline.build(os.path.join(sdir, 'edl.json'))
            shorts_len.append(float(stl['total_f'] * fd(stl['fps'])))
        final = os.path.join(project, f'{slug(name)} {stamp}.fcpxml')
        run.script('fcpxml_merge.py', *parts, '-o', final, '--event', f'{name} – {stamp}', '--version', version)
        chapters = [(c['off_s'], c['chapter']) for c in clips if c.get('chapter')]
        if chapters:  # YouTube wants the first chapter at 0:00: the hook opens it
            chapters[0] = (0.0, chapters[0][1])
        with open(os.path.join(project, 'publication.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(['TITLES', *d['title_choices'], '', 'DESCRIPTION', d['description'], '',
                               *(['CHAPTERS', *(f'{mmss(a)} {c}' for a, c in chapters), ''] if chapters else []),
                               'TAGS', ', '.join(d['tags']), '',
                               'SUBTITLES', 'subtitles.*.srt (in each video folder); words to check: to-check.txt', '']))
        if cfg['auto']['open_in_final_cut'] and not args.no_open:
            fcp.open_in_fcp(final)
        lib = cfg['auto']['library']
        opened = cfg['auto']['open_in_final_cut'] and not args.no_open
        message = t['ready'].format(name=name, dur=mmss(length) + ('' if song else t['no_music']),
                                    short=(t['short'].format(dur=mmss(shorts_len[0])) if len(shorts_len) == 1 else
                                           t['shorts'].format(n=len(shorts_len), durs=', '.join(map(mmss, shorts_len))) if shorts_len else ''))
        if opened:  # the library question is only there when Final Cut Pro was opened
            message += t['asking'].format(lib=t['lib'].format(lib=lib) if lib else '')
        run.say(message + (f' ({note})' if note else ''))
        run.event('step', name='build', state='end')
        run.event('done', fcpxml=final, project=project, length=round(length, 2), shorts=[round(x, 2) for x in shorts_len],
                  library=lib or None, note=note, music=song and os.path.basename(song), no_music=music_note or None,
                  message=message + (f' {note}.' if note else ''))
        notify(title, message + (f' {note}.' if note else ''))
        print(final)
    except Cancelled:  # stopped from the app: nothing half-made is left
        run.say('cancelled')
        run.log.close()
        shutil.rmtree(project, ignore_errors=True)
        run.event('cancelled')
        raise SystemExit(130)
    except (Exception, SystemExit) as e:  # anything: the user hears about it in plain words, the details are in the log
        run.log.write(traceback.format_exc())
        full = isinstance(e, OSError) and e.errno == errno.ENOSPC or 'No space left' in str(e)
        if full:  # the disk is full: what this edit wrote goes, its log stays
            for item in os.listdir(project):
                if item != LOG:
                    path = os.path.join(project, item)
                    shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
        message = str(e) if isinstance(e, Plain) else (t['disk_full'] if full else t['failed_step']).format(
            name=name, step=t['steps'].get(run.step, t['steps'][None]))
        run.say(message)
        run.event('failed', why=message, detail=str(e)[:300], log=os.path.join(project, LOG))
        notify(title, message)
        raise SystemExit(1)
    finally:
        run.stop_background()
        if lock:
            lock.close()

def _fitted(words_json, hesitations=False):
    """The words were fitted to the pauses heard, by the words.py of this version (a newer one flags more), with the
    hesitations heard in the sound when they are asked for."""
    try:
        with open(words_json, encoding='utf-8') as f:
            d = json.load(f)
        return ('fitted_to_sound' in d and d.get('version', 1) >= WORDS_VERSION
                and (d.get('hesitations_heard', False) or not hesitations))
    except (OSError, ValueError):
        return False

def _primed(whisper_json):
    """The transcript was made with Whisper started on hesitations (languages.py --hesitations)."""
    try:
        with open(whisper_json, encoding='utf-8') as f:
            return bool((json.load(f).get('roughcut') or {}).get('hesitations'))
    except (OSError, ValueError):
        return False

def cut_spec(cuts):
    """How edl_from_ranges.py cuts, from the settings (cuts.*)."""
    hes = {True: 'all', False: 'none'}.get(cuts['hesitations'], cuts['hesitations'])
    return {'pause': cuts['pause_seconds'], 'pre': cuts['margin_before'], 'post': cuts['margin_after'],
            'reach': cuts['silence_reach_seconds'], 'room': cuts['silence_room_seconds'],
            'min_cut': cuts['min_cut_seconds'], 'min_piece': cuts['min_piece_seconds'], 'hesitations': hes,
            'splice_level': cuts['splice_level_db'], 'splice_timbre': cuts['splice_timbre']}

def write_listen(project, videos, t):
    """to-listen.txt: the moments of each video worth a listen, from what verify_edit.py heard and did."""
    lines = [t['listen_title'], '']
    for v in videos:
        try:
            with open(os.path.join(v, 'verify.json'), encoding='utf-8') as f:
                report = json.load(f)
        except (OSError, ValueError):
            continue
        name = t['listen_edit'] if v == project else os.path.basename(v).replace('short', 'Short ').replace('-', '').strip()
        rounds = report.get('rounds', [])
        fixed = [x for r in rounds for x in r.get('fixes', [])]
        kept = [x for r in rounds for x in r.get('kept', [])]
        moments = report.get('moments', [])
        cut = [m for m in moments if m['kind'] == 'cut']
        dip = [m for m in moments if m['kind'] == 'dip']
        left = [m for m in moments if m['kind'] == 'kept']
        lines.append(t['listen_counts'].format(name=name, cut=len(cut) + len(dip), dip=len(dip), left=len(left), fixed=len(fixed),
                                               rounds=len(rounds)))
        for m in cut[:6]:
            lines.append(f'  {mmss(m["at"])}  {t["listen_cut"]}   « {m["text"]} »')
        for m in dip[:8]:
            lines.append(f'  {mmss(m["at"])}  {t["listen_dip"]}   « {m["text"]} »')
        for m in left[:5]:
            lines.append(f'  {mmss(m["at"])}  {t["listen_left"]}   « {m["text"]} »')
        for x in fixed[:8]:
            label = t['listen_fixed'].get(x['kind'].split('-')[0], x['kind'])
            lines.append(f'  ~{mmss(x["at"])}  {label}' + (t['listen_sep'] + x['what'] if x['what'] else ''))
        for x in kept[:5]:
            lines.append(f'  ~{mmss(x["at"])}  {t["listen_kept"]}{t["listen_sep"]}{x["what"]} ({t["listen_why"].get(x["why"], x["why"])})')
        lines.append('')
    with open(os.path.join(project, 'to-listen.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines).rstrip() + '\n')

def _said(edl, source, at):
    """The B-roll's moment is in the edit (its speech was kept)."""
    try:
        at = float(at)
    except (TypeError, ValueError):
        return False
    return any(os.path.abspath(c['file']) == os.path.abspath(source) and c['in'] - 0.1 <= at <= c['out'] + 0.1 for c in edl['clips'])

if __name__ == '__main__':
    main()
