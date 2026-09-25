#!/usr/bin/env python3
"""Builds a frame-accurate FCPXML (1.10) timeline from edl.json.
Usage: make_fcpxml.py edl.json -o project.fcpxml"""
import sys, os
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import quoteattr as qa
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import build, rt, fd, frames, tc_frames

NAMES = {Fraction(24000, 1001): '2398', Fraction(24): '24', Fraction(25): '25', Fraction(30000, 1001): '2997',
         Fraction(30): '30', Fraction(50): '50', Fraction(60000, 1001): '5994', Fraction(60): '60'}
STD_SIZES = {(3840, 2160), (1920, 1080), (1280, 720), (4096, 2160)}

def fmt_name(w, h, fps):
    if (w, h) in STD_SIZES and Fraction(fps) in NAMES:
        return f' name="FFVideoFormat{w}x{h}p{NAMES[Fraction(fps)]}"'
    return ''

def mmss(sec):
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def main():
    edl_path = sys.argv[1]
    out = sys.argv[sys.argv.index('-o') + 1]
    edl, tl, assets, clips = build(edl_path)
    fps = tl['fps']
    res, fmt_ids, asset_ids = [], {}, {}
    rid = 1

    def fmt_id(w, h, f):
        nonlocal rid
        key = (w, h, Fraction(f))
        if key not in fmt_ids:
            fmt_ids[key] = f'r{rid}'; rid += 1
            res.append(f'    <format id="{fmt_ids[key]}"{fmt_name(w, h, f)} frameDuration="{rt(1, f)}" width="{w}" height="{h}" colorSpace="1-1-1 (Rec. 709)"/>')
        return fmt_ids[key]

    seq_fmt = fmt_id(tl['width'], tl['height'], fps)
    for a in assets:
        af = fmt_id(a['width'], a['height'], a['fps'])
        start_f, drop = tc_frames(a['timecode'], a['fps'])
        a['start_f'], a['tcf'] = start_f, 'DF' if drop else 'NDF'
        dur_f = frames(a['duration'], a['fps'])
        aid = f'r{rid}'; rid += 1
        asset_ids[a['file']] = aid
        audio = (f' hasAudio="1" audioSources="1" audioChannels="{a["audio_channels"]}" audioRate="{a["audio_rate"]}"'
                 if a['audio_channels'] else '')
        res.append(f'    <asset id="{aid}" name={qa(os.path.splitext(os.path.basename(a["file"]))[0])} start="{rt(start_f, a["fps"])}" '
                   f'duration="{rt(dur_f, a["fps"])}" hasVideo="1" format="{af}"{audio}>\n'
                   f'      <media-rep kind="original-media" src="file://{quote(a["file"])}"/>\n    </asset>')

    spine, chapters = [], []
    tl_markers = sorted(edl.get('markers', []), key=lambda m: m['at'])
    for c in clips:
        a = c['asset']
        src_start_f = a['start_f'] + c['in_f']  # in asset frames
        src_start = src_start_f * fd(a['fps'])
        inner = []
        if edl.get('vertical'):
            inner.append('          <adjust-conform type="fill"/>')
        def src_time(t_tl_f):  # timeline time (frames) -> source rational time, snapped to the asset frame grid
            t = src_start + (t_tl_f - c['off_f']) * fd(fps)
            n = round(t / fd(a['fps']))
            return rt(n, a['fps'])
        if c.get('chapter'):
            inner.append(f'          <chapter-marker start="{rt(src_start_f, a["fps"])}" duration="{rt(1, a["fps"])}" value={qa(c["chapter"])} posterOffset="0s"/>')
            chapters.append((c['off_s'], c['chapter']))
        if c.get('marker'):
            inner.append(f'          <marker start="{rt(src_start_f, a["fps"])}" duration="{rt(1, a["fps"])}" value={qa(c["marker"])}/>')
        for m in tl_markers:
            mf = frames(m['at'], fps)
            if c['off_f'] <= mf < c['off_f'] + c['dur_f']:
                inner.append(f'          <marker start="{src_time(mf)}" duration="{rt(1, a["fps"])}" value={qa(m["text"])}/>')
        name = os.path.splitext(os.path.basename(c['file']))[0]
        body = ('>\n' + '\n'.join(inner) + '\n        </asset-clip>') if inner else '/>'
        spine.append(f'        <asset-clip ref="{asset_ids[c["file"]]}" offset="{rt(c["off_f"], fps)}" name={qa(name)} '
                     f'start="{rt(src_start_f, a["fps"])}" duration="{rt(c["dur_f"], fps)}" tcFormat="{a["tcf"]}"{body}')

    project = edl.get('project', 'Rough cut')
    rate = assets[0]['audio_rate']
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
{chr(10).join(res)}
  </resources>
  <library>
    <event name={qa("Rough cut – " + project)}>
      <project name={qa(project)}>
        <sequence format="{seq_fmt}" duration="{rt(tl['total_f'], fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="{'48k' if rate == 48000 else '44.1k'}">
          <spine>
{chr(10).join(spine)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
'''
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, 'w', encoding='utf-8').write(xml)
    total = float(tl['total_f'] * fd(fps))
    print(f'{out}\nTotal duration: {mmss(total)} ({total:.2f} s, {tl["total_f"]} frames at {float(fps):.3f} fps) | {len(clips)} clips | '
          f'format {tl["width"]}x{tl["height"]}')
    if chapters:
        print('\nYouTube chapters:')
        for t, title in chapters:
            print(f'{mmss(t)} {title}')
        issues = []
        if chapters[0][0] > 0.5: issues.append('the first chapter must start at 0:00')
        if len(chapters) < 3: issues.append('YouTube needs at least 3 chapters')
        ends = [t for t, _ in chapters[1:]] + [total]
        if any(e - t < 10 for (t, _), e in zip(chapters, ends)): issues.append('each chapter must last at least 10 s')
        if issues: print('WARNING, chapters:', '; '.join(issues))

if __name__ == '__main__':
    main()
