#!/usr/bin/env python3
"""The edit watched before it is delivered. A small proxy of the edit is rendered (render_preview.py, 360 lines
high), then:
- a picture is taken on each side of every cut, and in the middle of the shot that follows, and at the start, middle
  and end of every B-roll; they are laid out on contact sheets, one row per cut, each picture labelled;
- the sound of the proxy is transcribed again, with the same Whisper model as the clips, and set against the words
  kept: a sentence whose first or last word is not heard at the edge of a cut was cut short;
- the brain looks at the sheets and names the jump cuts, the missed looks at the camera, the blurred shots and the
  B-roll that does not fit what is said.
What can be mended without touching the words is mended in edl.json: a jump cut gets the punch-in zoom it lacks, a
blurred or misplaced B-roll is taken off. Everything else becomes a to-do marker (red in Final Cut Pro) at its time,
with the reason. Then once more, on the mended edit, at most --rounds times. The proxy, the sheets and
visual-review.json (what was seen and done) stay in review/ next to edl.json.
Usage: visual_review.py edl.json [--rounds 2] [--height 360] [--language fr] [--zoom-jump-cuts] [--config file]"""
import argparse, difflib, json, math, os, re, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain, broll, settings, timeline, verify_edit, zoom
import music as tracks
from make_srt import timeline_words

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BROLL = 16  # B-roll rows shown (2 sheets): the first ones
MAX_CUTS = 64  # rows shown to the brain (8 sheets): past this, the cuts inside one take first, where the jumps are
ROWS = 8  # cuts per sheet
NEAR = 0.08  # seconds from a cut to the pictures on each side of it
REMOVED_SHARE = 0.5  # of the B-roll, at most: the review mends an edit, it does not undo it
FONTS = ('/System/Library/Fonts/Helvetica.ttc', '/System/Library/Fonts/Supplemental/Arial.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
TEXT = {'en': {'label': 'Review:', 'short': 'sentence cut short', 'jump': 'jump cut', 'look': 'look away from the camera',
               'blur': 'blurred shot', 'misplaced': 'B-roll out of place'},
        'fr': {'label': 'Relecture :', 'short': 'phrase coupée', 'jump': 'saut d\'image', 'look': 'regard hors caméra',
               'blur': 'plan flou', 'misplaced': 'B-roll mal placé'}}

PROMPT = """You are checking the rough cut of a video before it goes to its editor, from pictures of it.
The {n} contact sheets attached show the edit in order. Each row is one cut, three pictures wide: the last picture
before the cut, the first one after it, and the middle of the shot that follows. Rows named B1, B2... are the B-roll
shots laid over the edit: their start, middle and end.{labels}
{legend}

The cuts ("same take": both sides come from one take, where a jump shows; "zoomed": the shot after the cut is
already scaled up to hide it):
{cuts}
{brolls}
Name only what a viewer would notice:
- "jump": a jump cut, the same person in the same framing on both sides, the picture jumping;
- "look": a missed look at the camera, the speaker starting or ending the shot looking away, at a screen, or
  reaching for the camera;
- "blur": a shot out of focus or shaken;
- for a B-roll, "blur", or "misplaced" when it does not show what is said while it plays, or shows nothing readable.
A change of shot is not a jump, and a cut hidden under a B-roll needs nothing. An edit with nothing to fix gets
empty lists. Write each "why" in a few words, in {language}.

Answer with ONE JSON object only, no text around it:
{{
  "cuts": [{{"cut": "C7", "problem": "jump", "why": ""}}],
  "broll": [{{"id": "B2", "problem": "misplaced", "why": ""}}]
}}"""

def mmss(t):
    return f'{int(t) // 60}:{int(t) % 60:02d}'

def font():
    """The font the labels are drawn with, or None when this ffmpeg cannot draw text (the legend then tells the rows)."""
    filters = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True).stdout
    return next((f for f in FONTS if os.path.exists(f)), None) if ' drawtext ' in filters else None

def rows_of(clips, places, fps):
    """What the sheets show: [(id, [(label, seconds)] * 3)], the cuts in order, then the B-roll. With more cuts than
    the sheets hold, the cuts inside one take come first."""
    cuts = list(range(1, len(clips)))
    if len(cuts) > MAX_CUTS:
        cuts = sorted(sorted(cuts, key=lambda k: clips[k]['file'] != clips[k - 1]['file'])[:MAX_CUTS])
    rows = []
    for k in cuts:
        c = clips[k]
        at = c['off_s']
        rows.append((f'C{k}', [(f'C{k} before', max(0.0, at - NEAR)), (f'C{k} after', at + min(NEAR, c['dur_s'] / 2)),
                               (f'C{k} middle', at + c['dur_s'] / 2)]))
    for j, p in enumerate(places[:MAX_BROLL], 1):
        at, dur = float(p[0] * timeline.fd(fps)), p[4] - p[3]
        rows.append((f'B{j}', [(f'B{j} start', at + min(0.1, dur / 2)), (f'B{j} middle', at + dur / 2),
                               (f'B{j} end', at + max(dur - 0.1, dur / 2))]))
    return rows

def contact_sheets(proxy, rows, out, prefix, fontfile=None):
    """The pictures of `rows` taken from the proxy and laid out, ROWS rows per sheet: the sheets' paths."""
    tmp = os.path.join(out, 'frames')
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    n = 0
    for _, shots in rows:
        for label, t in shots:
            n += 1
            vf = 'scale=320:-2'
            if fontfile:
                text = f'{label} {mmss(t)}'.replace(':', r'\:')
                vf += (f",drawtext=fontfile='{fontfile}':text='{text}':x=6:y=6:fontsize=15:fontcolor=white:"
                       'box=1:boxcolor=black@0.6:boxborderw=4')
            r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{t:.3f}', '-i', proxy,
                                '-frames:v', '1', '-vf', vf, '-q:v', '4', os.path.join(tmp, f'f_{n:04d}.jpg')],
                               capture_output=True, text=True)
            if r.returncode or not os.path.exists(os.path.join(tmp, f'f_{n:04d}.jpg')):  # past the last frame: the one before
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-sseof', '-0.2', '-i', proxy,
                                '-frames:v', '1', '-vf', vf, '-q:v', '4', os.path.join(tmp, f'f_{n:04d}.jpg')], check=True)
    for old in [f for f in os.listdir(out) if f.startswith(prefix) and f.endswith('.jpg')]:
        os.remove(os.path.join(out, old))
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', os.path.join(tmp, 'f_%04d.jpg'),
                    '-vf', f'tile=3x{ROWS}', '-q:v', '4', os.path.join(out, prefix + '%02d.jpg')], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return sorted(os.path.join(out, f) for f in os.listdir(out) if f.startswith(prefix) and f.endswith('.jpg'))

def cut_short(expected, heard, clips):
    """The sentences of the edit cut short: their first or last word, at the edge of a cut, is not heard in the
    render, and nothing is heard in its place (a word heard as another one is Whisper's doing, not the cut's).
    [{'at': seconds, 'text', 'word'}]. `expected`: timeline_words(source=True); `heard`: the words of the proxy."""
    said = [verify_edit.norm(w['w']) for w in heard if not (w.get('filler') or w.get('suspect'))]
    ops = difflib.SequenceMatcher(a=[verify_edit.norm(w[2]) for w in expected], b=said, autojunk=False).get_opcodes()
    gone = {(w[0], w[2]) for tag, i1, i2, _, _ in ops if tag == 'delete' for w in expected[i1:i2]}

    def at_edge(w, first):
        k = verify_edit.clip_at(clips, w[0])
        if k is None:
            return False
        c = clips[k]
        return w[0] - c['off_s'] < verify_edit.EDGE if first else c['off_s'] + c['dur_s'] - w[1] < verify_edit.EDGE

    out, sentence = [], []
    for i, w in enumerate(expected):
        sentence.append(w)
        if re.search(r'[.?!…]["»)]?$', w[2]) or i == len(expected) - 1:
            for word, first in ((sentence[0], True), (sentence[-1], False)):
                if (word[0], word[2]) in gone and at_edge(word, first):
                    out.append({'at': round(sentence[0][0], 2), 'text': ' '.join(x[2] for x in sentence)[:100], 'word': word[2]})
                    break
            sentence = []
    return out

def broll_order(edl, clips, folder, fps, beats, window):
    """The B-roll of the edit as it plays: [(index in edl["broll"], placement)], B1 first."""
    one = [(i, broll.placements({'broll': [b]}, clips, folder, fps, beats, window)[0]) for i, b in enumerate(edl.get('broll', []))]
    return sorted(one, key=lambda x: x[1][0])

def build_prompt(rows, sheets, clips, scales, ordered, expected, fps, labelled, language):
    legend = []
    for n in range(len(sheets)):
        ids = [r[0] for r in rows[n * ROWS:(n + 1) * ROWS]]
        legend.append(f'Sheet {n + 1}, rows from top to bottom: {", ".join(ids)}')
    cuts = []
    for rid, shots in rows:
        if rid[0] != 'C':
            continue
        k = int(rid[1:])
        same = clips[k]['file'] == clips[k - 1]['file']
        cuts.append(f'{rid} at {mmss(clips[k]["off_s"])}: {"same take" if same else "new shot"}'
                    + (', zoomed' if scales[k] != scales[k - 1] else ''))
    brolls = []
    for j, (_, p) in enumerate(ordered, 1):
        at, dur = float(p[0] * timeline.fd(fps)), p[4] - p[3]
        said = ' '.join(w[2] for w in expected if at - 0.5 <= w[0] <= at + dur)
        brolls.append(f'B{j} at {mmss(at)}, {dur:.1f} s ({p[5] or "no note"}), over: "{said or "(no speech)"}"')
    return PROMPT.format(
        n=len(sheets), legend='\n'.join(legend), cuts='\n'.join(cuts) or '(none)',
        labels=' A label on each picture names its row and its time.' if labelled else '',
        brolls=('\nThe B-roll, and what is said while each plays:\n' + '\n'.join(brolls) + '\n') if brolls else '',
        language={'fr': 'French', 'en': 'English'}.get(language, 'the language of the video'))

def apply(edl, clips, answer, ordered, scales, replan, fps, room=None):
    """The fixes of the review made in `edl`, and what was done: (changed, [{'at', 'kind', 'what', 'done'}]).
    `replan(edl)`: the zoom scales of the edit as it would be, or None when the zooms are off. `room`: how many
    B-roll may still be taken off (default: half of them)."""
    d = answer if isinstance(answer, dict) else {}
    done, changed = [], False

    def note(at, kind, why, did):
        why = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(why or ''))[:60]  # it ends in an XML attribute
        done.append({'at': round(at, 2), 'kind': kind, 'what': why, 'done': did})

    for item in d.get('cuts') if isinstance(d.get('cuts'), list) else []:
        m = re.fullmatch(r'C(\d+)', str(item.get('cut', '')).strip()) if isinstance(item, dict) else None
        kind = item.get('problem') if m else None
        if kind not in ('jump', 'look', 'blur') or not 0 < int(m.group(1)) < len(clips):
            continue
        k = int(m.group(1))
        fixed = False
        if kind == 'jump' and scales is not None and scales[k] == scales[k - 1]:
            edl['clips'][k]['zoom'] = scales[k - 1] == 1  # the scale the shot before does not have
            after = replan(edl)
            fixed = after is not None and after[k] != after[k - 1]
            if not fixed:  # too short for a zoom, or no face to anchor it on
                edl['clips'][k].pop('zoom')
        changed = changed or fixed
        note(clips[k]['off_s'], kind, item.get('why'), 'zoom' if fixed else 'marker')
    by_id = {f'B{j}': (i, p) for j, (i, p) in enumerate(ordered, 1)}
    room = math.ceil(REMOVED_SHARE * len(ordered)) if room is None else room
    gone = []
    for item in d.get('broll') if isinstance(d.get('broll'), list) else []:
        hit = by_id.get(str(item.get('id', '')).strip()) if isinstance(item, dict) else None
        kind = item.get('problem') if hit else None
        if kind not in ('blur', 'misplaced') or hit[0] in gone:
            continue
        take = len(gone) < room
        if take:
            gone.append(hit[0])
        note(float(hit[1][0] * timeline.fd(fps)), kind, item.get('why'), 'removed' if take else 'marker')
    if gone:
        edl['broll'] = [b for i, b in enumerate(edl['broll']) if i not in gone]
        changed = True
    return changed, done

def markers(done, short, t):
    """The to-do markers of what the review left to the editor: one per time and kind."""
    out, seen = [], set()
    for x in [x for x in done if x['done'] == 'marker'] + [dict(s, kind='short', what=s['text']) for s in short]:
        if (x['at'], x['kind']) in seen:
            continue
        seen.add((x['at'], x['kind']))
        out.append({'at': x['at'], 'text': f'{t["label"]} {t[x["kind"]]}' + (f' ({x["what"]})' if x['what'] else ''), 'todo': True})
    return out

def main():
    parser = argparse.ArgumentParser(description='Watches an assembled edit on contact sheets and mends what shows.')
    parser.add_argument('edl', help='edl.json (changed in place)')
    parser.add_argument('--rounds', type=int, help='at most this many looks (default: visual_review.rounds)')
    parser.add_argument('--height', type=int, help='height of the proxy, in lines (default: visual_review.proxy_height)')
    parser.add_argument('--language', default='auto', help='main language of the speech (default: auto)')
    parser.add_argument('--zoom-jump-cuts', action='store_true', help='the edit is made with make_fcpxml.py --zoom-jump-cuts')
    parser.add_argument('--transcripts', help='folder of the clips\' .words.json (default: transcripts/ next to edl.json)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    folder = os.path.dirname(os.path.abspath(args.edl))
    cfg = settings.load(folder, args.config)
    cfg['ui']['language'] = os.environ.get('ROUGHCUT_LANG') or cfg['ui']['language']
    t = TEXT.get(cfg['ui']['language'], TEXT['en'])
    v = cfg['visual_review']
    tdir = args.transcripts or os.path.join(folder, 'transcripts')
    out = os.path.join(folder, 'review')
    os.makedirs(out, exist_ok=True)
    proxy = os.path.join(out, 'proxy.mp4')
    fontfile = font()
    report = {'rounds': []}
    todo, short, room = [], [], None

    def replan(edl):
        if not args.zoom_jump_cuts or edl.get('vertical'):
            return None
        tmp = os.path.join(out, 'edl-try.json')
        with open(tmp, 'w', encoding='utf-8') as f:  # relative paths in it are read from the folder of edl.json
            json.dump(dict(edl, clips=[dict(c, file=os.path.join(folder, os.path.expanduser(c['file']))) for c in edl['clips']]), f)
        try:
            _, tl, _, clips = timeline.build(tmp)
        finally:
            os.remove(tmp)
        analyses = zoom.load_analyses(os.path.join(folder, 'analysis'), {c['file'] for c in clips})
        return [s for s, _, _ in zoom.plan(clips, cfg, analyses, tl['width'], tl['height'])]

    try:
        for n in range(1, (args.rounds or v['rounds']) + 1):
            edl, tl, _, clips = timeline.build(args.edl)
            fps = tl['fps']
            r = subprocess.run([sys.executable, os.path.join(HERE, 'render_preview.py'), args.edl, '-o', proxy,
                                '--height', str(args.height or v['proxy_height']), '--split-edits',
                                *(['--zoom-jump-cuts'] if args.zoom_jump_cuts else []), *(['--config', args.config] if args.config else [])],
                               capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f'Cannot render the proxy: {(r.stderr or r.stdout).strip()[-300:]}')
            music = tracks.plan(edl, clips, tl, folder, cfg)
            ordered = broll_order(edl, clips, folder, fps, music and music['beats'], cfg['music']['snap_window_seconds'])
            rows = rows_of(clips, [p for _, p in ordered], fps)
            expected = timeline_words(clips, tdir, source=True)
            wav = os.path.join(out, 'proxy.wav')
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', proxy, '-vn', '-ac', '1', '-ar', '16000',
                            '-c:a', 'pcm_s16le', wav], check=True)
            try:
                short = cut_short(expected, verify_edit.transcribe(wav, out, args.language), clips)
            finally:
                for f in ('proxy.wav', 'proxy.json', 'proxy.words.json'):
                    if os.path.exists(os.path.join(out, f)):
                        os.remove(os.path.join(out, f))
            round_ = {'cuts': len(clips) - 1, 'shown': sum(1 for rid, _ in rows if rid[0] == 'C'), 'broll': len(ordered),
                      'cut_short': short, 'sheets': [], 'done': []}
            report['rounds'].append(round_)
            print(f'round {n}: {round_["cuts"]} cuts ({round_["shown"]} shown), {len(ordered)} B-roll, {len(short)} sentence(s) cut short')
            if not rows:  # one shot and no B-roll: nothing to look at
                break
            sheets = contact_sheets(proxy, rows, out, f'sheet-{n}-', fontfile)
            round_['sheets'] = [os.path.basename(s) for s in sheets]
            scales = replan(edl)
            prompt = build_prompt(rows, sheets, clips, scales or [1] * len(clips), ordered, expected, fps, bool(fontfile),
                                  cfg['ui']['language'])
            with open(os.path.join(out, f'prompt-{n}.txt'), 'w', encoding='utf-8') as f:
                f.write(prompt)
            try:
                answer, engine, _ = brain.ask_json(prompt, cfg, images=sheets)
            except brain.BrainError as e:  # the edit is worth more than its review
                round_['brain'] = f'none ({e})'
                print(f'round {n}: the brain did not look ({e})')
                break
            with open(args.edl, encoding='utf-8') as f:  # as written, without what build() adds
                edl = json.load(f)
            if room is None:  # half of the B-roll of the edit as it came, over all the rounds
                room = math.ceil(REMOVED_SHARE * len(ordered))
            changed, done = apply(edl, clips, answer, ordered, scales, replan, fps, room)
            room -= sum(1 for x in done if x['done'] == 'removed')
            round_.update(brain=engine, answer=answer, done=done)
            todo = [x for x in todo if x['done'] == 'marker'] + done
            print(f'round {n} ({engine}): ' + (', '.join(f'{x["kind"]} at {mmss(x["at"])}: {x["done"]}' for x in done) or 'nothing to fix'))
            if not changed:
                break
            with open(args.edl, 'w', encoding='utf-8') as f:
                json.dump(edl, f, ensure_ascii=False, indent=1)
    finally:  # what a first round saw is kept when a later one fails
        added = markers(todo, short, t)
        if added:
            with open(args.edl, encoding='utf-8') as f:
                edl = json.load(f)
            edl['markers'] = edl.get('markers', []) + added
            with open(args.edl, 'w', encoding='utf-8') as f:
                json.dump(edl, f, ensure_ascii=False, indent=1)
        report['markers'] = len(added)
        report['fixed'] = sum(1 for r in report['rounds'] for x in r['done'] if x['done'] != 'marker')
        with open(os.path.join(out, 'visual-review.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f'{report["fixed"]} fixed, {len(added)} marker(s) left for the editor; proxy and sheets in {out}')

if __name__ == '__main__':
    main()
