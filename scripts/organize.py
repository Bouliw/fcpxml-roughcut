#!/usr/bin/env python3
"""Logs the footage into a Final Cut Pro event, ready to browse: every clip comes with keyword ranges ("Face cam"
where someone speaks to a detected face, "B-roll" where nobody speaks, "Place" from the GPS tag when the clip has
one), rejected ranges (blurred, shaky, black...), favourite ranges (the best B-roll moments) and markers on the
thumbnail candidates. It reads what analyze.py and the transcripts found; nothing is decided for the editor, who
can show or hide rejected ranges in the browser.
Usage: organize.py clip [clip ...] -o logged.fcpxml [--analysis dir] [--transcripts dir] [--event name] [--open]"""
import argparse, json, os, re, sys
from xml.sax.saxutils import quoteattr as qa
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline
from timeline import ffprobe, rt, frames, disk_path, transcript_path
from make_fcpxml import Resources, VERSIONS
import fcp, settings

def speech(path, tdir, gap=1.0):
    """[[start, end]] where words are said, or None without a transcript."""
    p = transcript_path(tdir, path) if tdir else None
    if not p or not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        words = [w for w in json.load(f)['words'] if not w.get('suspect') and not w.get('filler')]
    out = []
    for w in words:
        if out and w['start'] - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], w['end'])
        else:
            out.append([w['start'], w['end']])
    return out

def merge(times, step, shortest):
    """Sample times -> [[start, end]] where consecutive samples are within 1.5 steps, at least `shortest` long."""
    out = []
    for t in times:
        if out and t - out[-1][1] <= 1.5 * step:
            out[-1][1] = t + step
        else:
            out.append([t, t + step])
    return [r for r in out if r[1] - r[0] >= shortest]

def gaps(spans, duration, shortest):
    """What [[start, end]] leave free in [0, duration], pieces of at least `shortest`."""
    out, pos = [], 0.0
    for a, b in sorted(spans):
        if a - pos >= shortest:
            out.append([pos, a])
        pos = max(pos, b)
    if duration - pos >= shortest:
        out.append([pos, duration])
    return out

def place(path):
    """(latitude, longitude) from the clip's GPS tag (ISO 6709, as phones and some cameras write it), or None."""
    tags = ffprobe(path).get('format', {}).get('tags', {}) or {}
    for k, v in tags.items():
        if 'location' in k.lower():
            m = re.match(r'([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)', v)
            if m:
                return float(m.group(1)), float(m.group(2))
    return None

def main():
    parser = argparse.ArgumentParser(description='Logs the footage into a Final Cut Pro event with keyword ranges, ratings and markers.')
    parser.add_argument('clips', nargs='+')
    parser.add_argument('-o', required=True, dest='out', help='FCPXML file to write')
    parser.add_argument('--analysis', help='folder of the analyze.py results (default: analysis/ next to the output)')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ next to the output)')
    parser.add_argument('--event', help='event name in Final Cut Pro')
    parser.add_argument('--fcpxml-version', choices=VERSIONS, default='1.13')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    parser.add_argument('--open', action='store_true', help='open the file in Final Cut Pro, which starts the import')
    args = parser.parse_args()
    folder = os.path.dirname(os.path.abspath(args.out))
    cfg = settings.load(folder, args.config)
    o, names = cfg['organize'], cfg['organize']['keywords']
    adir = args.analysis or os.path.join(folder, 'analysis')
    tdir = args.transcripts or os.path.join(folder, 'transcripts')
    res, items, report = Resources(), [], []
    for clip in args.clips:
        path = disk_path(os.path.abspath(clip))
        a = timeline.probe(path)
        aid = res.asset(a)
        fps = a['fps']
        at = lambda t: rt(a['start_f'] + frames(t, fps), fps)  # noqa: E731  seconds in the clip -> clip time
        span = lambda t0, t1: f'start="{at(t0)}" duration="{rt(max(1, frames(t1, fps) - frames(t0, fps)), fps)}"'  # noqa: E731
        base = os.path.splitext(os.path.basename(path))[0]
        analysis = None
        apath = os.path.join(adir, base + '.analysis.json')
        if os.path.exists(apath):
            with open(apath, encoding='utf-8') as f:
                analysis = json.load(f)
        talk = speech(path, tdir)
        inner, counts = [], {}

        def add(line, what):
            inner.append(line)
            counts[what] = counts.get(what, 0) + 1
        end = a['duration']
        if talk is not None and analysis:
            step = 1 / cfg['quality']['sample_fps']
            faced = [s['t'] for s in analysis['samples'] if any(f[2] * f[3] >= o['face_cam_min_area'] for f in s.get('faces', []))
                     and any(x - 0.5 <= s['t'] <= y + 0.5 for x, y in talk)]
            for x, y in merge(faced, step, o['min_range_seconds']):
                add(f'      <keyword {span(x, min(y, end))} value={qa(names["face_cam"])}/>', 'face cam')
        if talk is not None:
            for x, y in gaps(talk, end, o['min_range_seconds']):
                add(f'      <keyword {span(x, y)} value={qa(names["broll"])}/>', 'B-roll')
        gps = place(path)
        if gps:
            label = f'{names["place"]} {gps[0]:.2f}, {gps[1]:.2f}'  # about 1 km: clips shot at one place share a keyword
            add(f'      <keyword {span(0, end)} value={qa(label)} note={qa(f"{gps[0]:.5f}, {gps[1]:.5f}")}/>', 'place')
        if analysis:
            for r in analysis['rejects']:
                add(f'      <rating {span(r["from"], min(r["to"], end))} value="reject" note={qa(", ".join(r["why"]))}/>', 'rejected')
            for m in analysis['broll']:
                add(f'      <rating {span(m["from"], min(m["to"], end))} value="favorite" note={qa("best B-roll")}/>', 'favourite')
            for th in analysis['thumbnails']:
                add(f'      <marker start="{at(th["t"])}" duration="{rt(1, fps)}" value={qa("Thumbnail candidate")}/>', 'marker')
        role = f' audioRole={qa(cfg["roles"]["default_audio"])}' if a['audio_channels'] else ''
        head = (f'    <asset-clip ref="{aid}" name={qa(base)} start="{at(0)}" duration="{rt(a["dur_f"], fps)}" '
                f'format="{a["format_id"]}" tcFormat="{a["tcf"]}"{role}')
        items.append(head + ('>\n' + '\n'.join(inner) + '\n    </asset-clip>' if inner else '/>'))
        missing = [w for w, ok in (('transcript', talk is not None), ('analysis', analysis is not None)) if not ok]
        report.append(f'{base}: ' + (', '.join(f'{n} {w}' for w, n in counts.items()) or 'nothing found') +
                      (f'  (no {" or ".join(missing)})' if missing else ''))
    event = args.event or f'{o["event_prefix"]}{os.path.basename(folder)}'
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="{args.fcpxml_version}">
  <resources>
{chr(10).join(res.lines)}
  </resources>
  <library>
    <event name={qa(event)}>
{chr(10).join(items)}
    </event>
  </library>
</fcpxml>
'''
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(xml)
    print(f'{args.out}: event "{event}", {len(items)} clips')
    print('\n'.join('  ' + r for r in report))
    ok, msg = fcp.check(args.out)
    print(f'\nDTD check: {msg}')
    if ok is False:
        raise SystemExit('The FCPXML does not validate: Final Cut Pro would refuse it.')
    if args.open:
        print(f'Opened in {os.path.basename(fcp.open_in_fcp(args.out))[:-4]}: the import starts there.')

if __name__ == '__main__':
    main()
