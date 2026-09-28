"""Split edits where the scene changes (the source changes from one cut to the next): a J cut lets the sound of the
next cut start before its picture, an L cut lets the sound of the previous cut run over the next picture. In Final
Cut Pro's primary storyline the two sounds never overlap: what one gains, the other gives up.

The length (0.6 s by default) is cut down, or the edit left straight, so that no word of the transcripts is cut off
or brought in: the sound moved across the cut must be free of speech on both sides. "auto" tries a J cut first
(entering the new scene by its sound), then an L cut. A cut can ask for "split": "j", "l" or "none" in edl.json,
and "split_seconds"; asking for one also applies it where there is no transcript to check."""
import os
from timeline import transcript_path

def load_words(clips, tdir):
    """{source: [(start, end)]} of every word said (hesitations included: they are sound too), or None when unknown."""
    import json
    out = {}
    for c in clips:
        if c['file'] in out:
            continue
        p = transcript_path(tdir, c['file'])
        out[c['file']] = None
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                out[c['file']] = [(w['start'], w['end']) for w in json.load(f)['words'] if not w.get('suspect')]
    return out

def free_before(words, t):
    """Seconds of silence in the source just before t."""
    ends = [e for s, e in words if s < t]
    return t - max(ends) if ends else t

def free_after(words, t, duration):
    """Seconds of silence in the source just after t."""
    starts = [s for s, e in words if e > t]
    return (min(starts) if starts else duration) - t

def plan(clips, cfg, words, fps):
    """[(lead frames, tail frames)] per cut (lead: sound starting before the picture; tail: sound running after it,
    negative when it gives up its end), and [(cut number, 'J' or 'L', seconds, or the reason it was left straight)]."""
    s = cfg['split_edits']
    lead, tail, notes = [0] * len(clips), [0] * len(clips), []
    for k in range(1, len(clips)):
        prev, nxt = clips[k - 1], clips[k]
        if prev['file'] == nxt['file']:
            continue  # the same scene
        if 'dialogue' != prev.get('role', 'dialogue') or 'dialogue' != nxt.get('role', 'dialogue'):
            continue  # an IRL moment keeps its sound with its picture
        mode = nxt.get('split', s['mode'])
        want = float(nxt.get('split_seconds', s['seconds']))
        if mode == 'none' or not prev['asset']['audio_channels'] or not nxt['asset']['audio_channels']:
            continue
        pw, nw = words.get(prev['file']), words.get(nxt['file'])
        forced = 'split' in nxt
        if (pw is None or nw is None) and not forced:
            notes.append((k + 1, None, 'left straight: no transcript to check the words'))
            continue
        p_out, n_in = prev['in_s'] + prev['dur_s'], nxt['in_s']
        room_prev = prev['dur_s'] + lead[k - 1] / fps - s['min_seconds']  # keep some sound of the previous cut
        # J: the next source's sound before its in point, over the end of the previous cut, both free of speech
        j = min(want, n_in, room_prev,
                *([] if forced else [free_before(nw, n_in), p_out - max([e for st, e in pw if st < p_out] or [prev['in_s']])]))
        # L: the previous source's sound after its out point, over the start of the next cut, both free of speech
        l = min(want, prev['asset']['duration'] - p_out, nxt['dur_s'] - s['min_seconds'],
                *([] if forced else [free_after(pw, p_out, prev['asset']['duration']), free_after(nw, n_in, nxt['asset']['duration'])]))
        choice = {'j': ('J', j), 'l': ('L', l)}.get(mode) or (('J', j) if j >= s['min_seconds'] else ('L', l))
        kind, x = choice
        x_f = int(x * fps)
        if x < s['min_seconds'] or x_f < 1:
            notes.append((k + 1, None, 'left straight: speech on both sides of the cut'))
            continue
        if kind == 'J':
            lead[k] += x_f
            tail[k - 1] -= x_f
        else:
            tail[k - 1] += x_f
            lead[k] -= x_f
        notes.append((k + 1, kind, x_f / fps))
    return list(zip(lead, tail)), notes
