#!/usr/bin/env python3
"""Footage inventory: duration, resolution, exact frame rate, codec, audio, timecode.
Usage: probe.py file1 [file2 ...] [--json output.json]"""
import json, subprocess, sys, os
from fractions import Fraction

def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next((s for s in d['streams'] if s.get('codec_type') == 'video'), None)
    a = [s for s in d['streams'] if s.get('codec_type') == 'audio']
    tc = (d.get('format', {}).get('tags', {}) or {}).get('timecode')
    for s in d['streams']:
        tc = tc or (s.get('tags', {}) or {}).get('timecode')
    info = {'file': os.path.abspath(path), 'duration': float(d['format'].get('duration', 0)), 'timecode': tc}
    if v:
        fps = Fraction(v.get('avg_frame_rate') or v.get('r_frame_rate'))
        if fps == 0:
            fps = Fraction(v.get('r_frame_rate'))
        rot = 0
        for sd in v.get('side_data_list', []) or []:
            if 'rotation' in sd:
                rot = int(sd['rotation'])
        w, h = int(v['width']), int(v['height'])
        if abs(rot) in (90, 270):
            w, h = h, w
        info.update({'width': w, 'height': h, 'fps': f'{fps.numerator}/{fps.denominator}', 'fps_float': round(float(fps), 3),
                     'vcodec': v.get('codec_name'), 'rotation': rot, 'color_transfer': v.get('color_transfer')})
    info['audio'] = [{'index': s['index'], 'codec': s.get('codec_name'), 'channels': s.get('channels'),
                      'rate': int(s.get('sample_rate', 48000))} for s in a]
    return info

def main():
    args = sys.argv[1:]
    out = None
    if '--json' in args:
        i = args.index('--json'); out = args[i + 1]; del args[i:i + 2]
    res = [probe(p) for p in args]
    for r in res:
        m, s = divmod(r['duration'], 60)
        print(f"{os.path.basename(r['file'])}: {int(m)}:{s:05.2f} | {r.get('width')}x{r.get('height')} "
              f"@ {r.get('fps_float')} ({r.get('fps')}) | {r.get('vcodec')} | {len(r['audio'])} audio track(s) | tc {r['timecode']}")
    rates = {(r.get('width'), r.get('height'), r.get('fps')) for r in res}
    if len(rates) > 1:
        print('WARNING: the files do not share the same format:', rates)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        json.dump(res, open(out, 'w'), indent=2)

if __name__ == '__main__':
    main()
