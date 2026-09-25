#!/usr/bin/env python3
"""Turns the JSON output of whisper-cli (-ml 1 -sow -oj) into timestamped words and a readable transcript.
Usage: words.py <clip>.json [--gap 0.7]
Writes <clip>.words.json and <clip>.txt next to the JSON."""
import json, sys, re, os

# French and English hesitations
FILLERS = {'euh', 'heu', 'euhh', 'hum', 'hmm', 'mmh', 'bah', 'ben', 'uh', 'um', 'erm'}

def load(path):
    d = json.load(open(path, encoding='utf-8'))
    words = []
    for seg in d.get('transcription', []):
        t = seg.get('text', '').strip()
        if not t or re.fullmatch(r'\[.*\]|\(.*\)', t):  # [Music], (laughs)...
            continue
        o = seg['offsets']
        words.append({'w': t, 'start': o['from'] / 1000.0, 'end': o['to'] / 1000.0})
    return words

def fmt(t):
    m, s = divmod(t, 60)
    return f'{int(m):02d}:{s:05.2f}'

def main():
    src = sys.argv[1]
    gap = float(sys.argv[sys.argv.index('--gap') + 1]) if '--gap' in sys.argv else 0.7
    words = load(src)
    base = re.sub(r'\.json$', '', src)
    for w in words:
        w['filler'] = re.sub(r'[^\wÀ-ÿ]', '', w['w'].lower()) in FILLERS
    json.dump({'words': words}, open(base + '.words.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    lines, cur, cur_start = [], [], None
    prev_end = None
    for w in words:
        if prev_end is not None and w['start'] - prev_end >= gap:
            if cur:
                lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur)); cur = []
            lines.append(f'        (pause {w["start"] - prev_end:.1f} s)')
        if not cur:
            cur_start = w['start']
        cur.append(f'[{w["w"]}]' if w['filler'] else w['w'])
        prev_end = w['end']
        if re.search(r'[.!?…]$', w['w']):
            lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur)); cur = []
    if cur:
        lines.append(f'[{fmt(cur_start)}–{fmt(prev_end)}] ' + ' '.join(cur))
    open(base + '.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    n_f = sum(w['filler'] for w in words)
    print(f'{len(words)} words, {n_f} hesitations found -> {base}.txt, {base}.words.json')

if __name__ == '__main__':
    main()
