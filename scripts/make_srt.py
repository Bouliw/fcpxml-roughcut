#!/usr/bin/env python3
"""SRT subtitles re-timed to the edl.json timeline, from the transcripts (<clip>.words.json).
Usage: make_srt.py edl.json -o project.srt [--transcripts folder] [--words N] [--chars 42]"""
import argparse, sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unicodedata
from timeline import build, transcript_path
from words import with_corrections

def ts(t):
    t = max(0, t)
    h, r = divmod(int(round(t * 1000)), 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def timeline_words(clips, tdir, source=False):
    """[(start, end, word)] on the edited timeline, from the transcripts with their corrections: hesitations and
    invented words left out. With source=True, also the clip's name and where the word starts in it."""
    # Transcripts are named after the clip: two clips with the same name would share one
    sources = {}
    for c in clips:
        sources.setdefault(unicodedata.normalize('NFC', os.path.splitext(os.path.basename(c['file']))[0]), set()).add(c['file'])
    for base, paths in sources.items():
        if len(paths) > 1:
            raise SystemExit(f'These clips share the name {base}, so their transcripts would overwrite each other: '
                             f'{", ".join(sorted(paths))}. Rename one of them, then transcribe it again.')
    cache, words = {}, []
    for c in clips:
        base = os.path.splitext(os.path.basename(c['file']))[0]
        if base not in cache:
            p = transcript_path(tdir, c['file'])
            cache[base] = None
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    cache[base] = with_corrections(p, json.load(f)['words'])
            else:
                print(f'WARNING: no transcript for {base} ({p})')
        for w in cache[base] or []:
            if w.get('filler') or w.get('suspect'):  # hesitations, and text Whisper invented
                continue
            if w['start'] >= c['in_s'] - 0.02 and w['end'] <= c['in_s'] + c['dur_s'] + 0.02:
                t0 = c['off_s'] + (w['start'] - c['in_s'])
                t1 = min(c['off_s'] + c['dur_s'], c['off_s'] + (w['end'] - c['in_s']))
                words.append((max(t0, c['off_s']), t1, w['w']) + ((base, w['start']) if source else ()))
    return words

def edit_words(edl_path, clips, tdir, source=False):
    """The words of an edit, timed on its own sound when verify_edit.py listened to it (final-words.json, for these
    very cuts), else as read in the clips' transcripts (timeline_words). Either way with the corrections made since."""
    folder = os.path.dirname(os.path.abspath(edl_path))
    try:
        with open(os.path.join(folder, 'final-words.json'), encoding='utf-8') as f:
            final = json.load(f)
        with open(edl_path, encoding='utf-8') as f:
            edl = json.load(f)
    except (OSError, ValueError):
        return timeline_words(clips, tdir, source)
    from verify_edit import fingerprint
    if final.get('edl') != fingerprint(edl):
        return timeline_words(clips, tdir, source)
    current = {}  # the words of each clip as they read now: {clip: {start: [words starting then, in order]}}
    seen = {}  # Whisper gives several words one start at times: the n-th word of the edit at that start is the n-th
    out = []
    for w in final['words']:
        start, end, _, base, src = w[:5]
        if base not in current:
            p = os.path.join(tdir, base + '.words.json')
            current[base] = {}
            try:
                with open(p, encoding='utf-8') as f:
                    for x in with_corrections(p, json.load(f)['words'], keep_removed=True):
                        current[base].setdefault(f'{x["start"]:.2f}', []).append(x)
            except (OSError, ValueError):
                pass
        key = (base, f'{src:.2f}')
        n = seen[key] = seen.get(key, -1) + 1
        same = [x for x in current[base].get(key[1], []) if not (x.get('filler') or x.get('suspect'))]
        word = same[n]['w'] if n < len(same) else None
        if word:  # a word taken out by a correction is gone
            out.append((start, end, word) + ((base, src) if source else ()))
    return out

def split_cues(words, max_words=0, max_chars=42):
    """Groups words into subtitles: at most max_words words (or max_chars characters), never across a pause or a sentence end."""
    cues, cur = [], []
    for item in words:  # (start, end, word, ...)
        t0, t1, w = item[:3]
        if cur:
            text = ' '.join(x[2] for x in cur)
            gap = t0 - cur[-1][1]
            full = (max_words and len(cur) >= max_words) or (not max_words and len(text) + 1 + len(w) > max_chars)
            if gap > 0.6 or full or (t1 - cur[0][0] > 5):
                cues.append(cur); cur = []
        cur.append(item)
        if re.search(r'[.!?…]$', w):
            cues.append(cur); cur = []
    if cur:
        cues.append(cur)
    return cues

SHORTEST = 0.3  # a subtitle timed on the start of the next one gets this long, from the time before it

def cue_times(cues):
    """(start, end) of each subtitle: at least 0.5 s on screen, but never over the next one. One whose words were
    timed on the next one's start (not heard in the edit, placed between two that were) starts a little earlier,
    in the time before it; with no time before it, it has none (end on its start: srt() joins it to the one before)."""
    starts = [cue[0][0] for cue in cues]
    for n in range(len(starts) - 2, -1, -1):
        if starts[n] > starts[n + 1] - SHORTEST:
            before = starts[n - 1] + 0.05 if n else 0.0
            starts[n] = min(starts[n], max(starts[n + 1] - SHORTEST, before))
    out = []
    for n, cue in enumerate(cues, 1):
        start = starts[n - 1]
        end = max(cue[-1][1] + 0.15, start + 0.5)
        if n < len(cues):
            nxt = starts[n]
            end = min(end, nxt - 0.02) if nxt - 0.02 > start else nxt
        out.append((start, end))
    return out

def srt(cues, texts=None):
    """The SRT file of these subtitles, with their own words or the `texts` given (a translation). A subtitle with
    no time of its own is joined to the one before it."""
    shown = []  # [start, end, text]
    for n, (cue, (start, end)) in enumerate(zip(cues, cue_times(cues)), 1):
        text = texts[n - 1] if texts else ' '.join(x[2] for x in cue)
        if end - start < 0.05 and shown:
            shown[-1][2] += ' ' + text
            shown[-1][1] = max(shown[-1][1], end)
        else:
            shown.append([start, end, text])
    lines = []
    for n, (start, end, text) in enumerate(shown, 1):
        lines += [str(n), f'{ts(start)} --> {ts(end)}', text, '']
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(description='SRT subtitles re-timed to the edl.json timeline.')
    parser.add_argument('edl', help='edl.json')
    parser.add_argument('-o', required=True, dest='out', help='SRT file to write')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ next to edl.json)')
    parser.add_argument('--words', type=int, default=0, help='at most N words per subtitle (default: split by characters)')
    parser.add_argument('--chars', type=int, default=42, help='at most N characters per subtitle (default 42)')
    args = parser.parse_args()
    out, max_words, max_chars = args.out, args.words, args.chars
    tdir = args.transcripts or os.path.join(os.path.dirname(os.path.abspath(args.edl)), 'transcripts')
    _, _, _, clips = build(args.edl)
    words = edit_words(args.edl, clips, tdir)
    cues = split_cues(words, max_words, max_chars)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(srt(cues))
    print(f'{out}: {len(cues)} subtitles, {len(words)} words')

if __name__ == '__main__':
    main()
