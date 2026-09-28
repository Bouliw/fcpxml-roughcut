#!/usr/bin/env python3
"""Turns the sentences to keep (time ranges read in the transcript) into the cuts of edl.json: only the words inside
each range are kept, the range is split at every pause, and each cut gets a margin that never reaches the
neighbouring words. Hesitations, words said again (words.py) and suspect words are left out.
Usage: edl_from_ranges.py ranges.json -o edl.json

ranges.json:
{"project": "Weekend vlog", "vertical": false, "file": "footage/C0001.MP4",
 "pause": 0.5, "pre": 0.08, "post": 0.12,
 "ranges": [{"from": 83.1, "to": 91.4, "chapter": "Hook"},
            {"file": "footage/C0002.MP4", "from": 3.0, "to": 12.9, "post": 0.03, "marker": "B-roll here"}]}
"file", "pause", "pre" and "post" can be set for the whole list or per range; "project", "vertical", "format" and
"markers" are copied to edl.json."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import transcript_path

UNTIMED_PRE = 0.25  # words whose timestamps Whisper lost: their start is a guess, so keep a wider margin
MIN_CUT = 0.6  # a cut shorter than this flashes by (one word between two pauses): it joins its neighbour...
JOIN_GAP = 0.8  # ...when the pause between them is no longer than this and holds no word left out
MIN_PIECE = 0.4  # a hesitation is left in when cutting it would leave a piece shorter than this: it breaks the sentence

def load_words(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)['words']
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f'Cannot read the transcript {path}: {e}')

def cuts(words, start, end, pause, pre, post, min_cut=MIN_CUT, min_piece=MIN_PIECE, hesitations=True):
    """[(in, out, [words])] for one range of the source, in seconds. `hesitations`: False keeps them in."""
    groups, cur = [], []
    for w in (w for w in words if w['start'] >= start - 0.01 and w['end'] <= end + 0.01):
        out = (w.get('filler') and hesitations) or w.get('suspect') or w.get('repeat')
        if out or (cur and w['start'] - cur[-1]['end'] > pause):
            if cur:
                groups.append(cur)
            cur = []
        if not out:
            cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        # the group's own extent (words with lost timestamps can come out of order)
        first, last, mine = min(w['start'] for w in g), max(w['end'] for w in g), {id(w) for w in g}
        before = max((w['end'] for w in words if w['end'] <= first and id(w) not in mine), default=0.0)
        after = min((w['start'] for w in words if w['start'] >= last and id(w) not in mine), default=float('inf'))
        lead = max(pre, UNTIMED_PRE) if g[0].get('untimed') else pre
        # a margin stops halfway to the next word said, so it never lets part of it in
        cin = max(first - lead, (before + first) / 2, 0.0)
        cout = min(last + post, (last + after) / 2)
        if cout - cin >= 0.04:  # nothing left of it: no cut
            out.append((round(cin, 3), round(cout, 3), g))
    return joined(out, words, min_cut, min_piece)

def joined(found, words, min_cut=MIN_CUT, min_piece=MIN_PIECE):
    """Cuts that overlap (words Whisper gave overlapping times) made one, so nothing plays twice; and a cut shorter
    than MIN_CUT joined to its neighbour across a short pause, unless a word left out (a hesitation, a word said
    again) is in that pause."""
    out = []
    for c in found:
        if out:
            p = out[-1]
            gap = c[0] - p[1]
            short = min(c[1] - c[0], p[1] - p[0]) < min_cut
            between = [w for w in words if p[1] <= w['start'] and w['end'] <= c[0]]
            left_out = any(w.get('filler') or w.get('suspect') or w.get('repeat') for w in between)
            # a hesitation cut out of a sentence, leaving a word or two alone between cuts: it stays, with its words
            breaks = (min(c[1] - c[0], p[1] - p[0]) < min_piece and gap <= JOIN_GAP and between
                      and all(w.get('filler') and not w.get('suspect') for w in between if not w.get('repeat')))
            if gap <= 0 or (short and gap <= JOIN_GAP and not left_out) or breaks:
                out[-1] = (p[0], max(p[1], c[1]), p[2] + c[2])
                continue
        out.append(c)
    return out

SNAP = 0.2  # a cut edge heard in the voice moves, by this much at most, to the quietest moment next to it

class Levels:
    """The level of a source's sound every 10 ms (pauses.py), read once; None without numpy or sound."""
    def __init__(self):
        self.cache = {}

    def __call__(self, path):
        if path not in self.cache:
            try:
                import numpy as np
                import pauses
                db = pauses.levels(path)
                floor, speech = np.percentile(db, [10, 90]) if len(db) else (0, 0)
                self.cache[path] = (db, floor + 0.5 * (speech - floor)) if speech - floor >= 6 else None
            except (ImportError, SystemExit):
                self.cache[path] = None
        return self.cache[path]

def snapped(found, words, sound):
    """Each cut edge that falls while the voice is loud (a syllable clipped, a breath cut in two) moved to the quietest
    moment within SNAP seconds outside the cut, never closer than 30 ms to the word before or after, and only when that
    moment is quiet. `sound`: (levels every 10 ms, level of speech) or None."""
    if not sound:
        return found
    db, loud = sound
    def level(t):
        i = int(t / 0.01)
        return db[max(0, i - 1):i + 2].max() if 0 <= i < len(db) else loud
    def quietest(a, b):
        i, j = max(0, int(a / 0.01)), min(len(db), int(b / 0.01))
        if j - i < 3:
            return None
        k = i + int(db[i:j].argmin())
        return round(k * 0.01, 3) if db[k] <= loud else None
    out = []
    for cin, cout, g in found:
        first, last, mine = min(w['start'] for w in g), max(w['end'] for w in g), {id(w) for w in g}
        before = max((w['end'] for w in words if w['end'] <= first and id(w) not in mine), default=0.0)
        after = min((w['start'] for w in words if w['start'] >= last and id(w) not in mine), default=float('inf'))
        if level(cin) > loud:
            t = quietest(max(cin - SNAP, before + 0.03), cin)
            cin = t if t is not None else cin
        if level(cout) > loud:
            t = quietest(cout, min(cout + SNAP, after - 0.03))
            cout = t + 0.01 if t is not None else cout
        out.append((cin, cout, g))
    return out

class Sounds:
    """The sound of each source, read closely (hesitations.py), once; None without numpy or sound."""
    def __init__(self):
        self.cache = {}

    def __call__(self, path):
        if path not in self.cache:
            try:
                import hesitations
                sound = hesitations.Sound(hesitations.read(path))
                self.cache[path] = sound if sound.n and sound.speech - sound.floor >= 6 else None
            except (ImportError, SystemExit):
                self.cache[path] = None
        return self.cache[path]

SPLICE_LEVEL = 13.6  # a cut out of a hesitation that touches the words: the level may jump by this much at most...
SPLICE_TIMBRE = 1.25  # ...and the sound change this much (9 in 10 of the joins between two words, said, do no more:
                      # measured at 2,425 of them on real footage)

def smooth(sound, a, b, level=SPLICE_LEVEL, timbre=SPLICE_TIMBRE):
    """Whether a cut from `a` to `b` seconds sounds like two words said one after the other at its join."""
    jump, change = sound.join(a, b)
    return jump <= level and change <= timbre

def splices(found, words, sound, mode, level=SPLICE_LEVEL, timbre=SPLICE_TIMBRE):
    """The cuts that take a hesitation out of a passage, checked: one in true silences on both sides stays; one
    touching the words (cut in the quietest moment next to it) stays only with `mode` "all" and when the join sounds
    like two words said one after the other (the level and the sound, just before and just after, no further apart
    than between two words said); else the hesitation is left in. Returns the cuts, and the indices of the ones that
    start on such a join (verify_edit.py listens to them)."""
    out, dips = [found[0]] if found else [], set()
    for c in found[1:]:
        p = out[-1]
        between = [w for w in words if p[1] - 0.05 <= w['start'] and w['end'] <= c[0] + 0.05]
        if not any(w.get('filler') and not w.get('suspect') for w in between):
            out.append(c)
            continue
        if sound.quiet_join(p[1], c[0]):
            out.append(c)
            continue
        if mode == 'all' and smooth(sound, p[1], c[0], level, timbre):
            dips.add(len(out))
            out.append(c)
        else:  # the hesitation stays: the two cuts are one again
            out[-1] = (p[0], c[1], p[2] + c[2])
    return out, dips

REACH = 0.35  # how far past a word Whisper timed a cut edge may move to find the silence
ROOM = 0.04  # of the silence kept next to the word, so the cut never falls on its first or last sound

def on_silence(found, words, sound, reach=None, room=None):
    """Each cut edge put in a true silence heard in the sound, next to the word it starts or ends on: the level near
    the noise floor, and no hiss above 2 kHz (an "s", an "f"). Whisper's timestamps are only a guide: a word often
    starts a little before them, or ends after. Never past the word or the hesitation before or after (a cut that
    started on an "euh"); where no silence is heard, the quietest moment there. `sound`: hesitations.Sound."""
    reach = REACH if reach is None else reach
    room = ROOM if room is None else room
    out = []
    for cin, cout, g in found:
        first, last, mine = min(w['start'] for w in g), max(w['end'] for w in g), {id(w) for w in g}
        before = max((w['end'] for w in words if w['end'] <= first + 0.02 and id(w) not in mine), default=0.0)
        after = min((w['start'] for w in words if w['start'] >= last - 0.02 and id(w) not in mine), default=float('inf'))
        # the start: the last silence before the word; or one just after Whisper's time, when nothing is heard
        # between (Whisper starts a word early, at 0.0 for a clip whose speech starts later)
        a, b = max(before + 0.02, first - reach), first + 0.3
        quiet = [q for q in sound.quiet_between(a, b)
                 if q[1] <= first + 0.05 or (first < q[0] <= first + 0.15 and not sound.loud_between(first, q[0]))]
        if quiet:
            s0, s1 = quiet[-1]
            cin = max(s0, s1 - room)
        elif first - a > 0.03:
            cin = sound.quietest(a, first)
        # the end: the first silence after the word; or one just before Whisper's time, when nothing is heard between
        a, b = last - 0.3, min(after - 0.02, last + reach)
        quiet = [q for q in sound.quiet_between(max(a, cin + 0.05), b)
                 if q[0] >= last - 0.05 or (last - 0.15 <= q[1] < last and not sound.loud_between(q[1], last))]
        if quiet:
            s0, s1 = quiet[0]
            cout = min(s1, s0 + room)
        elif b - last > 0.03:
            cout = sound.quietest(last, b)
        if cout - cin >= 0.04:
            out.append((round(max(cin, 0.0), 3), round(cout, 3), g))
    return out

def main():
    parser = argparse.ArgumentParser(description='Turns the sentences to keep into the cuts of edl.json.')
    parser.add_argument('ranges', help='ranges.json')
    parser.add_argument('-o', required=True, dest='out', help='edl.json to write')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ next to ranges.json)')
    args = parser.parse_args()
    try:
        with open(args.ranges, encoding='utf-8') as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f'Cannot read {args.ranges}: {e}')
    if not spec.get('ranges'):
        raise SystemExit(f'No ranges in {args.ranges}')
    base = os.path.dirname(os.path.abspath(args.ranges))
    tdir = args.transcripts or os.path.join(base, 'transcripts')
    same_folder = os.path.dirname(os.path.abspath(args.out)) == base
    edl = {k: spec[k] for k in ('project', 'vertical', 'format', 'markers') if k in spec}
    edl['clips'], cache, total, levels, sounds = [], {}, 0.0, Levels(), Sounds()
    mode = spec.get('hesitations', 'all')
    mode = {True: 'all', False: 'none'}.get(mode, mode)  # true / false, as written before there were three
    for r in spec['ranges']:
        src = r.get('file', spec.get('file'))
        if not src or 'from' not in r or 'to' not in r:
            raise SystemExit(f'Each range needs "from" and "to" in seconds, and a "file" (its own or the list\'s): {r}')
        if src not in cache:
            cache[src] = load_words(transcript_path(tdir, src))
        get = lambda k, d: r.get(k, spec.get(k, d))  # noqa: E731
        dips = set()
        if r.get('whole'):  # a moment kept as it is (an IRL moment, with its own sound): no words to cut around
            found = [(float(r['from']), float(r['to']), [])]
        else:
            found = cuts(cache[src], float(r['from']), float(r['to']), get('pause', 0.5), get('pre', 0.08), get('post', 0.12),
                         get('min_cut', MIN_CUT), get('min_piece', MIN_PIECE), hesitations=mode != 'none')
        if spec.get('snap', True) and not r.get('whole'):
            path = os.path.abspath(os.path.join(base, os.path.expanduser(src)))
            sound = sounds(path) if spec.get('silence', True) else None
            found = (joined(on_silence(found, cache[src], sound, get('reach', REACH), get('room', ROOM)), cache[src],
                            get('min_cut', MIN_CUT), get('min_piece', MIN_PIECE))
                     if sound else snapped(found, cache[src], levels(path)))
            if sound and mode != 'none':
                found, dips = splices(found, cache[src], sound, mode, get('splice_level', SPLICE_LEVEL), get('splice_timbre', SPLICE_TIMBRE))
        if not found:
            print(f"WARNING: no word said between {r['from']} and {r['to']} s in {src}, range left out")
        # paths stay as written when edl.json sits next to ranges.json, and become absolute otherwise
        file = src if same_folder or os.path.isabs(os.path.expanduser(src)) else os.path.abspath(os.path.join(base, src))
        for i, (cin, cout, g) in enumerate(found):
            clip = {'file': file, 'in': cin, 'out': cout}
            if i == 0:
                clip.update({k: r[k] for k in ('chapter', 'marker') if k in r})
            if r.get('note'):  # every piece of it: all of the hook is the hook
                clip['note'] = r['note']
            if r.get('role'):
                clip['role'] = r['role']
            if i in dips:  # starts on a join where a hesitation touching the words was cut: listened to afterwards
                clip['splice'] = 'dip'
            edl['clips'].append(clip)
            total += cout - cin
            print(f"{cin:9.2f} {cout:9.2f}  {cout - cin:5.2f} s  " + (' '.join(w['w'] for w in g) or r.get('marker', '(as it is)')))
    if not edl['clips']:
        raise SystemExit('No cut: none of the ranges contains a word from the transcript')
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(edl, f, ensure_ascii=False, indent=1)
    m, s = divmod(total, 60)
    print(f'{args.out}: {len(edl["clips"])} cuts, about {int(m)}:{s:05.2f} before frame snapping')

if __name__ == '__main__':
    main()
