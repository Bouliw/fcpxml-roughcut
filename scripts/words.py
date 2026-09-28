#!/usr/bin/env python3
"""Turns the JSON output of whisper-cli (-ml 1 -sow -oj) into timestamped words and a readable transcript.
With --audio, the words are fitted to the pauses heard in the clip (pauses.py), so pauses inside a long sentence show.
Hesitations, suspect words and abandoned starts (words said again at once: "we went, we went to the lake") are
flagged, for the cuts to leave them out.
Usage: words.py <clip>.json [--gap 0.7] [--audio clip [--track 0]] [--config file.json]
Writes <clip>.words.json and <clip>.txt next to the JSON."""
import argparse, difflib, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

# French and English hesitations
FILLERS = {'euh', 'heu', 'euhh', 'hum', 'hmm', 'mmh', 'uh', 'um', 'erm'}
# Hesitations only when Whisper writes them in lowercase: "Ben" is also a first name
LOWERCASE_FILLERS = {'bah', 'ben'}

# A single word lasting longer than this is usually text Whisper invented on silence (e.g. "Thank you." at the end)
SUSPECT_S = 3.0

# What Whisper writes on silence or noise, learned from the subtitles of TV shows and fan subtitles: the credits of a
# subtitler, never said in a video (on real footage, over two silent 3-minute clips: "Sous-titrage ST' 501" twenty
# times, and "Société Radio-Canada"), and noises written between stars. Suspect, like an invented word.
PHANTOMS = re.compile(r"sous-titrage (st'? ?501|société radio-canada|fr\b)|société radio-canada|\bst' ?501\b|"
                      r"sous-titres (réalisés|faits) par( la communauté)?( d'amara\.org)?|amara\.org|subtitles by|字幕(志愿者|组|由|提供)\S*( [^\sA-Za-z]+)?|"
                      r"ご視聴ありがとうございました", re.I)
VERSION = 3  # of the .words.json files: an older one is made again from Whisper's output (no new transcription)

# Whisper sometimes gives several words in a row one and the same timestamp. After a long pause it is real speech
# whose timing is lost; when a longer run repeats what was just said, it is text Whisper wrote twice (a short
# run like "something like" may well have been said twice)
COLLAPSED_RUN, REPEATED_RUN = 3, 5

def flag_collapsed(words):
    """Marks runs of words sharing one timestamp 'untimed', and 'suspect' too when they repeat the words before."""
    bare = lambda w: re.sub(r'\W', '', w['w'].lower())  # noqa: E731
    i = 0
    while i < len(words):
        j = i
        while j < len(words) and words[j]['start'] - words[i]['start'] <= 0.02 and words[j]['end'] - words[i]['start'] <= 0.02:
            j += 1
        if j - i >= COLLAPSED_RUN:
            run, before = [bare(w) for w in words[i:j]], [bare(w) for w in words[max(0, i - 40):i]]
            repeated = sum(b.size for b in difflib.SequenceMatcher(a=run, b=before, autojunk=False).get_matching_blocks())
            for w in words[i:j]:
                w['untimed'] = True
                w['suspect'] = w['suspect'] or (len(run) >= REPEATED_RUN and repeated >= 0.8 * len(run))
        i = max(j, i + 1)

# Words said twice on purpose ("very very", "no no"): never taken for a stutter
EMPHASIS = {'très', 'trop', 'vraiment', 'super', 'non', 'oui', 'si', 'ah', 'oh', 'ha', 'hé', 'bon', 'very', 'so', 'really',
            'no', 'yes', 'yeah', 'bye', 'ok', 'okay', 'nous', 'vous', 'là'}

def bare(word):
    return re.sub(r'[^\w\']', '', word.lower()).strip("'")

def flag_repeats(words):
    """Marks 'repeat' the first take of what is said again at once, hesitations aside: a stutter ("le le"), a group
    of words ("un petit peu un petit peu") or an abandoned start ("on va, euh, on va aller voir"). Words said twice on
    purpose ("très très") stay. Returns the number of words marked."""
    said = [(i, bare(w['w'])) for i, w in enumerate(words) if not (w.get('filler') or w.get('suspect'))]
    toks = [t for _, t in said]
    marked, i = 0, 0
    while i < len(toks):
        for k in range(min(4, (len(toks) - i) // 2), 0, -1):  # the longest repeated group first
            head = toks[i:i + k]
            if all(head) and toks[i + k:i + 2 * k] == head and (k >= 2 or (head[0] not in EMPHASIS and len(head[0]) <= 5)):
                for x in range(i, i + k):
                    words[said[x][0]]['repeat'] = True
                marked += k
                i += k
                break
        else:
            i += 1
    return marked

def flag_prompt_echo(words):
    """Marks 'suspect' the words of Whisper repeating the text it was started on (languages.PROMPTS) over a silence:
    four words or more of it in a row. Returns how many."""
    try:
        from languages import PROMPTS
    except ImportError:
        return 0
    n, said = 0, [bare(w['w']) for w in words]
    for text in PROMPTS.values():
        echo = [bare(x) for x in text.split()]
        size = 4
        for i in range(len(said) - size + 1):
            window = said[i:i + size]
            if any(window == echo[k:k + size] for k in range(len(echo) - size + 1)):
                for w in words[i:i + size]:
                    if not w['suspect']:
                        w['suspect'] = True
                        n += 1
    return n

def with_hesitations(words, audio, track=0):
    """The hesitations heard in the sound (hesitations.py) put among the words: a hesitation Whisper wrote takes the
    place of the held vowel heard there; one it left out becomes a word "euh", flagged like the others. Returns how
    many were added."""
    import hesitations
    sound = hesitations.Sound(hesitations.read(audio, track))
    added = 0
    for h in hesitations.found(words, sound):
        if h['word'] is not None:
            w = words[h['word']]
            w['start'], w['end'], w['heard'] = round(h['start'], 3), round(h['end'], 3), True
        else:
            words.append({'w': 'euh', 'start': round(h['start'], 3), 'end': round(h['end'], 3), 'filler': True,
                          'suspect': False, 'sound': True})
            added += 1
    words.sort(key=lambda w: w['start'])
    return added

def is_filler(word):
    bare = re.sub(r'\W', '', word)
    return bare.lower() in FILLERS or bare in LOWERCASE_FILLERS

def flag_phantoms(words):
    """Marks 'suspect' the words of a subtitler's credit Whisper invented, and noises written between stars
    ("*music*"). Returns how many."""
    text, starts = '', []
    for w in words:
        starts.append(len(text))
        text += w['w'] + ' '
    n = 0
    for i, w in enumerate(words):
        if '*' in w['w'] and not w['suspect']:
            w['suspect'] = True
            n += 1
    for m in PHANTOMS.finditer(text):
        for i, at in enumerate(starts):
            if m.start() <= at + len(words[i]['w']) and at < m.end() and not words[i]['suspect']:
                words[i]['suspect'] = True
                n += 1
    return n

def in_order(words):
    """Each word starts no earlier than the one before it, and ends no earlier than it starts: fitting to the pauses
    can move the first word of a run Whisper gave one timestamp past the others, and a cut through them would then end
    before it starts (the whole edit failed on it). Returns the number of words moved."""
    moved = 0
    for i, w in enumerate(words):
        start = max(w['start'], words[i - 1]['start']) if i else w['start']
        end = max(w['end'], start)
        if (start, end) != (w['start'], w['end']):
            w['start'], w['end'] = start, end
            moved += 1
    return moved

def load(path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    words, prefix = [], ''
    for seg in d.get('transcription', []):
        t = seg.get('text', '').strip()
        if not t or re.fullmatch(r'\[.*\]|\(.*\)', t):  # [Music], (laughs)...
            continue
        o = seg['offsets']
        if re.fullmatch(r'[^\w]+', t):
            # Punctuation alone (French " ?", " !", " :"): it goes with the previous word, "«" with the next one
            if t[0] in '«(“' or not words:
                prefix += t + (' ' if t[0] == '«' else '')
            else:
                words[-1]['w'] += (' ' if t[0] in '?!:;»' else '') + t
                words[-1]['end'] = o['to'] / 1000.0
            continue
        words.append({'w': prefix + t, 'start': o['from'] / 1000.0, 'end': o['to'] / 1000.0})
        prefix = ''
    return words

def corrections_path(transcript):
    return os.path.join(os.path.dirname(os.path.abspath(transcript)), 'corrections.json')

def correction_key(start, was):
    """Where a correction is kept: the start of the word and the word itself (Whisper gives some words one start)."""
    return f'{start:.2f}|{was}'

def with_corrections(transcript, words, keep_removed=False):
    """The words of a transcript (<clip>.words.json) with the corrections made to the captions (subtitles.py), kept
    in corrections.json next to it: {clip: {"12.34|wether": {"was": "wether", "now": "weather"}}}, keyed by the start of the
    word and the word; "now" empty takes the word out (kept, flagged 'removed', with keep_removed). A correction
    applies only while the word is still the one it was made on."""
    try:
        with open(corrections_path(transcript), encoding='utf-8') as f:
            fixes = json.load(f).get(os.path.basename(transcript)[:-len('.words.json')], {})
    except (OSError, ValueError):
        return words
    if not fixes:
        return words
    out = []
    for w in words:
        fix = fixes.get(correction_key(w['start'], w['w'])) or fixes.get(f'{w["start"]:.2f}')  # (the older key too)
        if fix and fix.get('was') == w['w']:
            if not fix.get('now'):
                if keep_removed:
                    out.append(dict(w, removed=True, filler=True))
                continue
            w = dict(w, w=fix['now'], corrected=True)
        out.append(w)
    return out

def fmt(t):
    m, s = divmod(t, 60)
    return f'{int(m):02d}:{s:05.2f}'

def main():
    parser = argparse.ArgumentParser(description='Turns the JSON output of whisper-cli into timestamped words and a readable transcript.')
    parser.add_argument('json', help='<clip>.json written by transcribe.sh')
    parser.add_argument('--gap', type=float, default=0.7, help='pauses longer than this are shown, in seconds (default 0.7)')
    parser.add_argument('--audio', help='the clip itself: fits the words to the pauses heard in it')
    parser.add_argument('--track', type=int, default=0, help='audio track of the clip (default 0)')
    parser.add_argument('--hesitations', action='store_true', help='with --audio: the hesitations heard in the sound put '
                        'among the words (the held vowels between two words), and the ones Whisper wrote placed where they are heard')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    src, gap = args.json, args.gap
    cfg = settings.load(os.path.dirname(os.path.abspath(src)), args.config)['cuts']
    words = load(src)
    base = re.sub(r'\.json$', '', src)
    moved = None
    if args.audio:
        try:
            import pauses
            spans = pauses.quiet_spans(pauses.levels(args.audio, args.track), cfg['quiet_ratio'], cfg['min_quiet_seconds'])
            moved = pauses.tighten(words, spans)
        except ImportError:
            print('numpy is missing: the words keep the timestamps Whisper gave (pip install numpy)')
    in_order(words)
    for w in words:
        w['filler'] = is_filler(w['w'])
        w['suspect'] = w['end'] - w['start'] > SUSPECT_S
    flag_phantoms(words)
    flag_prompt_echo(words)
    heard = None
    if args.audio and args.hesitations:
        try:
            heard = with_hesitations(words, args.audio, args.track)
        except ImportError:
            print('numpy is missing: only the hesitations Whisper wrote are known')
    flag_collapsed(words)
    repeats = flag_repeats(words) if cfg['remove_repeats'] else 0
    with open(base + '.words.json.part', 'w', encoding='utf-8') as f:  # then renamed: never half written
        json.dump({'words': words, 'fitted_to_sound': moved is not None, 'version': VERSION,
                   'hesitations_heard': heard is not None}, f, ensure_ascii=False, indent=1)
    os.replace(base + '.words.json.part', base + '.words.json')
    lines, cur, cur_start = [], [], None
    prev_end, prev_word = None, None
    for w in words:
        if prev_end is not None and w['start'] - prev_end >= gap:
            if cur:
                lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur)); cur = []
            lines.append(f'        (pause {w["start"] - prev_end:.1f} s)')
        if not cur:
            cur_start = w['start']
        text = f'[{w["w"]}]' if w['filler'] or w.get('repeat') else f'{{{w["w"]}}}' if w['suspect'] else w['w']
        first_untimed = w.get('untimed') and not (prev_word or {}).get('untimed')
        cur.append(('≈' if first_untimed and not w['suspect'] else '') + text)
        prev_word = w
        prev_end = w['end']
        if re.search(r'[.!?…]$', w['w']):
            lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur)); cur = []
    if cur:
        lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur))
    with open(base + '.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    n_f, n_s, n_u = (sum(bool(w.get(k)) for w in words) for k in ('filler', 'suspect', 'untimed'))
    fitted = f', {moved} fitted to the pauses heard' if moved is not None else ''
    heard = f' ({heard} heard in the sound only)' if heard else ''
    print(f'{len(words)} words{fitted}, {n_f} hesitations{heard}, {repeats} said again, {n_s} suspect, {n_u} untimed -> {base}.txt, {base}.words.json')

if __name__ == '__main__':
    main()
