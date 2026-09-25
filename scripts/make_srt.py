#!/usr/bin/env python3
"""SRT subtitles re-timed to the edl.json timeline, from the transcripts (<clip>.words.json).
Usage: make_srt.py edl.json -o project.srt [--transcripts folder] [--words N] [--chars 42]"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import build

def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def ts(t):
    t = max(0, t)
    h, r = divmod(int(round(t * 1000)), 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def main():
    edl_path = sys.argv[1]
    out = arg('-o', None)
    tdir = arg('--transcripts', os.path.join(os.path.dirname(os.path.abspath(edl_path)), 'transcripts'))
    max_words = int(arg('--words', 0))
    max_chars = int(arg('--chars', 42))
    _, _, _, clips = build(edl_path)
    cache, words = {}, []
    for c in clips:
        base = os.path.splitext(os.path.basename(c['file']))[0]
        if base not in cache:
            p = os.path.join(tdir, base + '.words.json')
            cache[base] = json.load(open(p, encoding='utf-8'))['words'] if os.path.exists(p) else None
            if cache[base] is None:
                print(f'WARNING: no transcript for {base} ({p})')
        for w in cache[base] or []:
            if w.get('filler'):
                continue
            if w['start'] >= c['in_s'] - 0.02 and w['end'] <= c['in_s'] + c['dur_s'] + 0.02:
                t0 = c['off_s'] + (w['start'] - c['in_s'])
                t1 = min(c['off_s'] + c['dur_s'], c['off_s'] + (w['end'] - c['in_s']))
                words.append((max(t0, c['off_s']), t1, w['w']))
    cues, cur = [], []
    for i, (t0, t1, w) in enumerate(words):
        if cur:
            text = ' '.join(x[2] for x in cur)
            gap = t0 - cur[-1][1]
            full = (max_words and len(cur) >= max_words) or (not max_words and len(text) + 1 + len(w) > max_chars)
            if gap > 0.6 or full or (t1 - cur[0][0] > 5):
                cues.append(cur); cur = []
        cur.append((t0, t1, w))
        if re.search(r'[.!?…]$', w):
            cues.append(cur); cur = []
    if cur:
        cues.append(cur)
    lines = []
    for n, cue in enumerate(cues, 1):
        start, end = cue[0][0], cue[-1][1] + 0.15
        if n < len(cues):
            end = min(end, cues[n][0][0] - 0.02)
        end = max(end, start + 0.5)
        lines += [str(n), f'{ts(start)} --> {ts(end)}', ' '.join(x[2] for x in cue), '']
    open(out, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'{out}: {len(cues)} subtitles, {len(words)} words')

if __name__ == '__main__':
    main()
