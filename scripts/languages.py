#!/usr/bin/env python3
"""Transcribes a clip where several languages are spoken, each in its own language. One whisper.cpp pass keeps one
language: given French (or detecting it from the first 30 s), an English answer comes out translated into French, or
is left out. So the language is asked piece by piece: 30 s at a time, and where whisper is not sure (the language
changes there, or no one speaks), sentence by sentence, the sound being cut at its pauses. The clip is then
transcribed run by run, each in its language, cut in the pause where the language changes. One language all along:
a single pass, as before, in that language.
Usage: languages.py audio.wav --model ggml-large-v3-turbo.bin --out transcript.json [--language fr|auto]
       (audio.wav: 16 kHz mono, as transcribe.sh extracts it; the JSON is whisper-cli's -oj output)"""
import argparse, json, os, re, subprocess, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SLICE = 30.0  # seconds asked at once, first
SURE = 0.99  # a slice is of one language only when whisper is this sure: on a real video, slices of 0.95 to 0.98
             # ended with 10 s of another language
PIECE = (4.0, 10.0)  # then, where it is not sure: pieces cut at the pauses, of 4 s at least (shorter ones are
                     # joined to the next) and 10 s at most
SURE_PIECE = 0.7  # below it, a piece is left to the pieces around it (music, noise)
GAP = 5.0  # a stretch of speech this long without a word was skipped by whisper
SHORT_RUN = 15.0  # a run of one language this short, next to runs of another, is checked: on its own, a French
                  # sentence can be taken for English (and then written translated)
BATCH = 200  # files per whisper-cli run
# Whisper leaves hesitations out ("euh", "um"): 0 in 5,658 words of real footage. Started on a text full of them, it
# writes them (13 in a clip where it wrote 1), and punctuates better. Only with --hesitations (cuts.hesitations).
PROMPTS = {'fr': 'Euh… donc, euh, voilà. Bon, ben, euh, on y va, hein. Et euh, du coup, euh…',
           'en': 'Um, uh, so, like, uh, you know, I mean, uh…'}
PRIMED = []  # set by --hesitations: [True]

def whisper_cli():
    return os.environ.get('WHISPER_CLI', 'whisper-cli')

def cut(wav, pieces, folder):
    """Writes each (start, end) of the 16 kHz audio to its own file; returns their paths."""
    paths = []
    with wave.open(wav) as src:
        rate = src.getframerate()
        for a, b in pieces:
            src.setpos(int(a * rate))
            cut.count = getattr(cut, 'count', 0) + 1
            path = os.path.join(folder, f'{cut.count:05d}.wav')
            with wave.open(path, 'wb') as out:
                out.setparams(src.getparams())
                out.writeframes(src.readframes(int((b - a) * rate)))
            paths.append(path)
    return paths

def duration(wav):
    with wave.open(wav) as w:
        return w.getnframes() / w.getframerate()

def detect(model, paths):
    """[(language, probability) or None] for each file, from whisper-cli -dl (one run for many files)."""
    found = {}
    for i in range(0, len(paths), BATCH):
        args = [whisper_cli(), '-m', model, '-dl']
        for p in paths[i:i + BATCH]:
            args += ['-f', p]
        r = subprocess.run(args, capture_output=True, text=True, errors='replace')
        if r.returncode:
            raise RuntimeError(f'whisper-cli -dl: {r.stderr.strip()[-300:]}')
        current = None
        for line in (r.stderr + r.stdout).splitlines():
            m = re.search(r"processing '(.+?)' \(", line)
            if m:
                current = m.group(1)
            m = re.search(r'auto-detected language: (\w+) \(p = ([0-9.]+)\)', line)
            if m and current:
                found[current] = (m.group(1), float(m.group(2)))
    return [found.get(p) for p in paths]

def pieces(a, b, spans):
    """[a, b] cut in the middle of its pauses into pieces of PIECE[0] to PIECE[1] seconds. A short piece is joined to
    the one it is closer to, across the shorter of its two pauses (a change of speaker, and of language, leaves a
    longer one); a long one is cut in equal parts."""
    inside = [(p, q) for p, q in spans if a < (p + q) / 2 < b]
    cuts = [(p + q) / 2 for p, q in inside]
    gaps = [q - p for p, q in inside]  # gaps[i]: the pause between segs[i] and segs[i + 1]
    segs = [[x, y] for x, y in zip([a] + cuts, cuts + [b])]
    while len(segs) > 1:
        short = [k for k, (x, y) in enumerate(segs) if y - x < PIECE[0]]
        if not short:
            break
        k = min(short, key=lambda k: segs[k][1] - segs[k][0])
        left = k > 0 and (k == len(segs) - 1 or gaps[k - 1] < gaps[k])
        m = k - 1 if left else k
        segs[m:m + 2] = [[segs[m][0], segs[m + 1][1]]]
        del gaps[m]
    out = []
    for x, y in segs:
        n = int((y - x) // PIECE[1]) + 1 if y - x > PIECE[1] else 1
        out += [(round(x + (y - x) * k / n, 3), round(x + (y - x) * (k + 1) / n, 3)) for k in range(n)]
    return out

def cells(model, wav, total, folder, spans):
    """The language of every part of the clip: [(start, end, language or None, probability)]. A slice of 30 s
    whisper is sure of is that language; the others are asked piece by piece (`spans`: the pauses; without them, an
    unsure slice is left unknown)."""
    slices = [(a, min(a + SLICE, total)) for a in steps(0, total, SLICE)]
    out, unsure = [], []
    for (a, b), d in zip(slices, detect(model, cut(wav, slices, folder))):
        if d and d[1] >= SURE:
            out.append((a, b, d[0], d[1]))
        elif spans is None:
            out.append((a, b, None, d[1] if d else 0.0))
        else:
            unsure += pieces(a, b, spans)
    for (a, b), d in zip(unsure, detect(model, cut(wav, unsure, folder)) if unsure else []):
        sure = d and d[1] >= SURE_PIECE and b - a >= 1.5
        out.append((a, b, d[0] if sure else None, d[1] if d else 0.0))
    return sorted(out)

def steps(a, b, step):
    x = a
    while x < b - 0.05:
        yield x
        x += step

def main_language(cells, given):
    """The language given, or the one spoken longest."""
    if given and given != 'auto':
        return given
    time = {}
    for a, b, lang, _ in cells:
        if lang:
            time[lang] = time.get(lang, 0) + b - a
    return max(time, key=time.get) if time else 'auto'

def runs(cells, main):
    """[(start, end, language)] from the cells. A cell of unknown language takes the language of the cells around
    it when they agree, and the main language when they do not."""
    known = [c[2] for c in cells]
    out = []
    for i, (a, b, lang, _) in enumerate(cells):
        if lang is None:
            before = next((x for x in reversed(known[:i]) if x), None)
            after = next((x for x in known[i + 1:] if x), None)
            lang = before if before == after else (before or after) if not (before and after) else main
        out.append((a, b, lang or main))
    return merged(out)

def merged(parts):
    """Next runs of one language joined."""
    out = []
    for a, b, lang in parts:
        if out and out[-1][2] == lang:
            out[-1] = (out[-1][0], b, lang)
        else:
            out.append((a, b, lang))
    return out

def settle(runs_, sound):
    """Each change of language in a pause: one that falls between two slices, where no pause was, goes to the
    quietest moment within 1.5 s (music under the voice: no clear pause). A run left under 2 s goes to the one
    before it."""
    out = [list(r) for r in runs_]
    for i in range(1, len(out)):
        t = out[i][0]
        if not any(p <= t <= q for p, q in sound.spans):
            t = sound.quietest(t - 1.5, t + 1.5)
            if out[i - 1][0] < t < out[i][1]:
                out[i - 1][1] = out[i][0] = t
    for i, r in enumerate(out):
        if r[1] - r[0] < 2.0 and len(out) > 1:
            r[2] = out[i - 1][2] if i else out[1][2]
    return merged([tuple(r) for r in out])

def confidence(model, wav, piece, langs, folder):
    """{language: mean probability of the tokens whisper writes} for the piece transcribed in each language. The
    language spoken is written with more confidence than one it would be translated into: on real footage, 0.88
    against 0.75 for a French sentence taken for English, 0.83 against 0.48 for an English answer, 0.94-0.98 against
    0.62-0.69 for French. {} when whisper-cli fails."""
    [f] = cut(wav, [piece], folder)
    out = {}
    for lang in langs:
        base = f'{f[:-4]}-{lang}'
        r = subprocess.run([whisper_cli(), '-m', model, '-l', lang, '-ojf', '-np', '-f', f, '-of', base],
                           capture_output=True, text=True, errors='replace')
        if r.returncode:
            return {}
        with open(base + '.json', encoding='utf-8') as fh:
            d = json.load(fh)
        ps = [t['p'] for seg in d.get('transcription', []) for t in seg.get('tokens', [])
              if not t.get('text', '').startswith('[_') and 'p' in t]
        out[lang] = sum(ps) / len(ps) if ps else 0.0
    return out

def checked(model, wav, runs_, folder):
    """A short run of one language between runs of another (or at an end, next to one) keeps its language only
    when whisper writes it with more confidence in that language than in the other; else it takes the other."""
    out = list(runs_)
    for i, (a, b, lang) in enumerate(runs_):
        around = {r[2] for r in runs_[max(0, i - 1):i] + runs_[i + 1:i + 2]}
        if b - a >= SHORT_RUN or len(around) != 1 or lang in around:
            continue
        other = around.pop()
        conf = confidence(model, wav, (a, b), (lang, other), folder)
        if conf and conf[other] > conf[lang]:
            print(f'languages: {mmss(a)}-{mmss(b)} is {other}, not {lang} ({conf[other]:.2f} against {conf[lang]:.2f})', file=sys.stderr)
            out[i] = (a, b, other)
    return merged(out)

def stamp(ms):
    s, ms = divmod(int(ms), 1000)
    return f'{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d},{ms:03d}'

def whisper(model, wav, pieces, folder):
    """{(start, end, language): [whisper segments, with the times of the whole clip]}: each piece transcribed in its
    language, one whisper-cli run per language."""
    found = {}
    for lang in dict.fromkeys(p[2] for p in pieces):
        mine = [p for p in pieces if p[2] == lang]
        files = cut(wav, [(a, b) for a, b, _ in mine], folder)
        args = [whisper_cli(), '-m', model, '-l', lang, '-ml', '1', '-sow', '-oj', '-np'] + prompt(lang)
        for f in files:
            args += ['-f', f, '-of', f[:-4]]
        r = subprocess.run(args, capture_output=True, text=True, errors='replace')
        if r.returncode:
            raise SystemExit(f'whisper-cli failed ({lang}): {r.stderr.strip()[-300:]}')
        for piece, f in zip(mine, files):
            with open(f[:-4] + '.json', encoding='utf-8') as fh:
                d = json.load(fh)
            shift = round(piece[0] * 1000)
            for seg in d.get('transcription', []):
                o = seg['offsets']
                o['from'], o['to'] = o['from'] + shift, o['to'] + shift
                seg['timestamps'] = {'from': stamp(o['from']), 'to': stamp(o['to'])}
            found[piece] = d
    return found

def prompt(lang):
    """whisper-cli's arguments that start it on hesitations in `lang`, when asked (--hesitations)."""
    return ['--prompt', PROMPTS[lang]] if PRIMED and lang in PROMPTS else []

def mark(out):
    """Notes in the transcript that it was started on hesitations: a transcript without it is made again."""
    if not PRIMED:
        return
    with open(out, encoding='utf-8') as f:
        d = json.load(f)
    d['roughcut'] = dict(d.get('roughcut') or {}, hesitations=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

def spoken(seg):
    t = seg.get('text', '').strip()
    return bool(t) and not re.fullmatch(r'\[.*\]|\(.*\)', t)

def skipped(segments, run, sound):
    """The stretches of a run where speech is heard and nothing was written: whisper sometimes jumps over 20 s at
    the start of a piece. [(start, end, language)]"""
    a, b, lang = run
    out, last = [], a
    for seg in sorted((s for s in segments if spoken(s)), key=lambda s: s['offsets']['from']) + [None]:
        start = seg['offsets']['from'] / 1000 if seg else b
        if start - last >= GAP and sound.speech(last, start) >= 0.5:
            out.append((round(last, 3), round(start, 3), lang))
        if seg:
            last = max(last, seg['offsets']['to'] / 1000)
    return out

def transcribe(model, wav, runs_, folder, out, main, sound):
    """Each run transcribed in its language, then what whisper skipped transcribed again on its own, all put back in
    one whisper JSON with the times of the whole clip."""
    found = whisper(model, wav, runs_, folder)
    holes = [h for run in runs_ for h in skipped(found[run]['transcription'], run, sound)]
    if holes:
        print('languages: transcribed again ' + ', '.join(f'{mmss(a)}-{mmss(b)}' for a, b, _ in holes), file=sys.stderr)
        again = whisper(model, wav, holes, folder)
    whole = found[runs_[0]]
    whole['transcription'] = [seg for run in runs_ for seg in found[run]['transcription']]
    for a, b, lang in holes:
        whole['transcription'] += [seg for seg in again[(a, b, lang)]['transcription']
                                   if a * 1000 <= seg['offsets']['from'] < b * 1000]
    whole['transcription'].sort(key=lambda seg: seg['offsets']['from'])
    whole['result'] = {'language': main}
    whole['languages'] = [[a, b, lang] for a, b, lang in runs_]
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(whole, f, ensure_ascii=False, indent=1)

def one_pass(model, wav, lang, out):
    r = subprocess.run([whisper_cli(), '-m', model, '-f', wav, '-l', lang, '-ml', '1', '-sow', '-oj', '-of', out[:-5], '-np']
                       + prompt(lang), stdout=subprocess.DEVNULL)
    if r.returncode:
        raise SystemExit(f'whisper-cli failed (exit {r.returncode})')
    mark(out)

class Sound:
    """The level of the audio every 10 ms (pauses.py): its pauses, its quietest moments, where speech is heard."""
    def __init__(self, wav):
        import numpy as np
        import pauses
        self.np, self.hop = np, pauses.HOP
        self.db = pauses.levels(wav)
        self.spans = pauses.quiet_spans(self.db, 0.35, 0.3)
        floor, speech = np.percentile(self.db, [10, 90]) if len(self.db) else (0.0, 0.0)
        self.loud = self.db >= floor + 0.35 * (speech - floor) if speech - floor >= 6 else np.ones(len(self.db), bool)

    def frames(self, a, b):
        return max(0, int(a / self.hop)), min(len(self.db), int(b / self.hop))

    def quietest(self, a, b):
        """The middle of the quietest 200 ms between a and b."""
        i, j = self.frames(a, b)
        if j - i < 20:
            return round((a + b) / 2, 3)
        level = self.np.convolve(self.db[i:j], self.np.ones(20) / 20, 'valid')
        return round((i + int(self.np.argmin(level)) + 10) * self.hop, 3)

    def speech(self, a, b):
        """The share of the time between a and b where the sound is at speech level."""
        i, j = self.frames(a, b)
        return float(self.loud[i:j].mean()) if j > i else 0.0

def sound_of(wav):
    try:
        return Sound(wav)
    except ImportError:  # no numpy
        return None

def mmss(s):
    return f'{int(s) // 60}:{int(s) % 60:02d}'

def main():
    parser = argparse.ArgumentParser(description='Transcribes each language of a clip in that language.')
    parser.add_argument('audio', help='16 kHz mono WAV')
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', required=True, help='the whisper JSON to write')
    parser.add_argument('--language', default='auto', help='the main language, or auto')
    parser.add_argument('--hesitations', action='store_true', help='start Whisper on a text full of hesitations, so it writes them')
    args = parser.parse_args()
    if args.hesitations:
        PRIMED.append(True)
    total = duration(args.audio)
    sound = sound_of(args.audio)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            found = cells(args.model, args.audio, total, tmp, sound.spans if sound else None)
        except (RuntimeError, OSError) as e:  # the languages could not be asked: one pass, as before
            print(f'languages: not detected ({e})', file=sys.stderr)
            return one_pass(args.model, args.audio, args.language, args.out)
        main_ = main_language(found, args.language)
        parts = runs(found, main_)
        if len(parts) < 2 or not sound:  # one language; or no numpy, so no pause to cut at: one pass, as before
            print(f'languages: {main_}' + ('' if sound else ' (numpy missing: the others in one pass with it)'), file=sys.stderr)
            return one_pass(args.model, args.audio, main_, args.out)
        parts = checked(args.model, args.audio, settle(parts, sound), tmp)
        print('languages: ' + ', '.join(f'{lang} {mmss(a)}-{mmss(b)}' for a, b, lang in parts), file=sys.stderr)
        transcribe(args.model, args.audio, parts, tmp, args.out, main_, sound)
        mark(args.out)

if __name__ == '__main__':
    main()
