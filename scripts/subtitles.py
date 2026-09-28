#!/usr/bin/env python3
"""The subtitles of every video of a project (the edit and its Shorts), from what is said, re-timed to each timeline:
- SRT files for YouTube, in the language of the footage and in English (or subtitles.languages), each covering all
  that is said: a passage spoken in another language is translated by the brain (an English answer in the French file,
  a French question in the English one);
- the obvious slips of the transcription (capitals, apostrophes, spaces: "paris", "il ya") fixed, and the words to
  check by ear listed (proper nouns whose spelling is not sure, words probably misheard): to-check.txt in the project,
  checks.json in each video folder for Final Cut Pro's to-do markers;
- captions.txt, the text of the captions, to correct.
A word corrected in captions.txt, or fixed here, is kept with the transcript (corrections.json, see words.py), so every
video of the footage and every later version gets it. `--regenerate` reads the captions.txt files, keeps their
corrections, then writes the subtitles again and renders the animated captions again (captions.py), in place, so
Final Cut Pro shows them where they already are. What the brain answered is kept (subtitles.json next to the
transcripts): only the lines that changed are asked again.
Usage: subtitles.py project_folder [--regenerate] [--config file.json]"""
import argparse, concurrent.futures, difflib, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain, settings, timeline
from make_srt import edit_words, split_cues, srt
from words import corrections_path, correction_key

BATCH = 100  # subtitles per question to the brain
LINE = 42  # characters per line of a subtitle; two lines at most
LANGUAGE_NAMES = {'fr': 'French', 'en': 'English', 'es': 'Spanish', 'de': 'German', 'it': 'Italian', 'pt': 'Portuguese',
                  'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean', 'ar': 'Arabic', 'ru': 'Russian', 'nl': 'Dutch'}
UI = {'en': {'title': 'Words to check by ear before publishing (proper nouns not sure, words the transcription may have misheard).',
             'fix': 'Fix them in captions.txt, then Roughcut › Regenerate the captions.', 'video': 'Main edit',
             'none': 'Nothing to check.', 'fixed': '{n} obvious slip(s) fixed (capitals, apostrophes, spaces).',
             'untranslated': '{n} subtitle(s) could not be translated: they are in their own language in {files}.',
             'marker': 'Check: {word}',
             'header': '# The captions of this video, one subtitle per line. Fix a word, keep the time at the start of '
             'the line,\n# then Roughcut › Regenerate the captions. Lines starting with # are notes, left as they are.'},
      'fr': {'title': 'Mots à vérifier à l\'écoute avant de publier (noms propres pas sûrs, mots que la transcription a pu mal entendre).',
             'fix': 'Corrige-les dans captions.txt, puis Roughcut › Regénérer les sous-titres.', 'video': 'Montage principal',
             'none': 'Rien à vérifier.', 'fixed': '{n} faute(s) évidente(s) corrigée(s) (majuscules, apostrophes, espaces).',
             'untranslated': '{n} sous-titre(s) n\'ont pas pu être traduits : ils restent dans leur langue dans {files}.',
             'marker': 'À vérifier : {word}',
             'header': '# Les sous-titres de cette vidéo, un par ligne. Corrige un mot en gardant l\'heure en début de '
             'ligne,\n# puis Roughcut › Regénérer les sous-titres. Les lignes qui commencent par # sont des notes.'}}

PROMPT = """You prepare the subtitles of a YouTube video for viewers who speak {langs}. Below are its subtitles,
numbered, each with the language it seems to be spoken in (a guess: correct it when it is wrong). Lines marked
"(context)" are only there to help you understand the others.
For every other line, give:
- "lang": the language it is really spoken in (fr, en, zh...);
- its translation into each of {codes} that is not that language, keeping the meaning, the tone and the register
  (spoken, casual when it is) and the names as they are; never merge or split lines, keep each short enough to read.
List apart, for those lines:
- "fix": the obvious slips of the transcription that change only capitals, apostrophes, hyphens or spaces
  ("paris" -> "Paris", "il ya" -> "il y a"): the words as written, and as they should be;
- "check": the words to check by ear: a proper noun whose spelling is not sure (a person, a little-known place, a
  school, a brand; not a well-known country, city or nationality, once written right), or a word the transcription
  probably got wrong (it does not fit, or sounds like another word that would). The word exactly as written, and why
  in a few words in {ui} with the likely right word; never mention line numbers.
Answer with ONE JSON object only, no text around it:
{{"lines": {{"12": {{"lang": "fr", "en": "..."}}}}, "fix": [{{"id": 12, "word": "paris", "now": "Paris"}}],
 "check": [{{"id": 12, "word": "...", "why": "..."}}]}}

{items}"""

# Words that tell French from English in a line, when Whisper's language for the clip is not the one spoken there
FUNCTION_WORDS = {
    'fr': set("le la les de des du un une et est sont que qui je tu il elle nous vous ils elles pas ne ce ça cette "
              "en au aux pour dans sur avec mais ou donc très plus été être avoir fait faire tout bien voilà alors "
              "parce quand comme aussi moi toi lui leur on y".split()),
    'en': set("the an and is are was were be to of in at for with that this it you we they he she not do does did "
              "have has but so what there here yes just like very can will would my your our their me him them".split())}

def guess_language(text, fallback):
    """The language of a line: Chinese characters, or more French than English function words (or the reverse)."""
    if re.search(r'[一-鿿]', text):
        return 'zh'
    tokens = re.findall(r"[a-zà-ÿœæç]+(?:'[a-zà-ÿ]+)?", text.lower())
    fr = sum(t in FUNCTION_WORDS['fr'] or bool(re.match(r"(l|d|j|c|qu|n|s|m|t)'", t)) or bool(re.search(r'[éèêàçùûôîœ]', t))
             for t in tokens)
    en = sum(t in FUNCTION_WORDS['en'] or t.endswith("'s") or t.endswith("n't") for t in tokens)
    if fr >= en + 2 and fr > en * 1.5:
        return 'fr'
    if en >= fr + 2 and en > fr * 1.5:
        return 'en'
    return fallback

def wrapped(text):
    """A subtitle on two lines when it is longer than one, cut at the space nearest the middle."""
    if len(text) <= LINE:
        return text
    spaces = [m.start() for m in re.finditer(' ', text)]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - len(text) / 2))
    return text[:cut] + '\n' + text[cut + 1:]

def videos(project):
    """The folders of a project that hold a video: the edit, then its Shorts."""
    out = [project] if os.path.exists(os.path.join(project, 'edl.json')) else []
    return out + sorted(os.path.join(project, d) for d in os.listdir(project)
                        if d.startswith('short') and os.path.exists(os.path.join(project, d, 'edl.json')))

def languages_of(tdir, bases):
    """{clip: (main language, [(start, end, language)])} from Whisper's output (languages.py writes the runs)."""
    out = {}
    for base in bases:
        try:
            with open(os.path.join(tdir, base + '.json'), encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            out[base] = (None, [])
            continue
        main = (d.get('result') or {}).get('language')
        out[base] = (main if main and main != 'auto' else None, [tuple(r) for r in d.get('languages') or []])
    return out

def whisper_language(cue, langs, fallback):
    counts = {}
    for w in cue:
        main, runs = langs.get(w[3], (None, []))
        lang = next((lg for a, b, lg in runs if a <= w[4] < b), main) or fallback
        counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else fallback

class Video:
    """One video of the project: its subtitles, each a list of words (start, end, word, clip, start in the clip)."""
    def __init__(self, folder):
        self.folder = folder
        _, self.tl, _, clips = timeline.build(os.path.join(folder, 'edl.json'))
        self.tdir = os.path.realpath(os.path.join(folder, 'transcripts'))
        self.cues = split_cues(edit_words(os.path.join(folder, 'edl.json'), clips, self.tdir, source=True), 0, 2 * LINE)
        self.langs = languages_of(self.tdir, {w[3] for c in self.cues for w in c})
        mains = [m for m, _ in self.langs.values() if m]
        self.fallback = max(set(mains), key=mains.count) if mains else 'en'

    def text(self, n):
        return ' '.join(w[2] for w in self.cues[n])

    def guess(self, n):
        return guess_language(self.text(n), whisper_language(self.cues[n], self.langs, self.fallback))

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def save_json(path, data):
    with open(path + '.part', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + '.part', path)

class Answers:
    """What the brain said about each line, by its text: {'lines': {text: {'lang', 'fr', 'en'...}},
    'fixes': {text: [{'word', 'now'}]}, 'checks': {text: [{'word', 'why'}]}}."""
    def __init__(self, path):
        self.path, self.data = path, load_json(path, {})
        for k in ('lines', 'fixes', 'checks'):
            self.data.setdefault(k, {})

    def done(self, text, targets):
        line = self.data['lines'].get(text)
        return bool(line) and all(t == line.get('lang') or t in line for t in targets)

    def lang(self, text, guess):
        return (self.data['lines'].get(text) or {}).get('lang') or guess

    def translation(self, text, target):
        return (self.data['lines'].get(text) or {}).get(target)

    def save(self):
        save_json(self.path, self.data)

def ask(items, targets, cfg, log=print):
    """One question to the brain: items [(id, guessed language, text, context?)]; its answer, or None."""
    lines = [f'{i} [{lang}]{" (context)" if context else ""} {text}' for i, lang, text, context in items]
    prompt = PROMPT.format(langs=' and '.join(LANGUAGE_NAMES.get(t, t) for t in targets), codes=', '.join(targets),
                           ui=LANGUAGE_NAMES.get(cfg['ui']['language'], 'English'), items='\n'.join(lines))
    try:
        answer, _, _ = brain.ask_json(prompt, cfg)
        return answer if isinstance(answer, dict) else None
    except brain.BrainError as e:
        log(f'subtitles: no answer from the brain ({e})')
        return None

def consult(vids, targets, cfg, answers, log=print, fixes=True):
    """Asks the brain about every line not answered yet, in batches (three at a time), a few lines of context before
    each batch."""
    order, seen = [], set()
    for v in vids:
        for n in range(len(v.cues)):
            text = v.text(n)
            if text not in seen:
                seen.add(text)
                order.append((text, v.guess(n)))
    todo = [k for k, (text, _) in enumerate(order) if not answers.done(text, targets)]
    if not todo:
        return
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    log(f'subtitles: {len(todo)} lines to translate and check, in {len(batches)} question(s) to the brain')

    def one(batch):
        items = [(k + 1, order[k][1], order[k][0], True) for k in range(max(0, batch[0] - 3), batch[0])]
        items += [(k + 1, order[k][1], order[k][0], False) for k in batch]
        return batch, ask(items, targets, cfg, log)

    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        for batch, answer in pool.map(one, batches):
            if not answer:
                continue
            lines = answer.get('lines') if isinstance(answer.get('lines'), dict) else {}
            for k in batch:
                text, guess = order[k]
                got = lines.get(str(k + 1))
                if not isinstance(got, dict):
                    continue
                lang = got.get('lang') if isinstance(got.get('lang'), str) and got['lang'] else guess
                entry = {'lang': lang}
                entry.update({t: got[t].strip() for t in targets if t != lang and isinstance(got.get(t), str) and got[t].strip()})
                answers.data['lines'][text] = entry
                mine = lambda key: [x for x in answer.get(key) or [] if isinstance(x, dict) and str(x.get('id')) == str(k + 1)  # noqa: E731
                                    and isinstance(x.get('word'), str) and x['word'] and x['word'] in text]
                answers.data['fixes'][text] = [{'word': x['word'], 'now': str(x.get('now', ''))} for x in mine('fix')
                                               if fixes and safe_fix(x['word'], str(x.get('now', '')))]
                answers.data['checks'][text] = [{'word': x['word'], 'why': str(x.get('why', ''))} for x in mine('check')]
            answers.save()

def safe_fix(word, now):
    """A fix that changes only capitals, apostrophes, hyphens or spaces: made without asking."""
    bare = lambda s: re.sub(r"[\s'’\-]", '', s).lower()  # noqa: E731
    return bool(now.strip()) and now != word and bare(word) == bare(now)

def apply_fixes(vids, answers):
    """The obvious slips fixed in the transcripts (corrections.json) of every line that has some; the brain's answer
    for a line is kept under its new text. Returns how many words were fixed."""
    found = {}
    for v in vids:
        for n, cue in enumerate(v.cues):
            text = v.text(n)
            for fix in answers.data['fixes'].get(text, []):
                old = fix['word'].split()
                for i in range(len(cue) - len(old) + 1):
                    if [w[2] for w in cue[i:i + len(old)]] == old:
                        for base, start, was, now in changes(cue[i:i + len(old)], fix['now'].split()):
                            found.setdefault(v.tdir, {}).setdefault(base, {})[correction_key(start, was)] = {'was': was, 'now': now}
                        break
    count = 0
    for tdir, fixes in found.items():
        path = corrections_path(os.path.join(tdir, 'x.words.json'))
        kept = load_json(path, {})
        for base, words in fixes.items():
            for key, fix in words.items():
                if kept.get(base, {}).get(key) != fix:
                    kept.setdefault(base, {})[key] = fix
                    count += 1
        save_json(path, kept)
    if count:  # the same lines, as they now read: what the brain said about them follows
        for text, fixes in list(answers.data['fixes'].items()):
            if not fixes:
                continue
            new = text
            for fix in fixes:
                new = re.sub(r'(?<!\S)' + re.escape(fix['word']) + r'(?!\S)', fix['now'], new)
            if new != text:
                for k in ('lines', 'checks'):
                    if text in answers.data[k]:
                        answers.data[k][new] = answers.data[k][text]
                answers.data['fixes'][new] = []
        answers.save()
    return count

def write(vids, targets, answers, cfg, project, fixed, log=print):
    """The SRT files, captions.txt and checks.json of every video, and to-check.txt for the project."""
    ui = UI.get(cfg['ui']['language'], UI['en'])
    report, untranslated = [ui['title'], ui['fix'], ''], {}
    for v in vids:
        name = ui['video'] if v.folder == project else os.path.basename(v.folder).replace('short', 'Short ').replace('-', '').strip()
        langs = [answers.lang(v.text(n), v.guess(n)) for n in range(len(v.cues))]
        for t in targets:
            texts = []
            for n in range(len(v.cues)):
                text = v.text(n)
                got = text if langs[n] == t else answers.translation(text, t)
                if not got:
                    untranslated.setdefault(t, set()).add(text)
                texts.append(wrapped(got or text))
            with open(os.path.join(v.folder, f'subtitles.{t}.srt'), 'w', encoding='utf-8') as f:
                f.write(srt(v.cues, texts))
        checks, lines = [], [ui['header'], '']
        for n, cue in enumerate(v.cues):
            text = v.text(n)
            for c in answers.data['checks'].get(text, [])[:3]:
                at = next((w[0] for w in cue if c['word'].split()[0] in w[2]), cue[0][0])
                checks.append({'at': round(at, 2), 'word': c['word'], 'why': c['why'], 'text': text})
                lines.append(f'#   ⚠ {c["word"]}: {c["why"]}')
            lines.append(f'{stamp(cue[0][0])}  {text}')
        checks = checks[:cfg['subtitles']['max_checks']]
        with open(os.path.join(v.folder, 'captions.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        save_json(os.path.join(v.folder, 'captions.words.json'), {'cues': [[list(w) for w in cue] for cue in v.cues]})
        save_json(os.path.join(v.folder, 'checks.json'), {'checks': checks, 'marker': ui['marker']})
        report.append(f'{name} ({os.path.relpath(os.path.join(v.folder, "captions.txt"), project)})')
        report += [f'  {stamp(c["at"])}  {c["word"]}: {c["why"]}   « {c["text"]} »' for c in checks] or [f'  {ui["none"]}']
        report.append('')
        log(f'{os.path.relpath(v.folder, project) if v.folder != project else "edit"}: {len(v.cues)} subtitles in '
            f'{", ".join(targets)}, {len(checks)} word(s) to check')
    if fixed:
        report.append(ui['fixed'].format(n=fixed))
    missing = sum(len(x) for x in untranslated.values())
    if missing:
        report.append(ui['untranslated'].format(n=missing, files=', '.join(f'subtitles.{t}.srt' for t in sorted(untranslated))))
        log(f'subtitles: {missing} line(s) left untranslated')
    with open(os.path.join(project, 'to-check.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(report).rstrip() + '\n')

def stamp(t):
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return (f'{h}:' if h else '') + f'{int(m):02d}:{s:05.2f}'

# ---------------------------------------------------------------- corrections made by hand

def changes(cue, new):
    """[(clip, start, was, now)]: the words of a subtitle [(start, end, word, clip, start in clip)] given its new text
    as a list of words. A word replaced by several keeps them all (one caption word); one taken out gets ""; words
    added go with the word before them (or the first one, at the start)."""
    old = [w[2] for w in cue]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'insert':
            k = max(0, i1 - 1)
            now = (old[k] + ' ' + ' '.join(new[j1:j2])) if i1 > 0 else (' '.join(new[j1:j2]) + ' ' + old[k])
            out.append((cue[k][3], cue[k][4], cue[k][2], now))
            continue
        k, m = i2 - i1, j2 - j1
        groups = [[new[j1 + q]] for q in range(min(k, m))] + [[] for _ in range(k - min(k, m))]
        if m > k:
            groups[-1] = new[j1 + k - 1:j2]
        for q in range(k):
            w, now = cue[i1 + q], ' '.join(groups[q])
            if now != w[2]:
                out.append((w[3], w[4], w[2], now))
    return out

def corrections_from(folder):
    """What was changed in a video's captions.txt: {clip: {start: {'was', 'now'}}}."""
    words_file, text_file = os.path.join(folder, 'captions.words.json'), os.path.join(folder, 'captions.txt')
    if not (os.path.exists(words_file) and os.path.exists(text_file)):
        return {}
    by_time = {stamp(c[0][0]): c for c in load_json(words_file, {}).get('cues', []) if c}
    out = {}
    with open(text_file, encoding='utf-8') as f:
        for line in f:
            m = re.match(r'\s*((?:\d+:)?\d+:\d+\.\d+)\s+(.*?)\s*$', line)
            if not m or line.lstrip().startswith('#') or m.group(1) not in by_time:
                continue
            for base, start, was, now in changes(by_time[m.group(1)], m.group(2).split()):
                out.setdefault(base, {})[correction_key(start, was)] = {'was': was, 'now': now}
    return out

def keep_corrections(folders):
    """The corrections of every video's captions.txt, merged into corrections.json next to the transcripts."""
    total = 0
    for folder in folders:
        found = corrections_from(folder)
        if not found:
            continue
        path = corrections_path(os.path.join(os.path.realpath(os.path.join(folder, 'transcripts')), 'x.words.json'))
        kept = load_json(path, {})
        for base, fixes in found.items():
            kept.setdefault(base, {}).update(fixes)
            total += len(fixes)
        save_json(path, kept)
    return total

# ---------------------------------------------------------------- the whole

def build_all(project, cfg, log=print):
    folders = videos(project)
    if not folders:
        raise SystemExit(f'No video in {project} (no edl.json)')
    vids = [Video(f) for f in folders]
    counts = {}
    for v in vids:
        for n in range(len(v.cues)):
            lang = v.guess(n)
            counts[lang] = counts.get(lang, 0) + len(v.cues[n])
    main = max(counts, key=counts.get) if counts else 'en'
    targets = cfg['subtitles']['languages'] or list(dict.fromkeys([main, 'en']))
    answers = Answers(os.path.join(os.path.dirname(vids[0].tdir), 'subtitles.json'))
    consult(vids, targets, cfg, answers, log)
    fixed = apply_fixes(vids, answers)
    if fixed:
        vids = [Video(f) for f in folders]  # read again, with the slips fixed
        log(f'subtitles: {fixed} obvious slip(s) fixed')
        # a fixed line can be longer: the lines around it are cut again, and asked about (their slips are left)
        consult(vids, targets, cfg, answers, log, fixes=False)
    write(vids, targets, answers, cfg, project, fixed, log)
    return vids, targets

def rerender(vids, log=print):
    """The animated captions of every video that has them, rendered again from the corrected words."""
    here = os.path.dirname(os.path.abspath(__file__))
    for v in vids:
        mov = os.path.join(v.folder, 'captions.mov')
        if not (os.path.exists(os.path.join(v.folder, 'captions.json')) or os.path.exists(mov)):
            continue
        r = subprocess.run([sys.executable, os.path.join(here, 'captions.py'), os.path.join(v.folder, 'edl.json'), '-o', mov],
                           capture_output=True, text=True)
        log((r.stdout.strip().splitlines() or [''])[-1] if r.returncode == 0 else f'captions: {r.stderr.strip()[-300:]}')

def main():
    parser = argparse.ArgumentParser(description='SRT subtitles in two languages, words to check and the text of the captions, for every video of a project.')
    parser.add_argument('project', help='project folder (edl.json, and short*/ folders)')
    parser.add_argument('--regenerate', action='store_true', help='keep the corrections made in captions.txt, then write '
                        'the subtitles and render the animated captions again')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    project = os.path.abspath(args.project)
    cfg = settings.load(project, args.config)
    cfg['ui']['language'] = os.environ.get('ROUGHCUT_LANG') or cfg['ui']['language']
    if args.regenerate:
        print(f'corrections kept: {keep_corrections(videos(project))}')
    vids, _ = build_all(project, cfg)
    if args.regenerate:
        rerender(vids)
    print(os.path.join(project, 'to-check.txt'))

if __name__ == '__main__':
    main()
