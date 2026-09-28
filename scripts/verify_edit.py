#!/usr/bin/env python3
"""Listens to an edit once it is assembled, and fixes what is heard wrong: the sound of the edit, as Final Cut Pro
plays it, is transcribed again (Whisper started on hesitations, and the held vowels found in the sound), then set
against what was to be kept:
- a hesitation still heard ("euh") is cut out, unless that leaves a piece too short to stand (it breaks the sentence);
- a word to keep that is not heard, at the edge of a cut (its start or end eaten), gets the cut's edge moved to the
  silence past it;
- a scrap of a word that was cut, heard at the edge of a cut, gets the edge moved to the silence before it.
Then again, until nothing is left to fix (at most --rounds times). Last, every word kept is timed on the sound of the
edit itself (final-words.json next to edl.json): the captions and the subtitles follow it, not the times read in the
clips, which drift at the cuts.
With --hesitations all, a hesitation still heard is cut, and every join where one touching the words was cut is
checked: the words on either side heard whole, else the hesitation goes back. Otherwise they are listed, not cut. verify.json, next to edl.json, holds what was
heard and done, and the moments to listen to.
Usage: verify_edit.py edl.json [--hesitations none|clean|all] [--rounds 3] [--time-only] [--transcripts dir] [--language fr]"""
import argparse, difflib, hashlib, json, os, re, shutil, subprocess, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline
from make_srt import timeline_words
from edl_from_ranges import MIN_PIECE, SPLICE_LEVEL, SPLICE_TIMBRE, smooth
import hesitations

HERE = os.path.dirname(os.path.abspath(__file__))
EDGE = 0.3  # a word this near the edge of a cut is taken as touched by the cut
FILLERS = {'euh', 'heu', 'euhh', 'hum', 'hmm', 'mmh', 'uh', 'um', 'erm'}

def fingerprint(edl):
    """What the words of an edit depend on: its cuts."""
    return hashlib.sha1(json.dumps([(c['file'], c['in'], c['out']) for c in edl['clips']]).encode()).hexdigest()

def norm(word):
    return re.sub(r"[^\w']", '', word.lower()).strip("'")

def render(clips, total, sources, out):
    """The dialogue of the edit, 16 kHz mono, as Final Cut Pro plays it (the moments played for their own sound left
    silent: their voices are not what was chosen)."""
    import numpy as np
    buf = np.zeros(int(round(total * hesitations.RATE)) + 1, np.float32)
    for c in clips:
        if c.get('role') == 'effects' or not c['asset'].get('audio_channels'):
            continue
        if c['file'] not in sources:
            sources[c['file']] = hesitations.read(c['file'])
        x = sources[c['file']]
        a, n = int(round(c['in_s'] * hesitations.RATE)), int(round(c['dur_s'] * hesitations.RATE))
        at = int(round(c['off_s'] * hesitations.RATE))
        piece = x[a:a + n]
        buf[at:at + len(piece)] = piece[:len(buf) - at]
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hesitations.RATE)
        w.writeframes((np.clip(buf, -1, 1) * 32767).astype(np.int16).tobytes())

def transcribe(wav, folder, language):
    """The words heard in the sound of the edit, hesitations included (transcribe.sh, then words.py)."""
    env = dict(os.environ, ROUGHCUT_HESITATIONS='1')
    r = subprocess.run(['bash', os.path.join(HERE, 'transcribe.sh'), wav, folder, language], capture_output=True, text=True, env=env)
    if r.returncode:
        raise SystemExit(f'Cannot transcribe the edit: {r.stderr.strip()[-300:]}')
    whisper_json = os.path.join(folder, os.path.splitext(os.path.basename(wav))[0] + '.json')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'words.py'), whisper_json, '--audio', wav, '--hesitations'],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'Cannot read the words of the edit: {r.stderr.strip()[-300:]}')
    with open(whisper_json[:-5] + '.words.json', encoding='utf-8') as f:
        return json.load(f)['words']

def clip_at(clips, t):
    for k, c in enumerate(clips):
        if c['off_s'] <= t < c['off_s'] + c['dur_s']:
            return k
    return None

def compare(expected, heard):
    """What is heard that should not be, and what should be that is not: (hesitations heard, words missing, scraps
    heard), each a list of timeline spans (start, end, word, ...), the missing ones with their clip and their start in
    it. `expected`: [(start, end, word, clip, start in clip)], `heard`: words."""
    said = [w for w in heard if not (w.get('filler') or w.get('suspect'))]
    hes = [(w['start'], w['end'], w['w']) for w in heard if w.get('filler') and not w.get('suspect')
           and (w.get('sound') or norm(w['w']) in FILLERS)]
    a, b = [norm(w[2]) for w in expected], [norm(w['w']) for w in said]
    missing, scraps = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ('delete', 'replace'):
            missing += [tuple(w) for w in expected[i1:i2]]
        if tag in ('insert', 'replace'):
            scraps += [(w['start'], w['end'], w['w']) for w in said[j1:j2]]
    return hes, missing, scraps

def timed(expected, heard, clips=None):
    """The words kept, timed on the sound of the edit: a word heard takes the times it is heard at, when that is
    believable (inside its own cut, and less than a second from where the clip's transcript puts it: a common word
    can be matched to the same word said elsewhere); one not heard, or not believable, is moved like its neighbours
    that are."""
    said = [w for w in heard if not (w.get('filler') or w.get('suspect'))]
    a, b = [norm(w[2]) for w in expected], [norm(w['w']) for w in said]
    times = [None] * len(expected)
    for m in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks():
        for k in range(m.size):
            w, h = expected[m.a + k], said[m.b + k]
            c = clip_at(clips, w[0]) if clips else None
            inside = c is None or clips[c]['off_s'] - 0.05 <= h['start'] <= clips[c]['off_s'] + clips[c]['dur_s'] + 0.05
            if inside and abs(h['start'] - w[0]) < 1.0:
                times[m.a + k] = (h['start'], h['end'])
    out = []
    for k, w in enumerate(expected):
        if times[k] is None:
            prev = next((j for j in range(k - 1, -1, -1) if times[j]), None)
            nxt = next((j for j in range(k + 1, len(times)) if times[j]), None)
            shift = [times[j][0] - expected[j][0] for j in (prev, nxt) if j is not None]
            d = sum(shift) / len(shift) if shift else 0.0
            c = clip_at(clips, w[0]) if clips else None
            if c is not None:  # never out of its own cut
                lo, hi = clips[c]['off_s'], clips[c]['off_s'] + clips[c]['dur_s']
                d = min(max(d, lo - w[0]), hi - w[1])
            times[k] = (w[0] + d, w[1] + d)
        out.append([round(times[k][0], 3), round(max(times[k][1], times[k][0] + 0.05), 3)] + list(w[2:]))
    for k in range(1, len(out)):  # in order, never overlapping
        out[k][0] = max(out[k][0], out[k - 1][0])
    return out

def moments(clips, words_of, expected, sounds=None):
    """The moments of the edit worth a listen: where a hesitation was cut out (a gap between two pieces of one take
    holding a hesitation and no word said: 'dip' when the hesitation touched the words and the cut is in the quietest
    moment next to it, its join checked; else 'cut', in silences) and where one was left in. [{'at', 'kind', 'text'}],
    `at` in seconds of the edit. `sounds` is not used any more (kept for callers)."""
    def around(t):
        near = [w[2] for w in expected if t - 2.0 <= w[0] <= t + 2.0]
        return ' '.join(near)[:90]
    out = []
    for k, c in enumerate(clips):
        if c.get('role') == 'effects':
            continue
        words = words_of(c['file'])
        heard = [w for w in words if w.get('filler') and not w.get('suspect')]
        for w in heard:  # left in: inside what the cut plays
            if c['in_s'] + 0.05 <= w['start'] and w['end'] <= c['in_s'] + c['dur_s'] - 0.05:
                t = c['off_s'] + w['start'] - c['in_s']
                out.append({'at': round(t, 2), 'kind': 'kept', 'text': around(t)})
        if k + 1 < len(clips) and clips[k + 1]['file'] == c['file']:
            gap = c['in_s'] + c['dur_s'], clips[k + 1]['in_s']
            inside = [w for w in words if gap[0] - 0.05 <= w['start'] and w['end'] <= gap[1] + 0.05]
            if (gap[1] > gap[0] and any(w in heard for w in inside)
                    and not any(not (w.get('filler') or w.get('suspect') or w.get('repeat')) for w in inside)):
                t = clips[k + 1]['off_s']
                out.append({'at': round(t, 2), 'kind': 'dip' if clips[k + 1].get('splice') == 'dip' else 'cut', 'text': around(t)})
    return out

def fragment(word, clips, s, e, words_of):
    """A scrap of a word cut out, heard at the edge of a cut: the start or the end of a word of the clip said just past
    that edge (a whole word heard there is said there: kept)."""
    bare = norm(word)
    if not bare:
        return False
    k = clip_at(clips, (s + e) / 2)
    if k is None:
        return False
    c = clips[k]
    src = c['in_s'] + (s - c['off_s'])
    for w in words_of(c['file']):
        other = norm(w['w'])
        if abs(w['start'] - src) < 0.6 and other != bare and (other.startswith(bare) or other.endswith(bare)):
            return True
    return False

EDGE_WORD = 0.12  # a word to keep starting or ending this near the edge of its cut may have been cut into
JOIN_WINDOW = 1.5  # the words on either side of a join where a hesitation was cut, heard again: this far each way
JOIN_MATCH = 0.75  # at least this share of them heard as they should be, the two touching the join among them

def joins_heard(edl, clips, expected, heard):
    """The joins where a hesitation touching the words was cut (edl_from_ranges.py marks the clip after them
    'splice': 'dip'), each checked in the edit's own sound: the words on either side are heard whole. Returns the
    indices of the clips whose join failed (the hesitation goes back)."""
    said = [w for w in heard if not (w.get('filler') or w.get('suspect'))]
    failed = []
    for k, c in enumerate(edl['clips']):
        if c.get('splice') != 'dip' or k == 0 or k >= len(clips):
            continue
        t = clips[k]['off_s']
        exp = [w for w in expected if t - JOIN_WINDOW <= w[0] < t + JOIN_WINDOW]
        got = [w for w in said if t - JOIN_WINDOW - 0.3 <= w['start'] < t + JOIN_WINDOW + 0.3]
        if not exp:
            continue
        a, b = [norm(w[2]) for w in exp], [norm(w['w']) for w in got]
        share = difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()
        touching = [norm(w[2]) for w in exp if w[0] < t][-1:] + [norm(w[2]) for w in exp if w[0] >= t][:1]
        whole = all(any(x == y or (len(x) > 3 and y.startswith(x[:4])) for y in b) for x in touching)
        if share < JOIN_MATCH or not whole:
            failed.append(k)
    return failed

def fixes(clips, hes, missing, scraps, sounds, words_of):
    """The changes to the cuts: [(clip index, kind, source start, source end, why)] with kind 'cut' (take out that
    stretch of the clip), 'extend-in', 'extend-out', 'trim-in', 'trim-out' (move an edge to that time). Whisper's times
    in the sound of the edit are loose: a hesitation is only cut where the clip's own sound holds a vowel there,
    clear of every word to keep; a word is only taken as cut when it touches the edge of its cut."""
    out = []
    for s, e, w in hes:
        k = clip_at(clips, (s + e) / 2)
        if k is None or clips[k].get('role') == 'effects':
            continue
        c = clips[k]
        at = c['in_s'] + ((s + e) / 2 - c['off_s'])
        sound = sounds(c['file'])
        if sound is None:
            continue
        keep = [x for x in words_of(c['file']) if not (x.get('filler') or x.get('suspect'))
                and x['end'] > c['in_s'] and x['start'] < c['in_s'] + c['dur_s']]
        vowels = [v for v in sound.held_vowels() if abs((v[0] + v[1]) / 2 - at) < 0.4 and c['in_s'] < v[0] and
                  v[1] < c['in_s'] + c['dur_s'] and hesitations.clear_of(v, keep)]
        if vowels:
            v = min(vowels, key=lambda v: abs((v[0] + v[1]) / 2 - at))
            out.append((k, 'cut', v[0], v[1], f'"{w}"'))
    for w in missing:
        if len(w) < 5:
            continue
        k = next((j for j, c in enumerate(clips) if c.get('role') != 'effects' and
                  os.path.splitext(os.path.basename(c['file']))[0] == w[3] and c['in_s'] - 0.05 <= w[4] <= c['in_s'] + c['dur_s']), None)
        if k is None:
            continue
        c = clips[k]
        src_start, src_end = w[4], w[4] + (w[1] - w[0])
        if src_start - c['in_s'] < EDGE_WORD:
            out.append((k, 'extend-in', src_start, src_end, f'"{w[2]}"'))
        elif c['in_s'] + c['dur_s'] - src_end < EDGE_WORD:
            out.append((k, 'extend-out', src_start, src_end, f'"{w[2]}"'))
    for s, e, w in scraps:
        k = clip_at(clips, (s + e) / 2)
        if k is None or clips[k].get('role') == 'effects' or not fragment(w, clips, s, e, words_of):
            continue
        c = clips[k]
        if s - c['off_s'] < EDGE:
            out.append((k, 'trim-in', c['in_s'] + (s - c['off_s']), c['in_s'] + (e - c['off_s']), f'"{w}"'))
        elif c['off_s'] + c['dur_s'] - e < EDGE:
            out.append((k, 'trim-out', c['in_s'] + (s - c['off_s']), c['in_s'] + (e - c['off_s']), f'"{w}"'))
    return out

def apply(edl, clips, todo, sounds, words_of, keep=None, tried=None, level=SPLICE_LEVEL, timbre=SPLICE_TIMBRE):
    """The changes made to edl.json's cuts; returns what was done and what was left as it is, and why. `keep`: {clip
    index: (source start of its first word to keep, source end of its last)}: an edge never moves past them. A
    hesitation cut out of a cut must fall in silences, or its join sound like two words said (`level`, `timbre`: see
    edl_from_ranges.smooth)."""
    done, left = [], []
    keep = keep or {}
    tried = set() if tried is None else tried  # a fix already made once is not made again: no going round in circles
    todo = [f for f in todo if (f[1], clips[f[0]]['file'], round(f[2], 1)) not in tried]
    tried.update((f[1], clips[f[0]]['file'], round(f[2], 1)) for f in todo)
    cuts = edl['clips']
    by_clip = {}
    for f in todo:
        by_clip.setdefault(f[0], []).append(f)
    rejoin = {f[0] for f in todo if f[1] == 'rejoin'}
    todo = [f for f in todo if f[1] != 'rejoin']
    new = []
    for k, c in enumerate(cuts):
        if k in rejoin and new and new[-1]['file'] == c['file']:  # the hesitation goes back: one cut again
            new[-1]['out'] = c['out']
            done.append((k, 'rejoin', ''))
            continue
        pieces = [dict(c)]
        prev_out = cuts[k - 1]['out'] if k and cuts[k - 1]['file'] == c['file'] and cuts[k - 1]['out'] <= c['in'] else 0.0
        next_in = cuts[k + 1]['in'] if k + 1 < len(cuts) and cuts[k + 1]['file'] == c['file'] and cuts[k + 1]['in'] >= c['out'] else float('inf')
        for _, kind, a, b, why in sorted(by_clip.get(k, []), key=lambda f: f[2]):
            p = pieces[-1]
            sound = sounds(c['file'])
            file_words = words_of(c['file'])
            if kind == 'cut':
                left_end = end_before(sound, a, p['in'])
                right_start = start_after(sound, b, p['out'])
                if left_end - p['in'] < MIN_PIECE and p['out'] - right_start < MIN_PIECE:
                    done.append((k, 'cut', why))  # the whole cut is the hesitation: it drops out of the edit
                    pieces.pop()
                    continue
                if left_end - p['in'] < MIN_PIECE:
                    p['in'] = right_start
                elif p['out'] - right_start < MIN_PIECE:
                    p['out'] = left_end
                elif a - p['in'] < MIN_PIECE or p['out'] - b < MIN_PIECE:
                    left.append((k, why, 'cutting it would leave a word alone'))
                    continue
                elif sound.quiet_join(left_end, right_start) or smooth(sound, left_end, right_start, level, timbre):
                    second = {kk: v for kk, v in p.items() if kk not in ('chapter', 'marker', 'splice')}
                    p['out'], second['in'] = left_end, right_start
                    if not sound.quiet_join(left_end, right_start):
                        second['splice'] = 'dip'  # its join is listened to next time, like the others
                    pieces.append(second)
                else:
                    left.append((k, why, 'its join would jump in level or sound'))
                    continue
                done.append((k, 'cut', why))
            elif kind.startswith('extend'):
                was = (p['in'], p['out'])
                # never past the word said before or after it (a hesitation included): only the word is put back
                before = max((w['end'] for w in file_words if w['end'] <= a + 0.02 and w['start'] < a - 0.02), default=0.0)
                after = min((w['start'] for w in file_words if w['start'] >= b - 0.02 and w['end'] > b + 0.02), default=float('inf'))
                if kind == 'extend-in':
                    p['in'] = min(p['in'], start_before(sound, a, max(a - 0.5, prev_out, before)))
                else:
                    p['out'] = max(p['out'], end_after(sound, b, min(b + 0.5, next_in, after)))
                if abs(p['in'] - was[0]) + abs(p['out'] - was[1]) >= 0.02:
                    done.append((k, kind, why))
            else:
                first, last = keep.get(k, (float('inf'), float('-inf')))
                was = (p['in'], p['out'])
                if kind == 'trim-in':
                    p['in'] = max(p['in'], min(start_after(sound, b, b + 0.3), first - 0.02))
                else:
                    p['out'] = min(p['out'], max(end_before(sound, a, a - 0.3), last + 0.02))
                if abs(p['in'] - was[0]) + abs(p['out'] - was[1]) >= 0.02:
                    done.append((k, kind, why))
        new += [p for p in pieces if p['out'] - p['in'] >= 0.1]
    # an edge moved never plays a stretch twice: two cuts of one clip in a row do not overlap
    for i in range(1, len(new)):
        if new[i]['file'] == new[i - 1]['file'] and new[i]['in'] < new[i - 1]['out'] and new[i]['in'] > new[i - 1]['in']:
            new[i]['in'] = new[i - 1]['out']
    edl['clips'] = [dict(c, **{'in': round(c['in'], 3), 'out': round(c['out'], 3)}) for c in new if c['out'] - c['in'] >= 0.1]
    return done, left

ROOM = 0.04  # of silence kept next to the speech at a cut

def _quiet(sound, a, b):
    return sound.quiet_between(a, b) if sound is not None else []

def end_before(sound, t, limit):
    """Where a piece ends that must stop before time t: just into the last silence before t (the speech before it
    kept whole); at worst the quietest moment just before t. Never before `limit`."""
    q = _quiet(sound, max(limit, t - 0.3), t + 0.02)
    if q:
        return max(limit, min(t, q[-1][0] + ROOM))
    return max(limit, sound.quietest(max(limit, t - 0.15), t)) if sound is not None else t

def start_after(sound, t, limit):
    """Where a piece starts that must begin after time t: at the end of the first silence after t, just before the
    speech that follows. Never after `limit`."""
    q = _quiet(sound, t - 0.02, min(limit, t + 0.3))
    if q:
        return min(limit, max(t, q[0][1] - ROOM))
    return min(limit, sound.quietest(t, min(limit, t + 0.15))) if sound is not None else t

def start_before(sound, t, limit):
    """Where a piece starts so that a word at time t is whole: in the last silence before it. Never before `limit`."""
    q = [x for x in _quiet(sound, max(limit, t - 0.4), t + 0.02) if x[0] <= t]
    return max(limit, min(t, max(q[-1][0], q[-1][1] - ROOM))) if q else max(limit, t - 0.08)

def end_after(sound, t, limit):
    """Where a piece ends so that a word ending at time t is whole: in the first silence after it. Never after `limit`."""
    q = [x for x in _quiet(sound, t - 0.02, min(limit, t + 0.4)) if x[1] >= t]
    return min(limit, max(t, min(q[0][1], q[0][0] + ROOM))) if q else min(limit, t + 0.12)

def main():
    parser = argparse.ArgumentParser(description='Listens to an assembled edit and fixes the hesitations and the cut words heard.')
    parser.add_argument('edl', help='edl.json (changed in place)')
    parser.add_argument('--rounds', type=int, default=3, help='at most this many listenings (default 3)')
    parser.add_argument('--transcripts', help='folder of the clips\' .words.json (default: transcripts/ next to edl.json)')
    parser.add_argument('--language', default='auto', help='main language of the speech (default: auto)')
    parser.add_argument('--hesitations', nargs='?', const='all', default='none', choices=('none', 'clean', 'all'),
                        help='the hesitations still heard: all (cut, checked like the others), clean or none (listed only)')
    parser.add_argument('--time-only', action='store_true', help='listen once and time the words on the sound, the cuts left as they are')
    parser.add_argument('--splice-level', type=float, default=SPLICE_LEVEL, help=f'a hesitation cut where it touches the words: largest jump of level at its join, dB (default {SPLICE_LEVEL})')
    parser.add_argument('--splice-timbre', type=float, default=SPLICE_TIMBRE, help=f'...and of sound (default {SPLICE_TIMBRE})')
    args = parser.parse_args()
    if args.time_only:
        args.rounds = 1
    folder = os.path.dirname(os.path.abspath(args.edl))
    tdir = args.transcripts or os.path.join(folder, 'transcripts')
    sources, sound_cache, word_cache = {}, {}, {}

    def sounds(path):
        if path not in sound_cache:
            if path not in sources:
                sources[path] = hesitations.read(path)
            s = hesitations.Sound(sources[path])
            sound_cache[path] = s if s.n and s.speech - s.floor >= 6 else None
        return sound_cache[path]

    def words_of(path):
        if path not in word_cache:
            try:
                with open(timeline.transcript_path(tdir, path), encoding='utf-8') as f:
                    word_cache[path] = json.load(f)['words']
            except (OSError, ValueError):
                word_cache[path] = []
        return word_cache[path]

    report = {'rounds': []}
    tried = set()
    best = None  # (problems heard, edl, words timed, moments, clips, joins that failed) of the best listening so far
    retime = False
    work = tempfile.mkdtemp(prefix='verify-')
    try:
        for n in range(1, args.rounds + 2):
            # after the joins put back at the last listening, or with the better cuts back: once more, only to time the words
            timing = n > args.rounds or retime
            with open(args.edl, encoding='utf-8') as f:
                edl = json.load(f)
            _, tl, _, clips = timeline.build(args.edl)
            total = float(tl['total_f'] * timeline.fd(tl['fps']))
            wav = os.path.join(work, f'edit-{n}.wav')
            render(clips, total, sources, wav)
            heard = transcribe(wav, work, args.language)
            expected = timeline_words(clips, tdir, source=True)
            hes, missing, scraps = compare(expected, heard)
            todo = [] if args.time_only or timing else fixes(clips, hes if args.hesitations == 'all' else [], missing, scraps, sounds, words_of)
            if not (args.time_only or timing):
                todo += [(k, 'rejoin', clips[k]['in_s'], clips[k]['in_s'], 'join') for k in joins_heard(edl, clips, expected, heard)]
            if n == args.rounds:  # no listening after this one to check a new cut: only the joins go back (the take as said)
                todo = [f for f in todo if f[1] == 'rejoin']
            score = len(todo)
            fresh = [f for f in todo if (f[1], clips[f[0]]['file'], round(f[2], 1)) not in tried]
            if best is not None and score > best[0]:  # worse than before: the better cuts back, and done
                back, bclips, joins = best[1], best[4], best[5]
                # the joins that failed then still go back: the hesitation in again, the take as it was said
                rejoined, _ = apply(back, bclips, joins, sounds, words_of) if joins else ([], [])
                with open(args.edl, 'w', encoding='utf-8') as f:
                    json.dump(back, f, ensure_ascii=False, indent=1)
                last = report['rounds'][-1]
                last['undone'] = [x for x in last.pop('fixes', []) if x['kind'] != 'rejoin']  # not in the edit any more
                last['fixes'] = [{'at': round(bclips[k]['off_s'], 2), 'kind': kind, 'what': why} for k, kind, why in rejoined]
                report['rounds'].append({'hesitations_heard': len(hes), 'fixes': []})
                print(f'round {n}: no better ({score} to fix, {best[0]} before): the cuts of round {n - 1} are kept')
                if rejoined and n <= args.rounds:
                    retime = True
                    continue
                with open(os.path.join(folder, 'final-words.json'), 'w', encoding='utf-8') as f:
                    json.dump(best[2], f, ensure_ascii=False)
                report['moments'] = best[3]
                break
            best = (score, json.loads(json.dumps(edl)), {'edl': fingerprint(edl), 'words': timed(expected, heard, clips)},
                    moments(clips, words_of, expected, sounds), clips, [f for f in todo if f[1] == 'rejoin'])
            round_ = {'hesitations_heard': len(hes), 'missing_at_edges': sum(1 for f in todo if f[1].startswith('extend')),
                      'scraps_at_edges': sum(1 for f in todo if f[1].startswith('trim')), 'missing_elsewhere': len(missing),
                      'fixes': []}
            report['rounds'].append(round_)
            print(f'round {n}: {len(hes)} hesitations heard, {round_["missing_at_edges"]} words cut at an edge, '
                  f'{round_["scraps_at_edges"]} scraps of words at an edge ({len(missing)} words heard differently in all)')
            if not fresh or timing:  # nothing new to fix (what is left was tried once already)
                with open(os.path.join(folder, 'final-words.json'), 'w', encoding='utf-8') as f:
                    json.dump({'edl': fingerprint(edl), 'words': timed(expected, heard, clips)}, f, ensure_ascii=False)
                report['left'] = [{'at': round(s, 2), 'what': w, 'kind': 'hesitation'} for s, e, w in hes]
                report['moments'] = moments(clips, words_of, expected, sounds)
                break
            keep = {}
            for w in expected:
                k = clip_at(clips, w[0])
                if k is not None:
                    src = clips[k]['in_s'] + (w[0] - clips[k]['off_s'])
                    a0, b0 = keep.get(k, (float('inf'), float('-inf')))
                    keep[k] = (min(a0, w[4] if len(w) > 4 else src), max(b0, (w[4] if len(w) > 4 else src) + (w[1] - w[0])))
            done, left = apply(edl, clips, todo, sounds, words_of, keep, tried, args.splice_level, args.splice_timbre)
            round_['fixes'] = [{'at': round(clips[k]['off_s'], 2), 'kind': kind, 'what': why} for k, kind, why in done]
            round_['kept'] = [{'at': round(clips[k]['off_s'], 2), 'what': why, 'why': reason} for k, why, reason in left]
            with open(args.edl, 'w', encoding='utf-8') as f:
                json.dump(edl, f, ensure_ascii=False, indent=1)
            if not done:
                with open(os.path.join(folder, 'final-words.json'), 'w', encoding='utf-8') as f:
                    json.dump({'edl': fingerprint(edl), 'words': timed(expected, heard, clips)}, f, ensure_ascii=False)
                report['moments'] = moments(clips, words_of, expected, sounds)
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(folder, 'verify.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
