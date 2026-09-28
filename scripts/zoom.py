"""Punch-in zooms on jump cuts: along a run of back-to-back cuts from the same source (one take, one framing), every
other cut is scaled up (1.12 by default), so the cut reads as a change of shot instead of a jump. The zoom is
anchored on the face: the frame moves so the face stays where it was. No zoom where the source changes.

Face positions come from analyze.py (<clip>.analysis.json). A cut without a face is left alone: the zoom is there to
hide a jump on someone talking, and a centred zoom on scenery only looks like a mistake. Positions are in Final Cut
Pro's units: 1 unit = 1% of the frame height, origin at the centre, y up."""
import json, os

def load_analyses(folder, files):
    out = {}
    for f in files:
        p = os.path.join(folder, os.path.splitext(os.path.basename(f))[0] + '.analysis.json') if folder else None
        if p and os.path.exists(p):
            with open(p, encoding='utf-8') as fh:
                out[f] = json.load(fh)['samples']
    return out

def face_centre(samples, start, end):
    """Median centre of the largest face over [start, end] of the source, in normalised coordinates (y up)."""
    xs, ys = [], []
    for s in samples:
        if start <= s['t'] <= end and s.get('faces'):
            x, y, w, h = max(s['faces'], key=lambda f: f[2] * f[3])[:4]
            xs.append(x + w / 2)
            ys.append(y + h / 2)
    if not xs:
        return None
    xs.sort(), ys.sort()
    return xs[len(xs) // 2], ys[len(ys) // 2]

def plan(clips, cfg, analyses, width, height, vertical=False):
    """[(scale, (x, y), note)] per clip: scale 1 and no move for the cuts left alone."""
    z = cfg['zoom']
    out, run_file, n = [], None, 0
    for c in clips:
        n = n + 1 if c['file'] == run_file else 0
        run_file = c['file']
        a = c['asset']
        same_shape = (a['width'], a['height']) == (width, height)
        if vertical or n % 2 == 0 or c['dur_s'] < z['min_cut_seconds'] or not same_shape or c.get('role', 'dialogue') != 'dialogue':
            out.append((1, (0.0, 0.0), None))
            continue
        s = z['scale']
        centre = face_centre(analyses.get(c['file'], []), c['in_s'], c['in_s'] + c['dur_s'])
        if centre is None:
            out.append((1, (0.0, 0.0), 'no face found'))
            continue
        # face position in Final Cut Pro units, then the move that keeps it in place once scaled up
        px, py = (centre[0] - 0.5) * 100 * width / height, (centre[1] - 0.5) * 100
        out.append((s, (round(-(s - 1) * px, 2), round(-(s - 1) * py, 2)), 'on the face'))
    return out

def crop_filter(scale, move, width, height):
    """ffmpeg filter showing what the zoom shows in Final Cut Pro, for the preview (empty when no zoom)."""
    if scale == 1:
        return ''
    # the centre of the visible window, in source pixels from the frame centre (y down for ffmpeg)
    cx, cy = -move[0] / scale * height / 100, move[1] / scale * height / 100
    return (f"crop=w=iw/{scale}:h=ih/{scale}:x=(iw-iw/{scale})/2+{cx:.2f}*iw/{width}:"
            f"y=(ih-ih/{scale})/2+{cy:.2f}*ih/{height},")
