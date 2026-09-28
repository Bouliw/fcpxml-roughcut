"""Dialogue levelling: measures the loudness of every cut (ffmpeg ebur128) and works out one gain per cut, written
in the FCPXML as adjust-volume. Nothing is rendered: the footage and its sound stay untouched.

Cuts from the same source share one gain, worked out from all of them, so back-to-back cuts of one take never jump
in level. A cut gets its own gain only when it stands out from its source by more than clip_deviation_lu (2 LU by default: someone
further from the microphone, a shout). Cuts too short to measure take their source's gain.
A moment played for its own sound (role "effects": the IRL moments of a vlog) is brought towards ambient_target_lufs,
under the voice: on real footage they went from -54 LUFS (a quiet room) to -14 (wind on a road, louder than the voice).
A quiet one is raised a little only (ambient_max_boost_db), not to turn its hiss up."""
import hashlib, json, math, os, re, subprocess

def measure(path, start, duration, track=0):
    """Integrated loudness (LUFS) of a range of the source, or None when it is silent or has no sound."""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-ss', f'{start:.3f}', '-t', f'{duration:.3f}', '-i', path,
                        '-map', f'0:a:{track}', '-af', 'ebur128=framelog=quiet', '-f', 'null', '-'],
                       capture_output=True, text=True)
    found = re.findall(r'I:\s+(-?[\d.]+|-inf) LUFS', r.stderr)
    if r.returncode or not found or found[-1] == '-inf' or float(found[-1]) < -70:
        return None
    return float(found[-1])

class Cache:
    """Measurements kept in a JSON file, keyed by source (path, size, date) and range."""
    def __init__(self, path):
        self.path, self.data = path, {}
        if path and os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def key(self, path, start, duration):
        try:
            st = os.stat(path)
            stamp = f'{st.st_size}|{int(st.st_mtime)}'
        except OSError:
            stamp = 'missing'
        raw = f'{path}|{stamp}|{start:.3f}|{duration:.3f}'
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

    def measure(self, path, start, duration):
        k = self.key(path, start, duration)
        if k not in self.data:
            self.data[k] = measure(path, start, duration)
        return self.data[k]

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=0)

def combined(levels):
    """Loudness of several ranges played one after the other: [(lufs, seconds)] -> lufs (energy average)."""
    total = sum(d for _, d in levels)
    return 10 * math.log10(sum(d * 10 ** (l / 10) for l, d in levels) / total) if total else None

def gains(clips, cfg, cache):
    """One gain in dB per clip (None: no sound to level, or not dialogue), and the measurements behind them."""
    a = cfg['audio']
    target, limit, deviation, shortest = (a['dialogue_target_lufs'], a['max_gain_db'], a['clip_deviation_lu'],
                                          a['min_measure_seconds'])
    measured = []
    for c in clips:
        role = c.get('role', cfg['roles']['default_audio'])
        long_enough = c['dur_s'] >= shortest
        measured.append(cache.measure(c['file'], c['in_s'], c['dur_s'])
                        if role in ('dialogue', 'effects') and c['asset']['audio_channels'] and long_enough else None)
    by_source = {}
    for c, m in zip(clips, measured):
        if m is not None and c.get('role', cfg['roles']['default_audio']) == 'dialogue':
            by_source.setdefault(c['file'], []).append((m, c['dur_s']))
    source_level = {f: combined(v) for f, v in by_source.items()}
    out = []
    for c, m in zip(clips, measured):
        if c.get('role', cfg['roles']['default_audio']) == 'effects':
            g = None if m is None else max(-a['ambient_max_cut_db'], min(a['ambient_max_boost_db'], a['ambient_target_lufs'] - m))
            out.append((None if g is None else round(g, 1), m, None))
            continue
        speech = c.get('role', cfg['roles']['default_audio']) == 'dialogue' and c['asset']['audio_channels']
        level = source_level.get(c['file'])
        if not speech or level is None:
            out.append((None, m, level))
            continue
        own = m is not None and abs(m - level) > deviation
        g = max(-limit, min(limit, target - (m if own else level)))
        out.append((round(g, 1), m, level))
    return out

def db(g):
    """FCPXML volume amount: '+3.2dB', '-1.5dB', '0dB'."""
    return '0dB' if abs(g) < 0.05 else f'{g:+.1f}dB'
