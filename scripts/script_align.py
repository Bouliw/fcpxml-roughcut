#!/usr/bin/env python3
"""Scripted videos: lines up the script with the takes, word by word, and keeps the best take of every paragraph.

The script is a text file, paragraphs separated by a blank line. Every take of a paragraph is found in the
transcripts through the three-word sequences they share (so Whisper's mistakes and small rewordings do not break the
match), then the best one is kept: the most complete, then the smoothest (fewest hesitations and long pauses), then
the latest (the last take is usually the good one). Writes:
- ranges.json, for edl_from_ranges.py: one range per paragraph, in the order of the script;
- a report: every take found with its score, the choice, and the sentences never said in any take;
- visuals.txt: every passage with an empty line for the pictures to find, for whoever prepares the B-roll.
Usage: script_align.py script.txt clip [clip ...] -o folder [--transcripts dir]"""
import argparse, json, os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import disk_path, transcript_path

def norm(word):
    w = unicodedata.normalize('NFC', word.lower()).replace('’', "'")
    return re.sub(r"[^\w']+", '', w)

def tokens(text):
    return [t for t in (norm(w) for w in re.split(r"[\s\-–—]+", text)) if t]

def paragraphs(text):
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

def sentences(paragraph):
    return [s.strip() for s in re.split(r'(?<=[.!?…])\s+', paragraph) if s.strip()]

def grams(ws, n=3):
    return [tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)] or ([tuple(ws)] if ws else [])

def takes(par, words, clip):
    """Takes of one paragraph in one clip: [(coverage, start, end, first word index, last word index, clip)]."""
    kept = [(k, norm(w['w'])) for k, w in enumerate(words) if not w.get('filler')]  # an "um" never breaks a match
    tw = [t for _, t in kept]
    n = 3 if len(par) >= 3 else len(par)
    index = {}
    for j, g in enumerate(grams(tw, n)):
        index.setdefault(g, []).append(j)
    hits = sorted((j, i) for i, g in enumerate(grams(par, n)) for j in index.get(g, []))
    # A take moves forward through the script and the transcript together. Going back in the script (a retake
    # starts the paragraph again) or a long gap in the transcript starts another take.
    runs = []
    for j, i in hits:
        r = runs[-1] if runs else None
        if r and i >= r['maxi'] - 1 and j - r['last'] <= 12 and abs((j - i) - r['d']) <= max(3, len(par) // 5):
            r['i'].add(i); r['last'] = j; r['maxi'] = max(r['maxi'], i); r['d'] = j - i
        else:
            runs.append({'d': j - i, 'i': {i}, 'first': j, 'last': j, 'maxi': i, 'mini': i})
    total = max(1, len(par) - n + 1)
    out = []
    for r in runs:
        coverage = len(r['i']) / total
        if len(r['i']) < min(2, total) or coverage < 0.15:  # a stray common phrase is not a take
            continue
        first, last = r['first'], min(len(kept) - 1, r['last'] + n - 1)
        # the script's first and last words may not match (Whisper wrote "10h" for "dix heures"): take as many
        # words before and after as the script has there, without crossing a pause
        for _ in range(r['mini']):
            if first == 0 or words[kept[first][0]]['start'] - words[kept[first - 1][0]]['end'] > 0.7:
                break
            first -= 1
        for _ in range(len(par) - (r['maxi'] + n)):
            if last + 1 >= len(kept) or words[kept[last + 1][0]]['start'] - words[kept[last][0]]['end'] > 0.7:
                break
            last += 1
        a, b = kept[first][0], kept[last][0]
        out.append((coverage, words[a]['start'], words[b]['end'], a, b, clip))
    return out

def roughness(words, a, b):
    """Hesitations, long pauses and repeated words inside a take: lower is smoother."""
    span = words[a:b + 1]
    fillers = sum(1 for w in span if w.get('filler'))
    pauses = sum(1 for x, y in zip(span, span[1:]) if y['start'] - x['end'] > 1.0)
    repeats = sum(1 for x, y in zip(span, span[1:]) if norm(x['w']) == norm(y['w']))
    return fillers + pauses + repeats

def said(sentence, transcripts):
    """Best share of the sentence's word pairs found in a row of any transcript."""
    ws = tokens(sentence)
    pairs = grams(ws, 2) if len(ws) >= 2 else grams(ws, 1)
    best = 0.0
    for tw in transcripts:
        have = set(grams(tw, 2 if len(ws) >= 2 else 1))
        best = max(best, sum(1 for p in pairs if p in have) / max(1, len(pairs)))
    return best

def mmss(t):
    m, s = divmod(t, 60)
    return f'{int(m)}:{s:04.1f}'

def main():
    parser = argparse.ArgumentParser(description='Lines up a script with the takes and keeps the best take of every paragraph.')
    parser.add_argument('script', help='script as text, paragraphs separated by a blank line')
    parser.add_argument('clips', nargs='+', help='the takes (camera clips)')
    parser.add_argument('-o', required=True, dest='out', help='folder for ranges.json, the report and visuals.txt')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ in the output folder)')
    args = parser.parse_args()
    with open(args.script, encoding='utf-8') as f:
        pars = paragraphs(f.read())
    tdir = args.transcripts or os.path.join(args.out, 'transcripts')
    clips = {}
    for c in args.clips:
        path = disk_path(os.path.abspath(c))
        p = transcript_path(tdir, path)
        if not os.path.exists(p):
            raise SystemExit(f'No transcript for {os.path.basename(path)} ({p}): run transcribe.sh and words.py first')
        with open(p, encoding='utf-8') as f:
            clips[path] = [w for w in json.load(f)['words'] if not w.get('suspect')]
    os.makedirs(args.out, exist_ok=True)
    ranges, report, visuals = [], [], []
    for k, par in enumerate(pars, 1):
        ws = tokens(par)
        found = [t for path, words in clips.items() for t in takes(ws, words, path)]
        head = f'Paragraph {k}: "{" ".join(par.split()[:8])}..."'
        if not found:
            report.append(f'{head}\n  never said')
            visuals.append(f'[{k}] {par}\n    Visuals:\n')
            continue
        # most complete, then smoothest, then latest
        scored = sorted(found, key=lambda t: (round(t[0], 1), -roughness(clips[t[5]], t[3], t[4]), t[1]), reverse=True)
        best = scored[0]
        report.append(head)
        for t in sorted(found, key=lambda t: (t[5], t[1])):
            mark = '->' if t is best else '  '
            report.append(f'  {mark} {os.path.basename(t[5])} {mmss(t[1])}-{mmss(t[2])}  complete {t[0]:.0%}, '
                          f'hesitations and pauses {roughness(clips[t[5]], t[3], t[4])}')
        missing = [s for s in sentences(par) if said(s, [[norm(w['w']) for w in clips[best[5]][best[3]:best[4] + 1]]]) < 0.5]
        never = [s for s in missing if said(s, [[norm(w['w']) for w in ws_] for ws_ in clips.values()]) < 0.5]
        for s in never:
            report.append(f'     never said in any take: "{s}"')
        for s in missing:
            if s not in never:
                report.append(f'     not in the chosen take, said in another one: "{s}"')
        ranges.append({'file': best[5], 'from': round(best[1], 2), 'to': round(best[2], 2),
                       'note': f'paragraph {k}, take at {mmss(best[1])} ({len(found)} found)'})
        visuals.append(f'[{k}] {par}\n    Visuals:\n')
    with open(os.path.join(args.out, 'ranges.json'), 'w', encoding='utf-8') as f:
        json.dump({'ranges': ranges}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, 'script_report.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(report) + '\n')
    with open(os.path.join(args.out, 'visuals.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(visuals))
    print('\n'.join(report))
    print(f'\n{len(ranges)} of {len(pars)} paragraphs placed -> ranges.json (then edl_from_ranges.py), script_report.txt, visuals.txt')

if __name__ == '__main__':
    main()
