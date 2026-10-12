#!/usr/bin/env python3
"""The takes started over in front of the Takeline teleprompter. At the end of a reading, Takeline saves the script
sentence by sentence and every sentence the speaker started over, whether they said so out loud or simply read it
again (a .takeline.json file: https://github.com/Bouliw/takeline/blob/master/docs/session-format.md). Such a sentence
starts twice in the clip's transcript: the failed take runs from its first start to the second. These takes come on
top of the calls heard in the transcript (retakes.py) and go through the same checks.
Sessions are looked for in the folder of footage and in Takeline's own folder (~/Documents/Takeline). A session applies
to a clip that says at least three of its sentences, so sessions from other days are left alone.
Usage: takeline.py clip.words.json [session.takeline.json ...]   (prints the failed takes found)"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retakes import bare

FOLDER = os.path.expanduser('~/Documents/Takeline')
HEAD = 6  # the first words of a sentence, looked for in the transcript
MATCH = 3  # sentences of a session a clip must say for the session to apply to it
REACH = 60.0  # a failed take starts at most this long before the new one

def sessions(*folders):
    """The Takeline sessions in these folders (Takeline's own folder by default)."""
    out = []
    for folder in folders or (FOLDER,):
        for path in sorted(glob.glob(os.path.join(folder, '*.takeline.json'))):
            try:
                with open(path, encoding='utf-8') as f:
                    s = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(s, dict) and s.get('format') == 'takeline.session' and s.get('version') == 1:
                out.append(s)
    return out

def _head(text):
    return tuple(t for t in (bare(w) for w in str(text).split()) if t)[:HEAD]

def _common(a, b):
    """How many words of a are found in b, in the same order."""
    row = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], prev + 1 if x == y else max(row[j], row[j - 1])
    return row[-1]

def _at(toks, head):
    """Where the sentence starts in the clip: on its first or second word, with at least half of its first words
    following in order (a word misheard, left out or added does not hide it, as the speaker rarely says a sentence
    exactly as written). A start seen on its first word then on its second is one."""
    need = max(3, (len(head) + 1) // 2)
    out = []
    for i in range(len(toks)):
        if toks[i][1] in head[:2] and (not out or i - out[-1] > 1) \
                and _common(head, [t for _, t in toks[i:i + len(head) + 2]]) >= need:
            out.append(i)
    return out

def find(words, session):
    """The failed takes of a clip, shaped like retakes.find()'s candidates ('by' is 'takeline', 'start' and 'end' the
    moment the sentence starts over, 'repeat' the words said again), or none when the clip doesn't say the session."""
    toks = [(w, bare(w['w'])) for w in words if not (w.get('filler') or w.get('suspect') or w.get('removed'))]
    toks = [(w, t) for w, t in toks if t]
    keys = [_head(s.get('text', '')) if isinstance(s, dict) else () for s in session.get('sentences') or []]
    # a start shared by two sentences of the script tells nothing
    unique = {k for k in keys if len(k) >= 3 and keys.count(k) == 1}
    if sum(1 for k in unique if _at(toks, k)) < MATCH:
        return []
    redone = {}
    for r in session.get('retakes') or []:
        n = r.get('sentence') if isinstance(r, dict) else None
        if isinstance(n, int) and 0 <= n < len(keys) and keys[n] in unique:
            redone[n] = redone.get(n, 0) + 1
    out = []
    for n, times in sorted(redone.items()):
        starts = _at(toks, keys[n])
        # each time the sentence was started over, the take before the next start failed; the last ones count
        for a, b in list(zip(starts, starts[1:]))[-times:]:
            first, again = toks[a][0], toks[b][0]
            if again['start'] - first['start'] > REACH:
                continue
            out.append({'start': round(again['start'], 3), 'end': round(again['start'], 3), 'said': '',
                        'from': round(first['start'], 3), 'to': round(again['start'], 3), 'by': 'takeline',
                        'repeat': ' '.join(w['w'] for w, _ in toks[b:b + 4]), 'reported': False,
                        'cut': ' '.join(w['w'] for w, _ in toks if first['start'] <= w['start'] < again['start'])})
    return out

def overlaps(c, found):
    """Whether candidate c covers the same words as one already found (by a call heard, or another session)."""
    return any(c['from'] < d['to'] and d['from'] < c['to'] for d in found)

def main():
    parser = argparse.ArgumentParser(description='Lists the takes a Takeline session saw started over in a transcript.')
    parser.add_argument('words', help='<clip>.words.json (words.py)')
    parser.add_argument('sessions', nargs='*', help='.takeline.json files (default: those in ~/Documents/Takeline)')
    args = parser.parse_args()
    with open(args.words, encoding='utf-8') as f:
        ws = json.load(f)['words']
    found = []
    for path in args.sessions:
        with open(path, encoding='utf-8') as f:
            found += find(ws, json.load(f))
    if not args.sessions:
        for s in sessions():
            found += find(ws, s)
    for c in found:
        print(f'{c["from"]:8.2f}-{c["to"]:.2f} started over: "{c["repeat"]}"  cut "{c["cut"]}"')

if __name__ == '__main__':
    main()
