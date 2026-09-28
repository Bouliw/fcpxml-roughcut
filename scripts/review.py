"""The edit read again, as a viewer: once the edit is put together, the brain reads what it says, in order,
next to what was cut, and names what does not follow: a reference to a passage that was cut, a new place with nothing
to show the way there, a question without its answer or an answer without its question. Its fixes (lines added back
from the clips, shots played with their own sound, lines taken out) are applied to the ranges of the edit, within
limits, so the review can mend the story but never remake it."""

PROMPT = """You are watching the rough cut of a YouTube video as a viewer who never saw the footage. Read the edit below
from start to end, then fix only what does not follow:
- a reference to something the viewer never heard or saw ("as I said", "this one", "him", a name never introduced);
- a new place or moment with nothing to show the way there, when the clips have it (a line, or a shot without speech);
- a question without its answer, or an answer without its question;
- a sentence or a story cut before its end.
Fix it by adding the missing line from the clips, or by taking out the line that cannot be understood. Keep the order
and the structure: the hook first, then the introduction, the day in the order it was filmed, the interviews, the
conclusion. Do not cut for length and do not add what is only nice to have: each change fixes one problem, and an
edit that follows needs none. Write each "why" in the language of the video, in a few words.

The edit, as it plays. E1, E2...: each line heard, in order; the hook plays first, out of its place; a [shot] has no
speech, only its own sound:
{edit}

The clips: everything that was said, [start-end] in seconds of the clip; a line of the edit shows its number, a line
left out its text:
{clips}
{shots}
Answer with ONE JSON object only, no text around it:
{{
  "add": [{{"rush": "R1", "from": 0.0, "to": 0.0, "after": "E12", "why": ""}}],  // lines left out, put back (copy their
                                                        // times; consecutive lines in one range), played after line
                                                        // E12 of the edit (E0: first, after the hook)
  "add_shot": [{{"shot": "B001", "after": "E12", "why": ""}}],  // a shot of the list, played with its own sound
  "remove": [{{"line": "E14", "why": ""}}]              // lines of the edit taken out
}}"""

MAX_ADD_SECONDS = 45.0  # one range added back: a few lines, never a whole passage
ADDED_SHARE, ADDED_MIN = 0.1, 60.0  # all that is added: 10 % of the edit, at least a minute
REMOVED_SHARE = 0.1  # all that is taken out
SHORTEST = 0.3  # what is left of a range once lines are taken out, below this: dropped

def lines_of(ranges, sentences, rush_of):
    """The lines the edit plays, in order: {'id': 'E1', 'k': index of its range, 'a', 'b', 'text', 'hook', 'shot'}.
    `sentences`: {file: [(start, end, text)]}; `rush_of`: {file: 'R1'}."""
    out = []
    for k, r in enumerate(ranges):
        hook = r.get('note') == 'hook'
        if r.get('whole'):  # a moment without speech
            out.append({'k': k, 'a': r['from'], 'b': r['to'], 'text': r.get('marker', ''), 'hook': hook, 'shot': True})
            continue
        found = []
        for a, b, text in sentences.get(r['file'], []):
            inside = min(b, r['to']) - max(a, r['from'])
            if b > a and inside > 0.5 * (b - a):
                found.append({'k': k, 'a': max(a, r['from']), 'b': min(b, r['to']), 'text': text, 'hook': hook, 'shot': False})
        out += found or [{'k': k, 'a': r['from'], 'b': r['to'], 'text': '(no speech)', 'hook': hook, 'shot': False}]
    for n, line in enumerate(out, 1):
        line['id'] = f'E{n}'
    return out

def build_prompt(ranges, lines, sentences, rush_of, shots):
    """`shots`: the shots of the catalogue not used yet, [{'id', 'rush', 'from', 'to', 'labels'}]."""
    edit = []
    for line in lines:
        where = f'{rush_of.get(ranges[line["k"]]["file"], "?")} {line["a"]:.2f}-{line["b"]:.2f}'
        if line['shot']:
            edit.append(f'{line["id"]} [shot {where}] {line["text"]}')
        else:
            edit.append(f'{line["id"]} [{where}]{" (hook)" if line["hook"] else ""} {line["text"]}')
    clips = []
    for f, rid in rush_of.items():
        played = [line for line in lines if not line['shot'] and ranges[line['k']]['file'] == f]
        rows = []
        for a, b, text in sentences.get(f, []):
            ids = [line['id'] for line in played if min(b, line['b']) - max(a, line['a']) > 0.5 * (b - a)]
            rows.append(f'[{a}-{b}] ' + ('= ' + ', '.join(ids) if ids else text))
        clips.append(f'### {rid}\n' + ('\n'.join(rows) or '(no speech)'))
    listed = [f'{s["id"]}: {s["rush"]} {s["from"]}-{s["to"]} s, {", ".join(s.get("labels", [])) or "no label"}' for s in shots]
    return PROMPT.format(edit='\n'.join(edit), clips='\n\n'.join(clips),
                         shots=('Shots without speech, not in the edit:\n' + '\n'.join(listed) + '\n') if listed else '')

def _free(file, a, b, ranges):
    """[a, b] of `file` without what the edit already plays (the hook included): the pieces left."""
    pieces = [(a, b)]
    for r in ranges:
        if r['file'] != file:
            continue
        pieces = [p for x, y in pieces for p in ((x, min(y, r['from'])), (max(x, r['to']), y)) if p[1] - p[0] > 0]
    return [p for p in pieces if p[1] - p[0] >= 0.5]

def apply(ranges, lines, answer, files, durations, shots, moment, label='Review'):
    """The ranges of the edit with the review's fixes, and what was done: [(kind, why, detail)].
    `files`: {'R1': file}; `durations`: {'R1': seconds}; `shots`: {id: shot} the review could choose from;
    `moment(shot, marker)`: the range that plays a shot with its own sound."""
    d = answer if isinstance(answer, dict) else {}
    by_id = {line['id']: line for line in lines}
    length = sum(r['to'] - r['from'] for r in ranges)
    story = [line for line in lines if not line['hook']]
    order = {f: i for i, f in enumerate(files.values())}
    done, cuts, inserts = [], {}, {}
    removed, added = 0.0, 0.0

    def after(item, file=None, at=None):
        """Where an addition goes: (range index, time in it); the start of the story for E0 or a hook line; else, when
        the line named is unknown, in the order the clips were filmed."""
        line = by_id.get(str(item.get('after', '')).strip())
        if line and not line['hook']:
            return line['k'], line['b']
        if str(item.get('after', '')).strip() in ('E0', '0') or line:
            return (story[0]['k'], float('-inf')) if story else None
        if file is None or not story:
            return None
        later = [x for x in story if (order.get(ranges[x['k']]['file'], 0), x['a']) > (order.get(file, 0), at)]
        if not later:
            return story[-1]['k'], story[-1]['b']
        first = later[0]
        return first['k'], first['a'] - 1e-6

    for item in d.get('remove') or []:
        line = by_id.get(str(item.get('line', '')).strip()) if isinstance(item, dict) else None
        if not line or line['hook'] or line.get('gone'):
            continue
        if removed + line['b'] - line['a'] > REMOVED_SHARE * length:
            done.append(('skipped', item.get('why', ''), f'{line["id"]}: past the share that can be taken out'))
            continue
        line['gone'] = True
        removed += line['b'] - line['a']
        r = ranges[line['k']]
        cuts.setdefault(line['k'], []).append((r['from'], r['to']) if line['shot'] else (line['a'], line['b']))
        done.append(('removed', item.get('why', ''), f'{line["id"]} {line["text"]}'))
    budget = max(ADDED_MIN, ADDED_SHARE * length)
    for item in d.get('add') or []:
        try:
            rid, a, b = item['rush'], float(item['from']), float(item['to'])
        except (KeyError, TypeError, ValueError):
            continue
        if rid not in files or not 0 <= a < b or a >= durations[rid] or b - a > MAX_ADD_SECONDS:
            continue
        pieces = _free(files[rid], a, min(b, durations[rid]), ranges)
        seconds = sum(y - x for x, y in pieces)
        where = after(item, files[rid], a)
        if not pieces or where is None:
            continue
        if added + seconds > budget:
            done.append(('skipped', item.get('why', ''), f'{rid} {a}-{b}: past the share that can be added'))
            continue
        added += seconds
        marker = f'{label}: {item.get("why", "")}'.strip(': ')[:80]
        inserts.setdefault(where[0], []).extend(
            (where[1], {'file': files[rid], 'from': round(x, 2), 'to': round(y, 2), 'marker': marker}) for x, y in pieces)
        done.append(('added', item.get('why', ''), f'{rid} {a}-{b}'))
    for item in d.get('add_shot') or []:
        shot = shots.get(str(item.get('shot', '')).strip()) if isinstance(item, dict) else None
        where = after(item, shot and shot['file'], shot and shot['from']) if shot else None
        if not shot or where is None:
            continue
        m = moment(shot, f'{label}: {item.get("why", "")}'.strip(': ')[:80])
        if not m or added + m['to'] - m['from'] > budget:
            continue
        added += m['to'] - m['from']
        shots.pop(shot['id'], None)  # once
        inserts.setdefault(where[0], []).append((where[1], m))
        done.append(('added', item.get('why', ''), f'shot {shot["id"]}'))
    out = []
    for k, r in enumerate(ranges):
        out += _rebuilt(r, cuts.get(k, []), inserts.get(k, []))
    return out, done

def _rebuilt(r, cuts, inserts):
    """Range r without the spans `cuts`, with the ranges `inserts` [(time in r, range)] played at those times."""
    pieces, at = [], r['from']
    for a, b in sorted(cuts):
        if a > at:
            pieces.append((at, a))
        at = max(at, b)
    if at < r['to']:
        pieces.append((at, r['to']))
    pending = sorted(inserts, key=lambda x: x[0])  # stable: several at one time keep their order
    out, first = [], [True]

    def piece(a, b):
        if b - a < SHORTEST:
            return
        keep = r if first[0] else {k: v for k, v in r.items() if k not in ('chapter', 'note')}
        out.append(dict(keep, **{'from': round(a, 2), 'to': round(b, 2)}))
        first[0] = False

    def put(new):
        if first[0] and r.get('chapter'):  # played before the range: its chapter starts there
            new, first[0] = dict(new, chapter=r['chapter']), False
        out.append(new)

    for a, b in pieces:
        start = a
        while pending and pending[0][0] < b - 0.05:
            t, new = pending.pop(0)
            if t > start:
                piece(start, t)
                start = t
            put(new)
        piece(start, b)
    for _, new in pending:
        put(new)
    return out
