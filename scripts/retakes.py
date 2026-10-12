#!/usr/bin/env python3
"""The takes the speaker calls failed out loud ("on la refait", "je reprends", "take two", "let me start over"): the
failed take and the call are cut, the take after it is kept. Such a phrase is only a candidate, as it can also be part
of what is told (a story, an interview, a quotation). Three things decide together:
- the phrase, from a list in the settings (cuts.retake_phrases), case and punctuation aside, hesitations skipped;
- whether the speaker then says again what came before the call: where the new take starts like an earlier stretch,
  that stretch is the failed take; when nothing matches, it starts after the last clear silence before the call;
- the brain, which reads each candidate with the whole transcript and says which ones are retakes.
A call reported ("il m'a dit : on la refait", "she said take two") with nothing said again after it is never cut;
without a word from the brain, a call is cut only when what follows says again what came before it.
Usage: retakes.py clip.words.json [--config roughcut.json]   (prints the candidates and the cut each one proposes)"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

LOOKBACK = 45.0  # a failed take starts at most this long before its call...
SILENCE_LOOKBACK = 15.0  # ...and, found by its silence alone, at most this long
RESTART = 12.0  # the new take starts this soon after the call, at most
SILENCE = 0.7  # a clear silence between two words (a sentence ends on one, auto_edit.sentences)
COMPARE = 20  # words of the new take set against what was said before the call
COVER = 0.25  # the words alike, at least this share of the words from the failed take's start to the call
# the word just before a call that makes it reported speech: "il m'a dit : on la refait", "she said take two"
REPORTED = {'dit', 'dis', 'disait', 'dire', 'répond', 'répondu', 'répondait', 'crié', 'criait', 'demandé', 'demandait',
            'que', 'said', 'says', 'say', 'told', 'tells', 'asked', 'asks', 'replied', 'answered', 'shouted', 'yelled',
            'goes', 'went', 'that'}
QUOTES = '«"“'

def bare(word):
    return re.sub(r"[^\w']", '', word.lower().replace('’', "'")).strip("'")

def phrases(texts):
    """The calls as lists of words, the longest first: "Attends, non" and "attends non" are one."""
    out = []
    for t in texts:
        p = [x for x in (bare(x) for x in str(t).split()) if x]
        if p and p not in out:
            out.append(p)
    return sorted(out, key=len, reverse=True)

def find(words, texts):
    """The candidates of a clip, times in its seconds: [{'start', 'end', 'said', 'from', 'to', 'by', 'repeat',
    'reported'}]. 'said': the call as transcribed; 'from'-'to': the cut it proposes, from the first word of the failed
    take to the first word of the new take (to the end of the call when no new take is recognised); 'by': how the start
    was found, 'repeat' (the new take says 'repeat' again), 'silence' or 'call' (nothing before it to cut); 'cut': the
    words the cut takes out."""
    toks = [(w, bare(w['w'])) for w in words if not (w.get('filler') or w.get('suspect') or w.get('removed'))]
    toks = [(w, t) for w, t in toks if t]
    by_first = {}
    for p in phrases(texts):
        by_first.setdefault(p[0], []).append(p)
    calls, k = [], 0
    while k < len(toks):
        p = next((p for p in by_first.get(toks[k][1], []) if [t for _, t in toks[k:k + len(p)]] == p), None)
        if p:
            # calls one after the other ("non attends, je reprends") are one call
            if calls and k - calls[-1][1] <= 2 and toks[k][0]['start'] - toks[calls[-1][1]][0]['end'] < 2.0:
                calls[-1] = (calls[-1][0], k + len(p) - 1)
            else:
                calls.append((k, k + len(p) - 1))
            k += len(p)
        else:
            k += 1
    out = []
    for n, (a, b) in enumerate(calls):
        first, last = toks[a][0], toks[b][0]
        floor = calls[n - 1][1] + 1 if n else 0  # never back past the call before, nor on to the next one
        ceil = calls[n + 1][0] if n + 1 < len(calls) else len(toks)
        before = [x for x in range(floor, a) if toks[x][0]['start'] >= first['start'] - LOOKBACK]
        after = list(range(b + 1, ceil))[:COMPARE]
        if after and toks[after[0]][0]['start'] - last['end'] > RESTART:
            after = []
        # the new take starting like a stretch before the call: two words or more alike, the stretch starting a phrase
        # and covering a good part of what was said up to the call (two common words alike 30 s back are no failed
        # take). A few words may come first in the new take ("ok", "allez"), but then the match must be longer, and a
        # match from its very first word wins (Whisper often times the first words of a sentence at the end of the one
        # before, so a later match, "want to show you", would leave "Today I" of the failed take in); then the latest
        # stretch, then the longest
        best = None
        for j in range(min(3, len(after))):
            for p in before:
                m = 0
                while p + m < a and j + m < len(after) and toks[p + m][1] == toks[after[j + m]][1]:
                    m += 1
                if m < (2 if j == 0 else 3) or m < COVER * (a - p) or not _starts(toks, p):
                    continue
                if best is None or (-j, p, m) > (-best[2], best[1], best[0]):
                    best = (m, p, j)
        repeat, to = '', last['end']
        if best:
            m, p, j = best
            start, to, by = toks[p][0]['start'], toks[after[j]][0]['start'], 'repeat'
            repeat = ' '.join(toks[p + i][0]['w'] for i in range(m))
        else:  # the failed take starts after the last clear silence before the call (the one just before it aside)
            start, by, nxt = first['start'], 'call', first
            for x in reversed(before):
                w = toks[x][0]
                if nxt is not first and nxt['start'] - w['end'] >= SILENCE:
                    start, by = nxt['start'], 'silence'
                    break
                if first['start'] - w['start'] > SILENCE_LOOKBACK:
                    break  # no clear silence near it: only the call goes
                nxt = w
            else:
                if nxt is not first:  # back to the start of the clip, or to the call before
                    start, by = nxt['start'], 'silence'
        prev = toks[a - 1][0] if a else None
        reported = bool(prev and ((toks[a - 1][1] in REPORTED and not re.search(r'[.!?…]$', prev['w'])
                                   and first['start'] - prev['end'] < SILENCE) or prev['w'].rstrip().endswith(':'))
                        or first['w'].lstrip()[:1] in QUOTES)
        out.append({'start': round(first['start'], 3), 'end': round(last['end'], 3),
                    'said': ' '.join(t[0]['w'] for t in toks[a:b + 1]), 'from': round(start, 3), 'to': round(to, 3),
                    'by': by, 'repeat': repeat, 'reported': reported,
                    'cut': ' '.join(w['w'] for w, _ in toks if start <= w['start'] < max(to, last['end']))})
    return out

def _starts(toks, p):
    """Word p starts a phrase: the first one, or after a punctuation mark or a breath."""
    if p == 0:
        return True
    prev = toks[p - 1][0]
    return bool(re.search(r'[.!?…,;:]$', prev['w'])) or toks[p][0]['start'] - prev['end'] >= 0.25

def asked(found):
    """The candidates the brain is asked about: a reported call with nothing said again is never cut, so not asked."""
    return [c for c in found if c['repeat'] or not c['reported']]

PROMPT = """The speaker may have called failed takes out loud. Each candidate below is such a call heard in a clip, with
the cut it would make: from the first word of the failed take to the first word of the new take, the call included.
Decide for each one: a retake (the speaker breaks off, says it to whoever edits, and starts again) or words that belong
to what is told (a story, an interview, a quotation, a joke), which are never cut. When the failed take starts earlier
or later than proposed, give its start in "from" (a line's start time). Leave the failed takes and the calls out of
"hook", "main" and "shorts".
{lines}
"""
FIELD = '\n  "retakes": [{"id": "C1", "retake": true, "from": 0.0, "to": 0.0}],  // one for each candidate retake above'

def prompt(found):
    """What the brain reads about the candidates (`found`, with their 'id' and 'rush'), and the field it answers in."""
    if not found:
        return '', ''
    lines = []
    for c in found:
        how = (f'the new take says again "{c["repeat"]}", first said at {c["from"]:.2f}' if c['by'] == 'repeat'
               else 'nothing after it says again what came before: the cut starts after the last clear pause' if c['by'] == 'silence'
               else 'nothing said just before it')
        cut = c.get('cut', '')
        cut = cut if len(cut) <= 200 else cut[:120] + ' … ' + cut[-60:]  # a long failed take: its start and its end
        lines.append(f'{c["id"]}: {c["rush"]}, "{c["said"]}" at {c["start"]:.2f}-{c["end"]:.2f}; '
                     f'proposed cut {c["from"]:.2f}-{c["to"]:.2f}' + (f', "{cut}"' if cut else '') + f' ({how})')
    return PROMPT.format(lines='\n'.join(lines)), FIELD

def verdicts(answer, found):
    """The brain's word on each candidate, {id: {'retake', 'from', 'to'}}: by its id, or, in the choices kept with an
    earlier version (where the ids may have changed with the phrases), by its clip and the time of its call."""
    items = answer.get('retakes') if isinstance(answer, dict) else None
    out = {}
    for v in items if isinstance(items, list) else []:
        if not isinstance(v, dict):
            continue
        if 'rush' in v and 'at' in v:
            c = next((c for c in found if c['rush'] == v['rush'] and _near(v['at'], c['start'])), None)
        else:
            c = next((c for c in found if c['id'] == v.get('id')), None)
        if c:
            out[c['id']] = v
    return out

def _near(x, y, within=0.5):
    try:
        return abs(float(x) - y) <= within
    except (TypeError, ValueError):
        return False

def _within(x, lo, hi, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return round(x, 3) if lo - 0.05 <= x <= hi + 0.05 else default

def decide(found, said):
    """Each candidate with 'cut' and 'why': 'brain' (the brain said), 'reported' (never cut), else, with no word from
    the brain, 'repeat' (cut: the new take says again what came before) or 'no repeat' (kept). `said`: verdicts(). A
    start or end the brain moved stays within reach of the call."""
    out = []
    for c in found:
        v = said.get(c.get('id'))
        d = dict(c)
        if c['reported'] and not c['repeat']:
            d.update(cut=False, why='reported')
        elif isinstance(v, dict) and 'retake' in v:
            d.update(cut=v['retake'] in (True, 'true', 'yes', 1), why='brain')
            if d['cut']:
                d['from'] = _within(v.get('from'), c['start'] - LOOKBACK, c['start'], c['from'])
                d['to'] = _within(v.get('to'), c['end'], c['end'] + RESTART, c['to'])
        else:
            d.update(cut=bool(c['repeat']), why='repeat' if c['repeat'] else 'no repeat')
        out.append(d)
    return out

def minus(ranges, spans):
    """The ranges of an edit without the failed takes, `spans` {file: [(from, to)]}: a range is cut around each span,
    a piece ending where the failed take starts and the next starting on the new take, so only whole words fall on
    either side. A chapter or a marker goes with the first piece left (a chapter on to the next range when nothing is
    left); a moment kept as it is (no speech) stays."""
    out, chapter = [], None
    for r in ranges:
        pieces = [(r['from'], r['to'])]
        for a, b in [] if r.get('whole') else spans.get(r['file'], []):
            pieces = [x for p, q in pieces for x in ([(p, q)] if b <= p or a >= q else [(p, a), (b, q)]) if x[1] - x[0] > 0.05]
        if not pieces:
            chapter = r.get('chapter') or chapter
            continue
        for n, (p, q) in enumerate(pieces):
            piece = {k: v for k, v in r.items() if n == 0 or k not in ('chapter', 'marker')}
            piece.update({'from': round(p, 3), 'to': round(q, 3)})
            if n == 0 and chapter and not piece.get('chapter'):
                piece['chapter'] = chapter
            out.append(piece)
        chapter = None
    return out

def spans_of(decided):
    """{file: [(from, to)]} of the retakes cut."""
    out = {}
    for d in decided:
        if d['cut']:
            out.setdefault(d['file'], []).append((d['from'], d['to']))
    return out

def main():
    parser = argparse.ArgumentParser(description='Lists the retakes called out loud in a transcript.')
    parser.add_argument('words', help='<clip>.words.json (words.py)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    with open(args.words, encoding='utf-8') as f:
        ws = json.load(f)['words']
    cfg = settings.load(os.path.dirname(os.path.abspath(args.words)), args.config)['cuts']
    for c in find(ws, cfg['retake_phrases']):
        print(f'{c["start"]:8.2f} "{c["said"]}"  cut {c["from"]:.2f}-{c["to"]:.2f} ({c["by"]}'
              + (f': "{c["repeat"]}"' if c['repeat'] else '') + (', reported' if c['reported'] else '') + ')')

if __name__ == '__main__':
    main()
