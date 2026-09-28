#!/usr/bin/env python3
"""Footage inventory: duration, resolution, exact frame rate, codec, audio, timecode.
Usage: probe.py file1 [file2 ...] [--json output.json]"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import ffprobe, video_stream, timecode, frame_rate, display_size

def probe(path):
    d = ffprobe(path)
    v = video_stream(d)
    a = [s for s in d['streams'] if s.get('codec_type') == 'audio']
    info = {'file': os.path.abspath(path), 'duration': float(d['format'].get('duration', 0)), 'timecode': timecode(d)}
    if v:
        fps = frame_rate(v)
        w, h, rot = display_size(v)
        info.update({'width': w, 'height': h, 'fps': f'{fps.numerator}/{fps.denominator}', 'fps_float': round(float(fps), 3),
                     'vcodec': v.get('codec_name'), 'rotation': rot, 'color_transfer': v.get('color_transfer')})
    info['audio'] = [{'index': s['index'], 'codec': s.get('codec_name'), 'channels': s.get('channels'),
                      'rate': int(s.get('sample_rate', 48000))} for s in a]
    return info

def main():
    parser = argparse.ArgumentParser(description='Footage inventory: duration, resolution, exact frame rate, codec, audio, timecode.')
    parser.add_argument('files', nargs='+', help='clips to describe')
    parser.add_argument('--json', dest='out', help='also write the inventory to this JSON file')
    args = parser.parse_args()
    res = [probe(p) for p in args.files]
    for r in res:
        m, s = divmod(r['duration'], 60)
        print(f"{os.path.basename(r['file'])}: {int(m)}:{s:05.2f} | {r.get('width')}x{r.get('height')} "
              f"@ {r.get('fps_float')} ({r.get('fps')}) | {r.get('vcodec')} | {len(r['audio'])} audio track(s) | tc {r['timecode']}")
    rates = {(r.get('width'), r.get('height'), r.get('fps')) for r in res}
    if len(rates) > 1:
        print('WARNING: the files do not share the same format:', rates)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=2)

if __name__ == '__main__':
    main()
