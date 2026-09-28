"""Vertical reframing that follows the face: on a vertical Short made from horizontal footage, the picture fills the
height and only a slice of its width shows. Instead of the centre slice, the slice follows the largest face found by
analyze.py, with keyframes on the position, smoothed and held still while the face moves less than a dead zone, so
the frame never wanders. A cut follows the face only when a face big enough is there most of the time; otherwise it
stays centred.

Positions are in Final Cut Pro's units (1% of the frame height, origin at the centre)."""

def _smooth(values, radius):
    return [sorted(values[max(0, i - radius):i + radius + 1])[len(values[max(0, i - radius):i + radius + 1]) // 2]
            for i in range(len(values))]

def follow(clip, samples, width, height, cfg):
    """[(seconds from the start of the cut, x)] keyframes for one cut, following the face, or the main subject when
    nobody faces the camera; None to leave it centred."""
    r = cfg['reframe']
    a = clip['asset']
    shown = a['width'] * height / a['height']  # width of the source once it fills the frame height
    if shown <= width:
        return None  # nothing to pan across
    start, end = clip['in_s'], clip['in_s'] + clip['dur_s']
    inside = [s for s in samples if start <= s['t'] <= end]
    faced = [(s['t'], max(s['faces'], key=lambda f: f[2] * f[3])) for s in inside
             if any(f[2] * f[3] >= r['min_face_area'] for f in s.get('faces', []))]
    if inside and len(faced) < r['min_face_share'] * len(inside):  # nobody faces the camera: the main subject
        faced = [(s['t'], s['subject']) for s in inside if s.get('subject') and s['subject'][2] < 0.9]
    if not inside or len(faced) < r['min_face_share'] * len(inside):
        return None
    times = [t for t, _ in faced]
    xs = _smooth([f[0] + f[2] / 2 for _, f in faced], 2)  # a median over five samples drops a stray detection
    limit = (shown - width) / 2  # beyond this, the frame would show past the edge of the picture
    px_per_unit = height / 100
    def units(x):
        move = -(x - 0.5) * shown  # pixels that bring the face to the centre
        return round(max(-limit, min(limit, move)) / px_per_unit, 2)
    keys, held = [], None
    for t, x in zip(times, xs):
        at = round(max(0.0, t - start), 3)
        if held is None:
            keys.append((0.0, units(x)))
        elif abs(x - held) * shown > r['dead_zone'] * width:
            # hold still until just before the move, then move in move_seconds: no slow drift between keyframes
            hold = round(max(keys[-1][0], at - r['move_seconds']), 3)
            if hold > keys[-1][0]:
                keys.append((hold, keys[-1][1]))
            keys.append((at, units(x)))
        else:
            continue
        held = x
    keys.append((round(clip['dur_s'], 3), keys[-1][1]))  # hold the last position to the end of the cut
    return keys

def crop_x(keys, width, height, src_w, src_h):
    """ffmpeg expression of the crop's x, in the scaled picture, that shows what the keyframes show (linear in between)."""
    shown = src_w * height / src_h
    def x(u):
        return (shown - width) / 2 - u * height / 100
    expr = f'{x(keys[-1][1]):.2f}'
    for (t0, u0), (t1, u1) in reversed(list(zip(keys, keys[1:]))):
        if t1 <= t0:
            continue
        expr = f'if(lt(t,{t1:.3f}),{x(u0):.2f}+({x(u1) - x(u0):.2f})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})'
    return expr
