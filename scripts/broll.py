#!/usr/bin/env python3
"""B-roll: a catalogue of the shots to cut away to, and the placement of the chosen ones over the edit.

Catalogue (this script): every stretch of a clip where nobody speaks, nobody faces the camera most of the time and nothing
was rejected by analyze.py, at least min_seconds long, with the scene labels Apple Vision saw, the aesthetic score, and a picture of its best-looking
moment. Whoever edits (an AI agent, or you) looks at the pictures, fills in "description" and "tags", and matches
the shots to what the transcript says.
Usage: broll.py clip [clip ...] -o broll.json [--analysis dir] [--transcripts dir] [--frames dir]

Placement (make_fcpxml.py and render_preview.py read "broll" in edl.json): each chosen shot becomes a connected
clip above the talking head, its sound turned down (-96 dB, Effects role, so it can be brought back), with a marker
so every placement gets checked:
  "broll": [{"file": "footage/C0012.MP4", "in": 3.0, "out": 7.5, "at_source": 245.3, "note": "the lake"}]
"at_source" is a time in the talking-head clip, read in its transcript ("source" names that clip when several are
used); "at" is a time on the edited timeline instead."""
import argparse, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline
from timeline import disk_path, frames
import settings

def snap(t, grid, window):
    """The beat nearest to t when one is within window seconds, t otherwise."""
    near = min(grid, key=lambda b: abs(b - t), default=None)
    return near if near is not None and abs(near - t) <= window else t

def placements(edl, clips, base, fps, beats=None, window=0.35):
    """[(timeline frame, parent cut index, source path, in seconds, out seconds, note, lane)] for edl["broll"]. With
    beats (timeline seconds), each shot starts and ends on the nearest beat within `window` seconds."""
    out = []
    for b in edl.get('broll', []):
        if not all(k in b for k in ('file', 'in', 'out')) or ('at' not in b and 'at_source' not in b):
            raise SystemExit(f'Each B-roll needs "file", "in", "out" and "at" (timeline) or "at_source" (talking-head clip): {b}')
        path = disk_path(os.path.abspath(os.path.join(base, os.path.expanduser(b['file']))))
        if 'at' in b:
            at_f = frames(float(b['at']), fps)
        else:
            src = disk_path(os.path.abspath(os.path.join(base, os.path.expanduser(b['source'])))) if b.get('source') else None
            t = float(b['at_source'])
            hits = [c for c in clips if (src is None or c['file'] == src) and c['in_s'] - 0.05 <= t <= c['in_s'] + c['dur_s'] + 0.05]
            if not hits:
                raise SystemExit(f'B-roll "{b.get("note", b["file"])}": {t} s of the talking head is not in the edit (it was cut out)')
            c = hits[0]
            at_f = c['off_f'] + frames(max(0.0, t - c['in_s']), fps)
        b_in, b_out = float(b['in']), float(b['out'])
        if beats:
            start = snap(float(at_f * timeline.fd(fps)), beats, window)
            end = snap(start + b_out - b_in, beats, window)
            if end - start >= 0.5:
                b_out = b_in + end - start
            at_f = frames(start, fps)
        parent = max(i for i, c in enumerate(clips) if c['off_f'] <= at_f) if at_f >= 0 else 0
        out.append([at_f, parent, path, b_in, b_out, b.get('note', ''), 1])
    # lanes: a B-roll that starts before the previous one ends goes one lane up
    out.sort(key=lambda p: p[0])
    ends = []
    for p in out:
        dur_f = frames(p[4] - p[3], fps)
        lane = next((i + 1 for i, e in enumerate(ends) if e <= p[0]), len(ends) + 1)
        if lane > len(ends):
            ends.append(0)
        ends[lane - 1] = p[0] + dur_f
        p[6] = lane
    return [tuple(p) for p in out]

def speech(path, tdir):
    p = timeline.transcript_path(tdir, path)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return [(w['start'], w['end']) for w in json.load(f)['words'] if not w.get('suspect')]

def main():
    parser = argparse.ArgumentParser(description='Catalogue of the B-roll shots, to describe and place over the edit.')
    parser.add_argument('clips', nargs='+')
    parser.add_argument('-o', required=True, dest='out', help='broll.json to write')
    parser.add_argument('--analysis', help='folder of the analyze.py results (default: analysis/ next to the output)')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files (default: transcripts/ next to the output)')
    parser.add_argument('--frames', help='folder for the pictures (default: broll_frames/ next to the output)')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    folder = os.path.dirname(os.path.abspath(args.out))
    cfg = settings.load(folder, args.config)
    b = cfg['broll']
    adir = args.analysis or os.path.join(folder, 'analysis')
    tdir = args.transcripts or os.path.join(folder, 'transcripts')
    fdir = args.frames or os.path.join(folder, 'broll_frames')
    os.makedirs(fdir, exist_ok=True)
    shots, n, talking = [], 0, 0
    for clip in args.clips:
        path = disk_path(os.path.abspath(clip))
        base = os.path.splitext(os.path.basename(path))[0]
        apath = os.path.join(adir, base + '.analysis.json')
        if not os.path.exists(apath):
            raise SystemExit(f'No analysis for {base}: run analyze.py first ({apath})')
        with open(apath, encoding='utf-8') as f:
            analysis = json.load(f)
        words = speech(path, tdir) or []
        rejects = [(r['from'], r['to']) for r in analysis['rejects']]
        step = 1 / cfg['quality']['sample_fps']
        free = [s for s in analysis['samples']
                if not any(a - b['speech_margin'] <= s['t'] <= e + b['speech_margin'] for a, e in words)
                and not any(a <= s['t'] <= e for a, e in rejects)]
        runs = []
        for s in free:
            if runs and s['t'] - runs[-1][-1]['t'] <= 1.5 * step:
                runs[-1].append(s)
            else:
                runs.append([s])
        for run in runs:
            start, end = run[0]['t'], run[-1]['t'] + step
            if end - start < b['min_seconds']:
                continue
            big = sum(1 for s in run if any(f[2] * f[3] >= cfg['organize']['face_cam_min_area'] for f in s.get('faces', [])))
            if big >= b['max_face_share'] * len(run):
                talking += 1  # someone facing the camera most of the time: an interview whose speech was not transcribed
                continue
            n += 1
            best = max(run, key=lambda s: s.get('aesthetic', 0))
            counts = {}
            for s in run:
                for label in s.get('labels', {}):
                    counts[label] = counts.get(label, 0) + 1
            labels = [l for l, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= len(run) / 3][:6]
            jpg = os.path.join(fdir, f'B{n:03d}_{base}_{best["t"]:.1f}.jpg')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{best["t"]:.3f}', '-i', path, '-frames:v', '1',
                            '-vf', 'scale=640:-2', '-q:v', '3', jpg], check=True)
            shots.append({'id': f'B{n:03d}', 'file': path, 'from': round(start, 2), 'to': round(end, 2),
                          'best': best['t'], 'labels': labels,
                          'aesthetic': round(sum(s.get('aesthetic', 0) for s in run) / len(run), 3),
                          'faces': sum(1 for s in run if s.get('faces')) / len(run) >= 0.5,
                          'frame': jpg, 'description': '', 'tags': []})
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump({'shots': shots}, f, ensure_ascii=False, indent=1)
    total = sum(s['to'] - s['from'] for s in shots)
    print(f'{args.out}: {len(shots)} B-roll shots, {total / 60:.1f} min, pictures in {fdir}')
    if talking:
        print(f'Left out: {talking} stretches with a face to camera most of the time (speech the transcript missed?)')
    print('Next: look at each picture, fill in "description" and "tags", then add the chosen shots to "broll" in edl.json.')

if __name__ == '__main__':
    main()
