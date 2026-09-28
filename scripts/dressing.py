"""The dressing of the main edit, all of it native to Final Cut Pro so it stays editable there: a title where each
chapter starts, the time of day when the story moves on, a lower third where each person interviewed first answers
(placed, with its text left to type) and a cross dissolve where a chapter starts. auto.dressing picks them: "none",
"light" (chapter titles and dissolves) or "full" (all of them, and the animated captions of the main edit).

The titles use Final Cut Pro's own templates with their own look (only the text and the place are set), and a
dissolve is only put where both clips have the media it needs around the cut."""
import datetime, json, os, re, subprocess

LEVELS = {'none': set(), 'light': {'titles', 'fades'}, 'full': {'titles', 'fades', 'times', 'lower_thirds', 'captions'}}

# Final Cut Pro's templates, as its own FCPXML names them ("..." is the folder of the templates it ships)
TITLE = ('Basic Title', '.../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti')
LOWER_THIRD = ('Basic Lower Third', '.../Titles.localized/Lower Thirds.localized/Basic Lower Third.localized/Basic Lower Third.moti')
CROSS_DISSOLVE = ('Cross Dissolve', 'FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265')

PLACEHOLDERS = {'fr': ('Prénom Nom', 'Pays'), 'en': ('Name', 'Country')}

def features(level):
    return LEVELS.get(level, LEVELS['light'])

def _probe_date(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags=com.apple.quicktime.creationdate',
                            '-of', 'json', path], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout or '{}').get('format', {}).get('tags', {}).get('com.apple.quicktime.creationdate')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None

def recorded_at(path, probe=_probe_date):
    """The wall-clock time the clip starts, where it was filmed, or None. From the date an iPhone writes with its time
    zone, else from a name like DJI_20260115101520_0001 or VID_20260115_101520 (the camera's clock). The UTC date of
    other cameras is left out: the time zone of the place is not known."""
    stamp = probe(path)
    m = re.match(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)', stamp or '')
    if not m:
        m = re.search(r'(20\d\d)(\d\d)(\d\d)_?(\d\d)(\d\d)(\d\d)', os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.datetime(*map(int, m.groups()))
    except ValueError:
        return None

def time_text(t, lang):
    return f'{t.hour}h{t.minute:02d}' if lang == 'fr' else f'{t.hour % 12 or 12}:{t.minute:02d} {"AM" if t.hour < 12 else "PM"}'

def story_start(clips):
    """The first clip after the hook."""
    return next((i for i, c in enumerate(clips) if c.get('note') != 'hook'), len(clips))

def time_labels(clips, cfg, lang, when=recorded_at):
    """[(clip index, text)]: the time of day at the start of the story and with each chapter filmed 10 minutes or
    more after the last time shown; then wherever the clock jumps by time_gap_minutes, if no other time shows within
    20 s of the edit."""
    first, starts, times = story_start(clips), {}, {}
    for i in range(first, len(clips)):
        c = clips[i]
        if c['file'] not in starts:
            starts[c['file']] = when(c['file'])
        if starts[c['file']]:
            times[i] = starts[c['file']] + datetime.timedelta(seconds=c['in_s'])
    out, last = {}, None
    for i in sorted(times):
        if last is None or (clips[i].get('chapter') and abs(times[i] - last) >= datetime.timedelta(minutes=10)):
            out[i] = last = times[i]
    gap = datetime.timedelta(minutes=cfg['time_gap_minutes'])
    for i in sorted(times):
        before = [j for j in out if j < i]
        if i in out or not before or abs(times[i] - out[max(before)]) < gap:
            continue
        if all(abs(clips[i]['off_s'] - clips[j]['off_s']) >= 20 for j in out):
            out[i] = times[i]
    return [(i, time_text(out[i], lang)) for i in sorted(out)]

def lower_thirds(clips, interviews):
    """[(clip index, seconds into it, who)]: where each person interviewed first answers, on the first clip of the
    edit that plays their answer (or the first one of that clip after it)."""
    out = []
    first = story_start(clips)
    for iv in interviews:
        for i in range(first, len(clips)):
            c = clips[i]
            if c['file'] == iv['file'] and c['in_s'] + c['dur_s'] > iv['at']:
                out.append((i, max(0.0, iv['at'] - c['in_s']), iv.get('who', '')))
                break
    return out

def dissolves(clips, seconds, fps, shortest=0.3):
    """{index of the clip a chapter starts on: frames of the dissolve that leads into it}, after the hook's cut. A
    dissolve takes half its length of media past the end of the clip before and before the start of this one: where
    there is less, it is shorter, down to `shortest` seconds; below, the cut stays. Never longer than a third of either
    clip."""
    out = {}
    for i in range(story_start(clips) + 1, len(clips)):
        a, b = clips[i - 1], clips[i]
        if not b.get('chapter'):
            continue
        # the media the clip really holds: its frames, as Final Cut Pro counts them (a file that dropped frames is
        # shorter than its duration says)
        asset = a['asset']
        held = float(asset['dur_f'] / asset['fps']) if asset.get('dur_f') else asset['duration']
        room = min(held - 2 / fps - (a['in_s'] + a['dur_s']), b['in_s'] - 1 / fps, a['dur_s'] / 3, b['dur_s'] / 3)
        n = 2 * min(round(seconds / 2 * fps), int(room * fps))  # an even number of frames, centred on the cut
        if n >= shortest * fps:
            out[i] = n
    return out

def title_xml(ref, lane, offset, duration, name, texts, position=None, scale=None, indent='          '):
    """A title from a template: its texts in the template's own style, moved and scaled if asked."""
    from xml.sax.saxutils import escape, quoteattr
    body = ''.join(f'\n{indent}  <text>{escape(t)}</text>' for t in texts)
    if position or scale:
        body += (f'\n{indent}  <adjust-transform' + (f' position="{position[0]:g} {position[1]:g}"' if position else '')
                 + (f' scale="{scale:g} {scale:g}"' if scale else '') + '/>')
    return (f'{indent}<title ref="{ref}" lane="{lane}" offset="{offset}" name={quoteattr(name)} duration="{duration}">'
            f'{body}\n{indent}</title>')
