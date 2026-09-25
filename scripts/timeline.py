"""Shared helpers: FCPXML rational time, timecode, and the frame-accurate timeline built from edl.json."""
import json, os, subprocess
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
    fps = Fraction(v.get('avg_frame_rate')) if v and v.get('avg_frame_rate') not in (None, '0/0') else Fraction(v['r_frame_rate'])
    rot = 0
    for sd in (v.get('side_data_list') or []):
        if 'rotation' in sd:
            rot = int(sd['rotation'])
    w, h = int(v['width']), int(v['height'])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {'file': os.path.abspath(path), 'duration': float(d['format']['duration']), 'fps': snap_fps(fps),
            'width': w, 'height': h, 'timecode': tc,
            'audio_channels': a[0].get('channels', 2) if a else 0, 'audio_rate': int(a[0].get('sample_rate', 48000)) if a else 48000}

STANDARD = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
            Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(120000, 1001), Fraction(120)]

def snap_fps(f):
    """Snaps a measured frame rate (e.g. 59.9401) to the closest standard rate."""
    best = min(STANDARD, key=lambda s: abs(float(s) - float(f)))
    return best if abs(float(best) - float(f)) < 0.05 else f

def fd(fps):
    return 1 / Fraction(fps)

def frames(sec, fps):
    return int(round(Fraction(str(sec)) * Fraction(fps)))

def rt(nframes, fps):
    """FCPXML rational time for n frames."""
    t = nframes * fd(fps)
    if t == 0:
        return '0s'
    return f'{t.numerator}/{t.denominator}s' if t.denominator != 1 else f'{t.numerator}s'

def tc_frames(tc, fps):
    """Timecode HH:MM:SS:FF (or ;FF for drop-frame) -> number of frames since 0."""
    if not tc:
        return 0, False
    drop = ';' in tc
    h, m, s, f = [int(x) for x in tc.replace(';', ':').split(':')]
    nominal = round(float(fps))
    n = ((h * 60 + m) * 60 + s) * nominal + f
    if drop:
        d = 2 if nominal == 30 else 4 if nominal == 60 else 0
        tm = h * 60 + m
        n -= d * (tm - tm // 10)
    return n, drop

def build(edl_path):
    """Reads edl.json and returns (edl, timeline format, assets, frame-accurate clips).
    Relative paths in edl.json are resolved from the folder of edl.json."""
    edl = json.load(open(edl_path, encoding='utf-8'))
    base = os.path.dirname(os.path.abspath(edl_path))
    src = lambda c: os.path.abspath(os.path.join(base, os.path.expanduser(c['file'])))
    assets, order = {}, []
    for c in edl['clips']:
        p = src(c)
        if p not in assets:
            assets[p] = probe(p); order.append(p)
    first = assets[order[0]]
    fmt = dict(edl.get('format') or {})
    fps = Fraction(fmt.get('fps')) if fmt.get('fps') else first['fps']
    if edl.get('vertical'):
        width, height = int(fmt.get('width', 1080)), int(fmt.get('height', 1920))
    else:
        width, height = int(fmt.get('width', first['width'])), int(fmt.get('height', first['height']))
    clips, pos = [], 0
    for c in edl['clips']:
        p = src(c)
        a = assets[p]
        cin, cout = float(c['in']), float(c['out'])
        if a['duration'] < cout <= a['duration'] + 0.5:
            cout = a['duration']  # end margin past the end of the source: stop on its last frame
        if not (0 <= cin < cout <= a['duration'] + 0.001):
            raise SystemExit(f'Cut outside the source clip: {os.path.basename(p)} {cin}-{cout} (duration {a["duration"]:.2f})')
        in_f = frames(cin, a['fps'])
        dur_f = frames(cout - cin, fps)
        if Fraction(fps) == Fraction(a['fps']):  # never go past the last frame of the source
            dur_f = min(dur_f, int(Fraction(str(a['duration'])) * Fraction(a['fps'])) - in_f)
        clips.append({**c, 'file': p, 'asset': a, 'in_f': in_f, 'dur_f': dur_f, 'off_f': pos,
                      'in_s': float(in_f * fd(a['fps'])), 'off_s': float(pos * fd(fps)), 'dur_s': float(dur_f * fd(fps))})
        pos += dur_f
    return edl, {'fps': fps, 'width': width, 'height': height, 'total_f': pos}, [assets[p] for p in order], clips
