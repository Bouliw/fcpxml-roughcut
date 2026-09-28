#!/usr/bin/env python3
"""Music: tempo and beats of each track, a ranking of the tracks of a folder for an edit, and the pieces that
make_fcpxml.py and render_preview.py use for "music" in edl.json (the volume that ducks under speech, the beats the
B-roll cuts snap to).

Beats come from librosa. Without librosa here, the script borrows the one of fcp-mcp-server if uvx is installed
(uvx --from "fcp-mcp-server[intelligence]"), as fcp-mcp-server does the same analysis; failing both, from a small
beat tracker written with numpy (onset strength, tempo by autocorrelation, beats by dynamic programming). Results are
cached.
Usage: music.py track_or_folder [...] --duration 180 [--bpm 80-130] [--cache dir]

In edl.json:
  "music": {"file": "music/track.mp3", "in": 0, "at": 0, "duck": true, "snap_broll": true}
"in": where to start in the track; "at": where it starts on the timeline; the level and the ducking are settings."""
import argparse, hashlib, json, os, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timeline import probe_audio, disk_path
import settings

AUDIO = ('.mp3', '.m4a', '.wav', '.aif', '.aiff', '.flac', '.aac', '.ogg')
SNIPPET = ('import json, sys\nfrom fcpxml.media_intel import detect_beats\n'
           'print(json.dumps(detect_beats(sys.argv[1])))')

def _librosa_beats(path):
    import librosa
    y, sr = librosa.load(path, sr=None, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    return {'bpm': float(tempo[0] if hasattr(tempo, '__len__') else tempo),
            'beats': [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=sr)]}

def _numpy_beats(path, sr=22050, hop=512, n_fft=2048):
    """Tempo and beats with numpy only (after D. Ellis, "Beat tracking by dynamic programming", 2007)."""
    import numpy as np
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0', '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'],
                       capture_output=True)
    y = np.frombuffer(r.stdout, dtype=np.float32)
    n = 1 + (len(y) - n_fft) // hop if len(y) >= n_fft else 0
    if r.returncode or n < 64:
        raise SystemExit(f'Beat detection failed on {path}: too short or unreadable')
    window, prev, flux = np.hanning(n_fft).astype(np.float32), None, np.zeros(n, dtype=np.float32)
    for s in range(0, n, 1024):  # onset strength: the rise of the log spectrum, in blocks to spare memory
        idx = (np.arange(s, min(n, s + 1024)) * hop)[:, None] + np.arange(n_fft)[None, :]
        mag = np.log1p(100 * np.abs(np.fft.rfft(y[idx] * window, axis=1)))
        steps = np.diff(mag, axis=0, prepend=mag[:1] if prev is None else prev[None, :])
        flux[s:s + len(mag)] = np.maximum(steps, 0).sum(axis=1)
        prev = mag[-1]
    smooth = np.convolve(flux, np.ones(16) / 16, 'same')
    onset = np.maximum(flux - smooth, 0)
    onset /= onset.std() or 1.0
    fps = sr / hop
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 60) + 1)  # 60 to 180 BPM
    ac = np.array([np.dot(onset[:-l], onset[l:]) for l in lags])
    prior = np.exp(-0.5 * (np.log2(60 * fps / lags / 120) / 1.0) ** 2)  # a preference for tempos around 120 BPM
    k = int(np.argmax(ac * prior))
    period = float(lags[k])
    if 0 < k < len(ac) - 1:  # between two lags: the top of the parabola through the three around the peak
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        period += 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0.0
    score, back = onset.astype(np.float64).copy(), np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(lo, hi + 1)
    penalty = -100 * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prevs = t - offsets
        ok = prevs >= 0
        cand = score[prevs[ok]] + penalty[ok]
        k = int(np.argmax(cand))
        score[t] = onset[t] + cand[k]
        back[t] = prevs[ok][k]
    t = int(np.argmax(score[-int(period):])) + n - int(period)
    frames = []
    while t >= 0:
        frames.append(t)
        t = int(back[t])
    return {'bpm': round(60 * fps / period, 1), 'beats': [round((f * hop + n_fft / 2) / sr, 3) for f in reversed(frames)]}  # frame centres

def beats(path, cache_dir=None):
    """{'bpm': ..., 'beats': [seconds]} of a track, from the cache when the file is unchanged."""
    st = os.stat(path)
    key = hashlib.sha1(f'{path}|{st.st_size}|{int(st.st_mtime)}'.encode('utf-8')).hexdigest()
    cache_file = os.path.join(cache_dir, 'beats.json') if cache_dir else None
    cache = {}
    if cache_file and os.path.exists(cache_file):
        with open(cache_file, encoding='utf-8') as f:
            cache = json.load(f)
    if key in cache:
        return cache[key]
    try:
        result = _librosa_beats(path)
    except ImportError:
        result = None
    if result is None and shutil.which('uvx'):
        r = subprocess.run(['uvx', '--from', 'fcp-mcp-server[intelligence]', 'python', '-c', SNIPPET, path],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            found = json.loads(r.stdout.strip().splitlines()[-1])
            result = {'bpm': round(found['bpm'], 1), 'beats': [round(b, 3) for b in found['beats']]}
    if result is None:
        result = _numpy_beats(path)
    cache[key] = result
    if cache_file:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    return result

def envelope(speech, total, cfg):
    """[(seconds, dB)] volume keyframes of the music: its level, dipping under every stretch of speech. The dip ends
    as the speech starts (ramp_down_seconds before it), the rise starts as it ends (ramp_up_seconds long), and a gap
    shorter than min_gap_seconds stays down. When a gap is too short for a full rise and fall, the music comes up only
    part of the way, where the two ramps meet."""
    m = cfg['music']
    base, low, down, up = m['volume_db'], m['duck_db'], m['ramp_down_seconds'], m['ramp_up_seconds']
    spans = []
    for a, b in speech:
        if spans and a - spans[-1][1] < m['min_gap_seconds']:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    keys = [(0.0, base)]
    for n, (a, b) in enumerate(spans):
        start = a - down
        if keys[-1][1] == low:  # coming from the end of the previous stretch
            b0 = keys[-1][0]
            if start < b0 + up:  # too short a gap for a full rise and fall: the two ramps meet part of the way up
                t = min(max((b0 * down / up + a) / (1 + down / up) if up else b0, b0), a)
                keys.append((round(t, 3), round(low + (base - low) * (t - b0) / up if up else base, 2)))
            else:
                keys.append((round(b0 + up, 3), base))
                if start > b0 + up:
                    keys.append((round(start, 3), base))
        elif start > keys[-1][0]:
            keys.append((round(start, 3), base))
        keys += [(round(max(a, 0.0), 3), low), (round(min(b, total), 3), low)]
    out = []
    for t, v in keys:  # two keyframes at one time (speech from the very start): the later one wins
        if out and t <= out[-1][0]:
            out[-1] = (out[-1][0], v)
        else:
            out.append((t, v))
    if out[-1][1] == low and out[-1][0] < total:  # after the last words: back up
        out.append((round(min(total, out[-1][0] + up), 3), base))
    if out[-1][0] < total:
        out.append((round(total, 3), out[-1][1]))
    return out

def speech_spans(words, gap=0.8):
    """Timeline words [(start, end, word)] -> [[start, end]] stretches of speech."""
    out = []
    for t0, t1, _ in words:
        if out and t0 - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], t1)
        else:
            out.append([t0, t1])
    return out

def volume_expr(keys):
    """ffmpeg volume expression (linear gain over t) following the dB keyframes, linear in dB in between."""
    expr = f'{keys[-1][1]}'
    for (t0, d0), (t1, d1) in reversed(list(zip(keys, keys[1:]))):
        if t1 > t0:
            expr = f'if(lt(t,{t1:.3f}),{d0}+({d1 - d0})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})'
    return f'pow(10,({expr})/20)'

def plan(edl, clips, tl, folder, cfg):
    """The music of edl.json, worked out for an edit: None, or {file, asset, in, at (timeline seconds), dur, keys
    [(seconds from the music start, dB)], ducked, bpm, beats (timeline seconds, when "snap_broll")}."""
    from make_srt import timeline_words  # here, not at the top: make_srt is a script too
    from timeline import fd
    m = edl.get('music')
    if not m:
        return None
    path = disk_path(os.path.abspath(os.path.join(folder, os.path.expanduser(m['file']))))
    a = probe_audio(path)
    m_in, m_at = float(m.get('in', 0)), float(m.get('at', 0))
    dur = min(float(tl['total_f'] * fd(tl['fps'])) - m_at, a['duration'] - m_in)
    if dur <= 0:
        raise SystemExit(f'The music starts after the end of the timeline or of the track: {m}')
    keys, ducked = [(0.0, cfg['music']['volume_db'])], 0
    if m.get('duck', True):
        spans = [[x - m_at, y - m_at] for x, y in speech_spans(timeline_words(clips, os.path.join(folder, 'transcripts')))
                 if y > m_at and x < m_at + dur]
        keys, ducked = envelope(spans, dur, cfg), len(spans)
    bpm, grid = None, None
    if m.get('snap_broll'):
        b = beats(path, os.path.join(folder, 'cache'))
        bpm, grid = b['bpm'], [m_at + t - m_in for t in b['beats'] if m_in <= t <= m_in + dur]
    return {'file': path, 'asset': a, 'in': m_in, 'at': m_at, 'dur': dur, 'keys': keys, 'ducked': ducked,
            'bpm': bpm, 'beats': grid}

def main():
    parser = argparse.ArgumentParser(description='Tempo and beats of music tracks, ranked for an edit of a given length.')
    parser.add_argument('paths', nargs='+', help='tracks, or folders of tracks')
    parser.add_argument('--duration', type=float, required=True, help='length of the edit, in seconds')
    parser.add_argument('--bpm', help='tempo range wanted, e.g. 80-130')
    parser.add_argument('--cache', help='folder for the beat cache (default: cache/ in the current folder)')
    args = parser.parse_args()
    files = []
    for p in args.paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(AUDIO))
        else:
            files.append(p)
    lo, hi = map(float, args.bpm.split('-')) if args.bpm else (0, 1e9)
    rows = []
    for f in files:
        path = disk_path(os.path.abspath(f))
        a = probe_audio(path)
        b = beats(path, args.cache or 'cache')
        # long enough first, then the closest to the length, then a tempo in the range
        short = max(0.0, args.duration - a['duration'])
        rows.append((short > 0, abs(a['duration'] - args.duration), not lo <= b['bpm'] <= hi, a['duration'], b['bpm'], path))
    rows.sort()
    print(f'Tracks for a {args.duration:.0f} s edit' + (f', tempo {args.bpm}' if args.bpm else '') + ':')
    for n, (too_short, _, off_tempo, dur, bpm, path) in enumerate(rows, 1):
        notes = ', '.join(x for x, on in (('too short', too_short), ('tempo out of range', off_tempo)) if on)
        m, s = divmod(dur, 60)
        print(f'{n:3d}. {int(m)}:{s:04.1f}  {bpm:5.1f} bpm  {os.path.basename(path)}' + (f'  ({notes})' if notes else ''))

if __name__ == '__main__':
    main()
