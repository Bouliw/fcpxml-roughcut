#!/usr/bin/env python3
"""Finds the pauses in the sound of a clip and fits the words of the transcript to them. Whisper's word timestamps run
on through silences (a word often ends where the next one starts, a second later), so a transcript alone shows few
pauses: the sound does. A stretch is quiet when its level stays below a threshold set between the clip's noise floor
and its speech level, for long enough.
Usage: pauses.py clip [--track 0] [--ratio 0.35] [--min 0.2]    (prints the quiet stretches)"""
import argparse, subprocess

HOP = 0.01  # seconds per level reading

def levels(path, track=0, rate=16000):
    """Level of the sound in dB, every 10 ms."""
    import numpy as np
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-map', f'0:a:{track}', '-ac', '1', '-ar', str(rate),
                        '-f', 's16le', '-'], capture_output=True)
    if r.returncode:
        raise SystemExit(f'Cannot read the sound of {path}: {r.stderr.decode(errors="replace").strip()[-200:]}')
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768
    hop = int(rate * HOP)
    n = len(a) // hop
    if not n:
        return np.zeros(0)
    db = 10 * np.log10(np.mean(a[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    return np.convolve(db, np.ones(3) / 3, 'same')  # 30 ms: a single quiet reading inside a word is not a pause

def quiet_spans(db, ratio=0.35, min_seconds=0.2):
    """[(start, end)] in seconds where the level stays under floor + ratio x (speech - floor), for min_seconds or more.
    The floor is the 10th percentile of the levels, the speech level the 90th."""
    import numpy as np
    if not len(db):
        return []
    floor, speech = np.percentile(db, [10, 90])
    if speech - floor < 6:  # no clear difference between speech and background: no reliable pause
        return []
    low = db < floor + ratio * (speech - floor)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], low.astype(np.int8), [0]))))
    return [(round(float(a) * HOP, 3), round(float(b) * HOP, 3)) for a, b in zip(edges[::2], edges[1::2]) if (b - a) * HOP >= min_seconds]

def tighten(words, spans, keep=0.02, near=0.5):
    """Moves the start and end of each word out of the quiet stretches it overlaps (keeping `keep` seconds of the
    quiet next to the word): a pause Whisper spread over a word becomes a gap between words again. A word placed
    entirely inside a pause goes to the end of the pause it is nearer, when speech is heard there within `near`
    seconds; with no speech on that side (the end of the clip), it is left where it is (invented, most likely).
    Returns the number of words moved."""
    moved, j = 0, 0
    for n, w in enumerate(words):
        a, b = w['start'], w['end']
        while j < len(spans) and spans[j][1] <= a:
            j += 1
        k = j
        while k < len(spans) and spans[k][0] < b:
            s0, s1 = spans[k]
            if s0 <= a and s1 >= b:  # the whole word sits in the pause
                after = words[n + 1]['start'] if n + 1 < len(words) else float('inf')
                before = words[n - 1]['end'] if n else float('-inf')
                late = s1 - b <= a - s0  # nearer the end of the pause
                if (max(after - s1, 0.0) if late else max(s0 - before, 0.0)) <= near:
                    length = min(b - a, 0.6)
                    if late:  # without running over the next word
                        a = s1 - keep
                        b = a + (min(length, after - a) if after - a >= 0.05 else 0.05)
                    else:  # without running over the word before
                        b = s0 + keep
                        a = b - (min(length, b - before) if b - before >= 0.05 else 0.05)
                break
            if s0 <= a < s1:  # quiet at the start of the word
                a = max(a, s1 - keep)
            elif s0 < b <= s1:  # quiet at the end of the word
                b = min(b, s0 + keep)
            elif a < s0 and s1 < b:  # a pause inside the word: the word sits on the longer side
                if s0 - a >= b - s1:
                    b = s0 + keep
                else:
                    a = s1 - keep
            k += 1
        if b - a >= 0.04 and (a, b) != (w['start'], w['end']):
            w['start'], w['end'] = round(a, 3), round(b, 3)
            moved += 1
    return moved

def main():
    parser = argparse.ArgumentParser(description='Prints the quiet stretches of a clip.')
    parser.add_argument('clip')
    parser.add_argument('--track', type=int, default=0)
    parser.add_argument('--ratio', type=float, default=0.35, help='threshold between noise floor (0) and speech (1)')
    parser.add_argument('--min', type=float, default=0.2, help='shortest pause, in seconds')
    args = parser.parse_args()
    spans = quiet_spans(levels(args.clip, args.track), args.ratio, args.min)
    for a, b in spans:
        print(f'{a:9.2f} {b:9.2f}  {b - a:5.2f} s')
    print(f'{len(spans)} pauses, {sum(b - a for a, b in spans):.1f} s')

if __name__ == '__main__':
    main()
