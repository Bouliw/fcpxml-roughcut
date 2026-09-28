#!/usr/bin/env python3
"""Looks at the footage a few frames per second: sharpness, shake, exposure, black frames, faces, and on macOS Apple
Vision's aesthetic score and scene labels. For each clip it writes <clip>.analysis.json (the measurements, reused on
the next run while the clip is unchanged) and <clip>.analysis.txt:
- ranges to reject, with the reason (blurred, shaky, black, over- or underexposed, camera pointing at the ground);
- the best B-roll moments: no speech, sharp, steady, well exposed, the best looking first;
- thumbnail candidates: a sharp, well-framed face in a good-looking frame, saved as full-size JPEG.
Everything here is a proposal made from sampled frames: the editor decides.
Needs numpy (pip install numpy). Apple Vision (pip install pyobjc-framework-Vision, macOS) adds faces, the
aesthetic score and the labels; without it they are left out and said so.
Usage: analyze.py clip [clip ...] -o analysis_dir [--transcripts dir] [--config file.json]"""
import argparse, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import probe, transcript_path
import settings

try:
    import numpy as np
except ImportError:
    raise SystemExit('analyze.py needs numpy: pip install numpy')

try:
    import objc
    import Quartz
    import Vision
    from Foundation import NSData
except ImportError:
    Vision = None

VERSION = 4  # bump when the measurements change, so cached analyses are redone (4: the main subject)

# ---------------------------------------------------------------- sampling

GPU_FORMATS = {('h264', 'yuv420p'): 'nv12', ('h264', 'yuvj420p'): 'nv12', ('hevc', 'yuv420p'): 'nv12',
               ('hevc', 'yuv420p10le'): 'p010le', ('h264', 'yuv420p10le'): 'p010le'}

def gpu_format(info):
    """How the Mac's video decoder hands over frames of this clip (None: decoded on the CPU)."""
    return GPU_FORMATS.get((info.get('codec'), info.get('pix_fmt'))) if sys.platform == 'darwin' else None

def sample_pairs(path, fps, every, width, height, gpu=None, deep=False):
    """Yields (frame number, rgb image, next rgb image or None): two consecutive frames every `every` frames. The
    second frame of each pair gives the camera motion at that instant. With `gpu` (the format of the decoder's
    frames), the frames stay on the GPU until the ones kept are picked: only those come to the CPU, which scales them
    as before (the same pixels, 40 % faster on 4K); if the decoder refuses the clip, it is read on the CPU. With
    `deep`, the images are 16 bits per channel (HDR and log footage, converted later)."""
    vf = f"select='lt(mod(n\\,{every})\\,2)'" + (f',hwdownload,format={gpu}' if gpu else '') + f',scale={width}:{height}'
    hw = ['-hwaccel', 'videotoolbox', '-hwaccel_output_format', 'videotoolbox_vld'] if gpu else ['-hwaccel', 'auto']
    cmd = ['ffmpeg', '-v', 'error', *hw, '-i', path, '-map', '0:v:0', '-vf', vf, '-fps_mode', 'passthrough',
           '-f', 'rawvideo', '-pix_fmt', 'rgb48le' if deep else 'rgb24', '-']
    dtype = np.uint16 if deep else np.uint8
    size = width * height * 3 * np.dtype(dtype).itemsize
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    k, pending, read = 0, None, 0
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            read += 1
            img = np.frombuffer(raw, dtype).reshape(height, width, 3)
            if pending is None:
                pending = img
            else:
                yield k * every, pending, img
                k, pending = k + 1, None
        if pending is not None:
            yield k * every, pending, None
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode('utf-8', 'replace')
        proc.stderr.close()
        failed = proc.wait() and not err.strip().startswith('Error while decoding')
    if failed and gpu and not read:  # the decoder would not take it: the CPU does
        print(f'{os.path.basename(path)}: read on the CPU ({err.strip()[-120:]})')
        yield from sample_pairs(path, fps, every, width, height, None, deep)
    elif failed:
        print(f'WARNING: ffmpeg stopped early on {os.path.basename(path)}: {err.strip()[-300:]}')

# ---------------------------------------------------------------- HDR and log footage

REF_WHITE = 203.0  # nits: HDR reference white (ITU-R BT.2408), which becomes SDR white
BT2020_TO_709 = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]], np.float32)
KNEE = 0.8  # above it, the highlights are rolled off towards SDR white instead of clipped

def _to_linear(look):
    """Table from a 16-bit code value to linear light, SDR white at 1.0."""
    e = np.arange(65536, dtype=np.float64) / 65535
    if look == 'HLG':  # scene light, 0 to 1 (ITU-R BT.2100); the display gain comes after, from the luminance
        a, b, c = 0.17883277, 0.28466892, 0.55991073
        return np.where(e <= 0.5, e * e / 3, (np.exp((e - c) / a) + b) / 12).astype(np.float32)
    if look == 'PQ':  # display light in nits (SMPTE ST 2084), then over reference white
        m1, m2, c1, c2, c3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
        p = np.power(e, 1 / m2)
        return (10000 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1) / REF_WHITE).astype(np.float32)
    # DJI's published D-Log curve, as an approximation of D-Log M (whose curve is not published): scene reflectance,
    # 18 % grey at 0.18, shown as SDR with 100 % reflectance at SDR white
    return np.where(e <= 0.14, (e - 0.0929) / 6.025, (np.power(10, 3.89616 * e - 2.27752) - 0.0108) / 0.9892).clip(0).astype(np.float32)

_LINEAR, _ENCODE = {}, None

def to_sdr(img16, look):
    """An HDR (HLG, PQ) or log frame, 16 bits per channel, as the Rec. 709 SDR picture Final Cut Pro would show on a
    Rec. 709 timeline: display light with HDR reference white at SDR white, highlights rolled off, BT.2020 colours
    brought into Rec. 709, 8 bits."""
    global _ENCODE
    if look not in _LINEAR:
        _LINEAR[look] = _to_linear(look)
    if _ENCODE is None:  # linear 0 to 8 in 16384 steps -> 8-bit code, with the roll-off and the 2.4 gamma
        x = np.arange(16384, dtype=np.float64) / 2048
        x = np.where(x > KNEE, KNEE + (1 - KNEE) * (1 - np.exp(-(x - KNEE) / (1 - KNEE))), x)
        _ENCODE = np.rint(255 * np.power(x, 1 / 2.4)).astype(np.uint8)
    lin = _LINEAR[look][img16]
    if look == 'HLG':  # the OOTF of a 1000-nit display (system gamma 1.2), over reference white
        ys = lin @ np.array([0.2627, 0.6780, 0.0593], np.float32)
        lin = lin * (1000 / REF_WHITE * np.power(np.maximum(ys, 1e-6), 0.2))[..., None]
    if look in ('HLG', 'PQ'):
        lin = lin @ BT2020_TO_709.T
    return _ENCODE[np.clip(lin * 2048, 0, 16383).astype(np.int32)]

def clipped(img16):
    """Share of the picture at the top of the signal: what the camera itself could not hold (HDR and log keep
    highlights well above SDR white, so SDR's 250 out of 255 says nothing there)."""
    return round(float((img16.max(axis=2) >= 64880).mean()), 3)  # 99 % of the signal

# ---------------------------------------------------------------- measurements

def gray_of(img):
    """Luma of an RGB image, 8 bits (ITU-R BT.601 weights)."""
    return np.rint(img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114).astype(np.uint8)

def half(gray):
    """Half the size, each pixel the mean of four."""
    g = gray[:gray.shape[0] // 2 * 2, :gray.shape[1] // 2 * 2].astype(np.float64)
    return (g[0::2, 0::2] + g[1::2, 0::2] + g[0::2, 1::2] + g[1::2, 1::2]) / 4

def phase_correlate(a, b):
    """(dx, dy, response): the shift between two pictures of the same size, from the peak of their normalised
    cross-power spectrum, to a fraction of a pixel (weighted centre of the 5x5 pixels around the peak); the response
    (near 1 for the same picture moved, near 0 for unrelated ones) says how much to trust it."""
    r = np.fft.fft2(a) * np.conj(np.fft.fft2(b))
    r = np.fft.fftshift(np.fft.ifft2(r / np.maximum(np.abs(r), 1e-12)).real)
    py, px = np.unravel_index(int(np.argmax(r)), r.shape)
    y0, y1, x0, x1 = max(py - 2, 0), min(py + 3, r.shape[0]), max(px - 2, 0), min(px + 3, r.shape[1])
    w = r[y0:y1, x0:x1]
    total = float(w.sum())
    if abs(total) < 1e-12:
        return 0.0, 0.0, 0.0
    ty = float(w.sum(axis=1) @ np.arange(y0, y1)) / total
    tx = float(w.sum(axis=0) @ np.arange(x0, x1)) / total
    return r.shape[1] / 2 - tx, r.shape[0] / 2 - ty, total

class Motion:
    """Camera displacement between two consecutive frames (phase correlation), in % of the frame width."""
    def __init__(self, width, height):
        self.size = (width // 2, height // 2)
        self.window = np.outer(np.hanning(self.size[1]), np.hanning(self.size[0]))  # softens the edges

    def __call__(self, a, b):
        ga, gb = (half(gray_of(x)) * self.window for x in (a, b))
        dx, dy, response = phase_correlate(ga, gb)
        return [round(100 * dx / self.size[0], 3), round(100 * dy / self.size[0], 3), round(response, 3)]

def laplacian(gray):
    """The 3x3 Laplacian (4 neighbours), edges mirrored."""
    g = np.pad(gray.astype(np.float64), 1, mode='reflect')
    return g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]

def sharpness(gray, grid=4):
    """Detail of the sharpest part of the picture: a sharp face over a soft background is not a blurred shot. The
    second sharpest tile of a 4x4 grid, so one noisy tile is not enough."""
    lap = laplacian(gray)
    h, w = gray.shape
    tiles = sorted(lap[y * h // grid:(y + 1) * h // grid, x * w // grid:(x + 1) * w // grid].var()
                   for y in range(grid) for x in range(grid))
    return float(tiles[-2])

def pixels(img):
    gray = gray_of(img)
    return {'sharp': round(sharpness(gray), 1),
            'luma': round(float(gray.mean()), 1), 'contrast': round(float(gray.std()), 1),
            'hi': round(float((gray >= 250).mean()), 3), 'lo': round(float((gray <= 8).mean()), 3)}

def vision(img):
    """Faces (normalised boxes, origin at the bottom left, with Vision's capture quality), the main subject (the
    largest object Vision finds salient: what a vertical Short follows when nobody faces the camera), aesthetic score,
    labels."""
    h, w, _ = img.shape
    data = np.ascontiguousarray(img).tobytes()
    picture = Quartz.CGImageCreate(w, h, 8, 24, w * 3, Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB),
                                   Quartz.kCGImageAlphaNone, Quartz.CGDataProviderCreateWithCFData(NSData.dataWithBytes_length_(data, len(data))),
                                   None, False, Quartz.kCGRenderingIntentDefault)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(picture, None)
    faces, classify = Vision.VNDetectFaceCaptureQualityRequest.alloc().init(), Vision.VNClassifyImageRequest.alloc().init()
    salient = Vision.VNGenerateObjectnessBasedSaliencyImageRequest.alloc().init()
    requests = [faces, classify, salient]
    aesthetics = None
    if hasattr(Vision, 'VNCalculateImageAestheticsScoresRequest'):  # macOS 15 and later
        aesthetics = Vision.VNCalculateImageAestheticsScoresRequest.alloc().init()
        requests.append(aesthetics)
    handler.performRequests_error_(requests, None)
    out = {'faces': [], 'labels': {}}
    for f in faces.results() or []:
        b = f.boundingBox()
        out['faces'].append([round(b.origin.x, 3), round(b.origin.y, 3), round(b.size.width, 3), round(b.size.height, 3),
                             round(float(f.faceCaptureQuality() or 0), 2)])
    for r in salient.results() or []:
        boxes = [o.boundingBox() for o in (r.salientObjects() or [])]
        if boxes:
            b = max(boxes, key=lambda b: b.size.width * b.size.height)
            out['subject'] = [round(b.origin.x, 3), round(b.origin.y, 3), round(b.size.width, 3), round(b.size.height, 3)]
    for o in classify.results() or []:
        if o.confidence() >= 0.3:
            out['labels'][str(o.identifier())] = round(float(o.confidence()), 2)
    if aesthetics is not None and aesthetics.results():
        r = aesthetics.results()[0]
        out['aesthetic'], out['utility'] = round(float(r.overallScore()), 3), bool(r.isUtility())
    return out

def measure(path, cfg, cached):
    """Samples of one clip, from the cache when the clip and the sampling settings are unchanged."""
    q = cfg['quality']
    st = os.stat(path)
    stamp = {'version': VERSION, 'size': st.st_size, 'mtime': int(st.st_mtime), 'sample_fps': q['sample_fps'],
             'width': q['width'], 'vision': Vision is not None}
    info = probe(path)
    look = info.get('hdr') or ('DLOG' if q.get('log_footage') else None)
    if look:  # HDR and log footage are measured on their SDR conversion: a cache made without it is redone
        stamp['look'] = look
    if cached and cached.get('info', {}).get('stamp') == stamp:
        return cached['samples'], cached['info']
    fps = float(info['fps'])
    width = q['width']
    height = 2 * round(width * info['height'] / info['width'] / 2)
    every = max(2, round(fps / q['sample_fps']))
    motion, samples = Motion(width, height), []
    total = int(info['duration'] * fps / every) + 1
    for n, img, nxt in sample_pairs(path, fps, every, width, height, gpu_format(info), deep=bool(look)):
        if look:  # the motion from the signal as it is (both frames alike), the rest from the picture as it shows
            raw, nraw, img = img, nxt, to_sdr(img, look)
            s = {'t': round(n / fps, 3), **pixels(img), 'hi': clipped(raw)}
            if nraw is not None:
                s['motion'] = motion((raw >> 8).astype(np.uint8), (nraw >> 8).astype(np.uint8))
        else:
            s = {'t': round(n / fps, 3), **pixels(img)}
            if nxt is not None:
                s['motion'] = motion(img, nxt)
        if Vision is not None:
            with objc.autorelease_pool():  # each frame's Vision objects freed at once: without it an hour of footage
                s.update(vision(img))      # held some 30 GB and the analysis was killed
        samples.append(s)
        print(f'\r{os.path.basename(path)}: {len(samples)}/{total} samples', end='', flush=True)
    print()
    info = {'duration': info['duration'], 'fps': fps, 'width': info['width'], 'height': info['height'], 'look': look}
    return samples, {**info, 'stamp': stamp}

# ---------------------------------------------------------------- judgements

def speech_ranges(path, tdir):
    """[(start, end)] where words are said, from the transcript when there is one."""
    p = transcript_path(tdir, path) if tdir else None
    if not p or not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        words = [w for w in json.load(f)['words'] if not w.get('suspect')]
    out = []
    for w in words:
        if out and w['start'] - out[-1][1] < 1.0:
            out[-1][1] = max(out[-1][1], w['end'])
        else:
            out.append([w['start'], w['end']])
    return out

def flags(samples, cfg):
    """Reasons to reject each sample (a list per sample)."""
    q = cfg['quality']
    # An absolute threshold: a face or a plain wall has little fine detail but stays far above it, while even a slight
    # blur (1.5 px at 640 px wide) divides the measure by ten or more. A threshold relative to the clip's median
    # flagged every face in a video full of detailed scenery.
    blur_limit = q['blur_sharpness']
    moves = [m if m and m[2] >= 0.2 else None for m in (s.get('motion') for s in samples)]  # unreliable on flat frames
    out = []
    for i, s in enumerate(samples):
        why = []
        if s['luma'] < q['black_luma'] and s['contrast'] < 8:
            why.append('black')
        elif s['luma'] < q['underexposed_luma']:
            why.append('underexposed')
        if s['hi'] > q['overexposed_share']:
            why.append('overexposed')
        if s['sharp'] < blur_limit and 'black' not in why:
            why.append('blurred')
        # shake: the camera speed changes abruptly from one sample to the next (a steady pan keeps its speed)
        jolts = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(moves[max(0, i - 1):i + 1], moves[max(0, i - 1) + 1:i + 2])
                 if a and b]
        if jolts and min(jolts) > q['shake_jolt']:
            why.append('shaky')
        # camera pointing at the ground: feet in the picture, or a road or pavement with nothing standing up
        labels = s.get('labels', {})
        feet = max((labels.get(l, 0) for l in q['feet_labels']), default=0)
        ground = max((labels.get(l, 0) for l in q['ground_labels']), default=0)
        upright = any(l in labels for l in q['upright_labels'])
        if not s.get('faces') and (feet >= q['ground_confidence'] or (ground >= q['ground_confidence'] and not upright)):
            why.append('ground?')
        out.append(why)
    return out

def ranges(samples, marks, min_seconds, step):
    """Merges consecutive flagged samples into [(start, end, reasons)] lasting at least min_seconds."""
    out, cur = [], None
    for s, why in zip(samples, marks):
        if why:
            if cur and s['t'] - cur[1] <= 1.5 * step:
                cur[1] = s['t'] + step
                for w in why:
                    cur[2][w] = cur[2].get(w, 0) + 1
            else:
                if cur:
                    out.append(cur)
                cur = [s['t'], s['t'] + step, {w: 1 for w in why}]
    if cur:
        out.append(cur)
    return [(a, b, sorted(r, key=lambda k: -r[k])) for a, b, r in out if b - a >= min_seconds]

def inside(t, spans):
    return any(a - 0.3 <= t <= b + 0.3 for a, b in spans or [])

def broll(samples, marks, speech, cfg, step):
    """Best windows without speech or rejection, the best looking first, never overlapping."""
    q = cfg['quality']
    n = max(1, round(q['broll_seconds'] / step))
    sharp = sorted(s['sharp'] for s in samples) or [1]
    ref = sharp[int(0.9 * (len(sharp) - 1))] or 1
    def score(s):
        big_face = any(f[2] * f[3] > q['face_cam_area'] for f in s.get('faces', []))
        return s.get('aesthetic', 0) + 0.5 * min(1, s['sharp'] / ref) - (1 if big_face else 0)
    cands = []
    for i in range(0, len(samples) - n + 1):
        win = samples[i:i + n]
        if any(marks[i:i + n]) or any(inside(s['t'], speech) for s in win):
            continue
        cands.append((sum(score(s) for s in win) / n, win[0]['t'], win[-1]['t'] + step))
    chosen = []
    for sc, a, b in sorted(cands, reverse=True):
        if all(b <= c[1] or a >= c[2] for c in chosen):
            chosen.append((sc, a, b))
        if len(chosen) == q['broll_count']:
            break
    return sorted(chosen, key=lambda c: c[1])

def thumbnails(samples, marks, cfg):
    """Frames with a sharp, well-captured face in a good-looking picture, spaced out."""
    q = cfg['quality']
    cands = []
    for s, why in zip(samples, marks):
        faces = [f for f in s.get('faces', []) if f[2] * f[3] >= q['thumbnail_min_face']]
        if why or not faces:
            continue
        best = max(faces, key=lambda f: f[2] * f[3])
        cands.append((round(best[4] + s.get('aesthetic', 0), 3), s['t']))
    chosen = []
    for sc, t in sorted(cands, reverse=True):
        if all(abs(t - c[1]) >= q['thumbnail_spacing_seconds'] for c in chosen):
            chosen.append((sc, t))
        if len(chosen) == q['thumbnail_count']:
            break
    return sorted(chosen, key=lambda c: c[1])

def still(clip, t, jpg, look, width, height):
    """The frame at t as a full-size JPEG; HDR and log footage converted to SDR like the analysed frames."""
    if not look:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', clip, '-frames:v', '1', '-q:v', '2', jpg], check=True)
        return
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', clip, '-frames:v', '1', '-vf', f'scale={width}:{height}',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-'], capture_output=True, check=True).stdout
    img = to_sdr(np.frombuffer(raw, np.uint16).reshape(height, width, 3), look)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-i', '-',
                    '-frames:v', '1', '-q:v', '2', jpg], input=img.tobytes(), check=True)

def mmss(t):
    m, s = divmod(t, 60)
    return f'{int(m):02d}:{s:04.1f}'

def main():
    parser = argparse.ArgumentParser(description='Sharpness, shake, exposure, faces and looks of the footage; rejects, B-roll and thumbnails.')
    parser.add_argument('clips', nargs='+')
    parser.add_argument('-o', required=True, dest='out', help='folder for the analyses and the thumbnails')
    parser.add_argument('--transcripts', help='folder of the <clip>.words.json files, to tell speech from B-roll')
    parser.add_argument('--config', help='settings file on top of config/defaults.json and the roughcut.json files found')
    args = parser.parse_args()
    cfg = settings.load(os.path.abspath(args.out), args.config)
    q = cfg['quality']
    os.makedirs(args.out, exist_ok=True)
    if Vision is None:
        print('Apple Vision not available (pip install pyobjc-framework-Vision on macOS): no faces, aesthetic score or labels')
    for clip in args.clips:
        clip = os.path.abspath(clip)
        base = os.path.splitext(os.path.basename(clip))[0]
        jpath = os.path.join(args.out, base + '.analysis.json')
        cached = None
        if os.path.exists(jpath):
            with open(jpath, encoding='utf-8') as f:
                cached = json.load(f)
        samples, info = measure(clip, cfg, cached)
        step = 1 / q['sample_fps']
        marks = flags(samples, cfg)
        speech = speech_ranges(clip, args.transcripts)
        rejects = ranges(samples, marks, q['min_reject_seconds'], step)
        moments = broll(samples, marks, speech, cfg, step)
        thumbs = thumbnails(samples, marks, cfg)
        files, kept = [], {(x['t'], x['file']) for x in (cached or {}).get('thumbnails', [])}
        for i, (sc, t) in enumerate(thumbs, 1):
            jpg = os.path.join(args.out, f'{base}_thumbnail_{i}.jpg')
            if (t, jpg) not in kept or not os.path.exists(jpg):  # the same frame already saved: kept
                still(clip, t, jpg, info.get('look'), info['width'], info['height'])
            files.append(jpg)
        result = {'file': clip, 'info': info, 'samples': samples,
                  'rejects': [{'from': round(a, 2), 'to': round(b, 2), 'why': w} for a, b, w in rejects],
                  'broll': [{'from': round(a, 2), 'to': round(b, 2), 'score': round(sc, 3)} for sc, a, b in moments],
                  'thumbnails': [{'t': t, 'score': sc, 'file': f} for (sc, t), f in zip(thumbs, files)],
                  'speech_known': speech is not None}
        with open(jpath + '.part', 'w', encoding='utf-8') as f:  # then renamed: a stopped run leaves no half cache
            json.dump(result, f, ensure_ascii=False)
        os.replace(jpath + '.part', jpath)
        lines = [f'{os.path.basename(clip)}: {len(samples)} samples, {info["duration"]:.1f} s' +
                 ('' if speech is not None else ' (no transcript: B-roll moments may contain speech)')]
        lines.append(f'\nTo reject ({len(rejects)}):')
        lines += [f'  {mmss(a)}-{mmss(b)}  {", ".join(w)}' for a, b, w in rejects]
        lines.append(f'\nBest B-roll moments ({len(moments)}):')
        lines += [f'  {mmss(a)}-{mmss(b)}  score {sc:.2f}' for sc, a, b in moments]
        lines.append(f'\nThumbnail candidates ({len(thumbs)}):')
        lines += [f'  {mmss(t)}  score {sc:.2f}  {os.path.basename(p)}' for (sc, t), p in zip(thumbs, files)]
        lines.append('\n"ground?" and every judgement here come from sampled frames: check them in the viewer.')
        text = '\n'.join(lines) + '\n'
        with open(os.path.join(args.out, base + '.analysis.txt'), 'w', encoding='utf-8') as f:
            f.write(text)
        print(text)

if __name__ == '__main__':
    main()
