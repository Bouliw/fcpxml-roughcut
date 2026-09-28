"""Shared helpers: FCPXML rational time, timecode, and the frame-accurate timeline built from edl.json."""
import json, os, subprocess, unicodedata
from fractions import Fraction

HDR_TRANSFERS = {'arib-std-b67': 'HLG', 'smpte2084': 'PQ'}
# Final Cut Pro's names for the colour spaces a clip can declare (the timelines themselves are Rec. 709)
REC709 = '1-1-1 (Rec. 709)'
COLOR_SPACES = {'HLG': '9-18-9 (Rec. 2020 HLG)', 'PQ': '9-16-9 (Rec. 2020 PQ)', 'bt2020': '9-1-9 (Rec. 2020)'}

def color_space(stream):
    """The colour space of a video stream, as Final Cut Pro names it: a 10-bit HLG clip from a DJI camera is Rec. 2020
    HLG, and saying Rec. 709 would make it look washed out instead of converted."""
    hdr = HDR_TRANSFERS.get(stream.get('color_transfer'))
    if hdr:
        return COLOR_SPACES[hdr]
    return COLOR_SPACES['bt2020'] if stream.get('color_primaries') == 'bt2020' else REC709

def disk_path(path):
    """The path as spelled on disk. macOS keeps accented names decomposed (NFD, "e" + accent) while typed
    text is composed (NFC): both open the same file, but Final Cut Pro gets the file's own spelling."""
    cur = os.sep
    for name in path.strip(os.sep).split(os.sep):
        try:
            entries = os.listdir(cur)
        except OSError:
            entries = []
        if name not in entries:
            nfc = unicodedata.normalize('NFC', name)
            name = next((e for e in entries if unicodedata.normalize('NFC', e) == nfc), name)
        cur = os.path.join(cur, name)
    return cur

def transcript_path(folder, clip):
    """<folder>/<clip name>.words.json, whether the accents in its name were typed or read on disk."""
    base = os.path.splitext(os.path.basename(clip))[0]
    return disk_path(os.path.join(os.path.abspath(folder), base + '.words.json'))

def ffprobe(path):
    """ffprobe's description of a file, with a clear message when it is missing or unreadable."""
    if not os.path.isfile(path):
        raise SystemExit(f'Source not found: {path}')
    r = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'ffprobe cannot read {path}: {r.stderr.strip()}')
    return json.loads(r.stdout)

def video_stream(d):
    """First real video stream: a cover picture embedded in the file is also a "video" stream."""
    return next((s for s in d['streams'] if s.get('codec_type') == 'video'
                 and not (s.get('disposition') or {}).get('attached_pic')), None)

def timecode(d):
    tc = ((d.get('format') or {}).get('tags') or {}).get('timecode')
    for s in d['streams']:
        tc = tc or (s.get('tags') or {}).get('timecode')
    return tc

def frame_rate(v):
    """Frame rate of a video stream: the nominal one when it is a standard rate within 1 % of the measured one (a
    clip that dropped a few frames measures 59.82 for 59.94, and its cuts must fall on the frames the file has), else
    the measured one (a phone's variable rate), snapped later."""
    rate, nominal = v.get('avg_frame_rate'), v.get('r_frame_rate')
    measured = Fraction(rate if rate not in (None, '0/0') else nominal)
    if nominal not in (None, '0/0') and Fraction(nominal) in STANDARD and abs(Fraction(nominal) - measured) / Fraction(nominal) < Fraction(1, 100):
        return Fraction(nominal)
    return measured

def display_size(v):
    """(width, height, rotation) as displayed: a phone clip shot upright is stored sideways."""
    rot = 0
    for sd in (v.get('side_data_list') or []):
        if 'rotation' in sd:
            rot = int(sd['rotation'])
    w, h = int(v['width']), int(v['height'])
    return (h, w, rot) if abs(rot) in (90, 270) else (w, h, rot)

def probe(path):
    d = ffprobe(path)
    v = video_stream(d)
    if not v:
        raise SystemExit(f'No video stream in {path}')
    a = [s for s in d['streams'] if s.get('codec_type') == 'audio']
    duration, fps = float(d['format']['duration']), snap_fps(frame_rate(v))
    w, h, _ = display_size(v)
    nb = int(v.get('nb_frames') or 0)  # exact frame count when the container stores it (mp4, mov)
    return {'file': os.path.abspath(path), 'duration': duration, 'fps': fps,
            'frames': nb or frames(duration, fps), 'width': w, 'height': h, 'timecode': timecode(d),
            'audio_channels': a[0].get('channels', 2) if a else 0, 'audio_rate': int(a[0].get('sample_rate', 48000)) if a else 48000,
            'hdr': HDR_TRANSFERS.get(v.get('color_transfer')), 'color_space': color_space(v),
            'codec': v.get('codec_name'), 'pix_fmt': v.get('pix_fmt')}

def probe_audio(path):
    """An audio-only source (music, a sound effect; a cover picture in an mp3 does not count as video)."""
    d = ffprobe(path)
    a = [s for s in d['streams'] if s.get('codec_type') == 'audio']
    if not a:
        raise SystemExit(f'No audio stream in {path}')
    return {'file': os.path.abspath(path), 'duration': float(d['format']['duration']), 'audio_only': True,
            'audio_channels': a[0].get('channels', 2), 'audio_rate': int(a[0].get('sample_rate', 48000))}

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

def is_number(x):
    try:
        return float(x) == float(x)  # NaN is not a number of seconds
    except (TypeError, ValueError):
        return False

def build(edl_path):
    """Reads edl.json and returns (edl, timeline format, assets, frame-accurate clips).
    Relative paths in edl.json are resolved from the folder of edl.json."""
    try:
        with open(edl_path, encoding='utf-8') as f:
            edl = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f'Cannot read {edl_path}: {e}')
    if not isinstance(edl, dict) or not edl.get('clips'):
        raise SystemExit(f'No clips in {edl_path}')
    for c in edl['clips']:
        if not (isinstance(c, dict) and 'file' in c and is_number(c.get('in')) and is_number(c.get('out'))):
            raise SystemExit(f'Each clip needs "file", and "in" and "out" in seconds: {c}')
    for m in edl.get('markers', []):
        if not (isinstance(m, dict) and is_number(m.get('at'))):
            raise SystemExit(f'Each marker needs "at" in seconds: {m}')
    base = os.path.dirname(os.path.abspath(edl_path))
    src = lambda c: disk_path(os.path.abspath(os.path.join(base, os.path.expanduser(c['file']))))
    assets, order = {}, []
    for c in edl['clips']:
        p = src(c)
        if p not in assets:
            assets[p] = probe(p); order.append(p)
    first = assets[order[0]]
    fmt = dict(edl.get('format') or {})
    fps = snap_fps(Fraction(str(fmt['fps']))) if fmt.get('fps') else first['fps']  # "59.94" -> 60000/1001
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
        # Both cut points snap to the source frames, so back-to-back cuts neither drop nor repeat a frame,
        # and the out point never goes past the last frame of the source
        in_f = frames(cin, a['fps'])
        out_f = min(frames(cout, a['fps']), a.get('frames') or frames(a['duration'], a['fps']))
        if out_f <= in_f:
            raise SystemExit(f'Cut shorter than one frame: {os.path.basename(p)} {cin}-{cout}')
        if Fraction(fps) == Fraction(a['fps']):
            dur_f = out_f - in_f
        else:
            dur_f = max(1, int(round((out_f - in_f) * fd(a['fps']) * Fraction(fps))))
        clips.append({**c, 'file': p, 'asset': a, 'in_f': in_f, 'dur_f': dur_f, 'off_f': pos,
                      'in_s': float(in_f * fd(a['fps'])), 'off_s': float(pos * fd(fps)), 'dur_s': float(dur_f * fd(fps))})
        pos += dur_f
    return edl, {'fps': fps, 'width': width, 'height': height, 'total_f': pos}, [assets[p] for p in order], clips
