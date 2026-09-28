#!/usr/bin/env python3
"""The sound of a clip, read closely enough to cut on it: its level (and above 2 kHz, where "s" and "f" are),
whether the voice is there and at what pitch, every 10 ms. Two uses:
- the hesitations Whisper does not write, or writes loosely timed: a vowel held on a steady note ("euh", "hmm")
  between two words, found where the voice holds one pitch and one sound for 0.2 s or more;
- the true silences between words, where a cut can go without eating a syllable (edl_from_ranges.py).
Usage: hesitations.py clip.wav|clip.mp4 [--words clip.words.json]   (prints the held vowels found, and where)"""
import argparse, json, os, subprocess, sys

RATE = 16000
HOP = 160  # 10 ms
WIN = 640  # 40 ms: two periods of a low voice
F0_RANGE = (70.0, 400.0)

def read(path, track=0):
    """The sound of a file, 16 kHz mono, as floats."""
    import numpy as np
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-map', f'0:a:{track}', '-ac', '1', '-ar', str(RATE),
                        '-f', 's16le', '-'], capture_output=True)
    if r.returncode:
        raise SystemExit(f'Cannot read the sound of {path}: {r.stderr.decode(errors="replace").strip()[-200:]}')
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768

class Sound:
    """Every 10 ms: level (dB), level above 2 kHz (dB), voicing (0-1, the height of the autocorrelation peak), pitch
    (Hz, 0 when unvoiced), and a coarse spectrum (24 log bands) to tell one held sound from a changing one."""
    def __init__(self, samples):
        import numpy as np
        self.np = np
        x = np.asarray(samples, dtype=np.float32)
        n = max(0, (len(x) - WIN) // HOP + 1)
        self.n = n
        self.db = np.full(n, -100.0, np.float32)
        self.hf = np.full(n, -100.0, np.float32)
        self.voicing = np.zeros(n, np.float32)
        self.f0 = np.zeros(n, np.float32)
        self.bands = np.zeros((n, 24), np.float32)
        if not n:
            return
        window = np.hanning(WIN).astype(np.float32)
        nfft = 1024
        freqs = np.fft.rfftfreq(nfft, 1 / RATE)
        hf = freqs >= 2000
        edges = np.geomspace(100, 5000, 25)
        band_of = np.digitize(freqs, edges) - 1
        lo, hi = int(RATE / F0_RANGE[1]), int(RATE / F0_RANGE[0])
        # the autocorrelation of the window itself, to undo its taper at each lag
        wac = np.fft.irfft(np.abs(np.fft.rfft(window, nfft)) ** 2)[:hi + 1]
        wac = np.maximum(wac / wac[0], 1e-3)
        for s in range(0, n, 4096):
            e = min(n, s + 4096)
            idx = (np.arange(s, e) * HOP)[:, None] + np.arange(WIN)[None, :]
            frames = x[idx]
            frames = frames - frames.mean(axis=1, keepdims=True)
            power = np.abs(np.fft.rfft(frames * window, nfft)) ** 2
            energy = power.sum(axis=1) + 1e-12
            self.db[s:e] = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
            self.hf[s:e] = 10 * np.log10(power[:, hf].sum(axis=1) / nfft + 1e-10)
            ac = np.fft.irfft(power, nfft)[:, :hi + 1] / wac[None, :]
            ac = ac / np.maximum(ac[:, :1], 1e-12)
            lag = lo + np.argmax(ac[:, lo:hi + 1], axis=1)
            peak = ac[np.arange(e - s), lag]
            self.voicing[s:e] = np.clip(peak, 0, 1)
            self.f0[s:e] = np.where(peak > 0.5, RATE / lag, 0)
            for b in range(24):
                m = band_of == b
                if m.any():
                    self.bands[s:e, b] = np.log10(power[:, m].sum(axis=1) / energy + 1e-9)
        self.floor = float(np.percentile(self.db, 10))
        self.speech = float(np.percentile(self.db, 90))
        quiet = self.db < self.floor + 3  # the hiss of the room, measured where it is quiet (a low voice has less of it)
        self.hf_floor = float(np.median(self.hf[quiet])) if quiet.any() else float(np.percentile(self.hf, 10))

    def t(self, i):
        return round(i * HOP / RATE + WIN / 2 / RATE, 3)  # the middle of frame i

    def i(self, t):
        return int(max(0, min(self.n - 1, round((t - WIN / 2 / RATE) * RATE / HOP)))) if self.n else 0

    def silent(self, i):
        """Nothing said in frame i: the level near the noise floor, and nothing hissing above 2 kHz either."""
        return self.db[i] < self.floor + 0.25 * (self.speech - self.floor) and self.hf[i] < self.hf_floor + 10

    def held_vowels(self, shortest=0.2, drift=0.06, change=0.35):
        """[(start, end, pitch)]: stretches where the voice holds one pitch (within `drift`, about a semitone) and one
        sound (the spectrum changing less than `change` from one 10 ms to the next), for `shortest` seconds or more."""
        key = (shortest, drift, change)
        if key in getattr(self, '_vowels', {}):
            return self._vowels[key]
        np = self.np
        loud = self.db > self.floor + 0.5 * (self.speech - self.floor)
        step = np.concatenate(([9.0], np.sqrt(((self.bands[1:] - self.bands[:-1]) ** 2).mean(axis=1))))
        out, i = [], 0
        while i < self.n:
            if not (loud[i] and self.f0[i] > 0 and self.voicing[i] > 0.6):
                i += 1
                continue
            j, pitches = i + 1, [self.f0[i]]
            while j < self.n and loud[j] and self.f0[j] > 0 and self.voicing[j] > 0.55 and step[j] < change:
                ref = float(np.median(pitches[-10:]))
                if abs(self.f0[j] - ref) > drift * ref:
                    break
                pitches.append(self.f0[j])
                j += 1
            if (j - i) * HOP / RATE >= shortest:
                out.append((self.t(i) - 0.02, self.t(j - 1) + 0.02, round(float(np.median(pitches)), 1)))
            i = max(j, i + 1)
        self._vowels = dict(getattr(self, '_vowels', {}), **{str(key): out})
        self._vowels[key] = out
        return out

    def quiet_between(self, a, b, least=0.03):
        """The silences between a and b seconds: [(start, end)] of at least `least` seconds."""
        i, j = self.i(a), self.i(b)
        out, k = [], i
        while k <= j:
            if self.silent(k):
                m = k
                while m + 1 <= j and self.silent(m + 1):
                    m += 1
                if (m - k + 1) * HOP / RATE >= least:
                    out.append((self.t(k) - 0.005, self.t(m) + 0.005))
                k = m + 1
            else:
                k += 1
        return out

    def loud_between(self, a, b):
        """Whether anything of a word is heard between a and b seconds: the voice, or a soft consonant ("s", "t",
        "f": little level, but a hiss above 2 kHz)."""
        if b <= a:
            return False
        i, j = self.i(a), self.i(b)
        voice = self.db[i:j + 1] > self.floor + 0.35 * (self.speech - self.floor)
        hiss = self.hf[i:j + 1] > self.hf_floor + 12
        return bool((voice | hiss).any())

    def quiet_join(self, a, b):
        """Whether a cut from `a` to `b` seconds falls in silences on both sides."""
        return not (self.loud_between(a - 0.015, a + 0.015) or self.loud_between(b - 0.015, b + 0.015))

    def join(self, a, b, span=0.04):
        """How a cut from `a` to `b` seconds sounds at its join: (the jump in level, in dB; the change of sound, the
        distance between the spectra) from the `span` before `a` to the `span` after `b`."""
        np = self.np
        i, j = self.i(a - span), self.i(b)
        before, after = slice(i, self.i(a) + 1), slice(j, self.i(b + span) + 1)
        level = abs(float(self.db[before].mean() - self.db[after].mean()))
        return level, float(np.sqrt(((self.bands[before].mean(axis=0) - self.bands[after].mean(axis=0)) ** 2).mean()))

    def quietest(self, a, b):
        """The quietest moment between a and b seconds (the level and the hiss together)."""
        np = self.np
        i, j = self.i(a), self.i(b)
        if j <= i:
            return round((a + b) / 2, 3)
        score = self.db[i:j + 1] + 0.5 * self.hf[i:j + 1]
        return self.t(i + int(np.argmin(score)))

def speech_over_background(sound, words):
    """How far the voice stands above the background, in dB: the level while words are said (75th percentile)
    against the level between them (median). On real footage: 40 to 46 dB for an interview somewhere quiet, 18 to 24
    walking or among people, under 11 with music."""
    import numpy as np
    said = [w for w in words if not (w.get('filler') or w.get('suspect'))]
    if len(said) < 10 or not sound.n:
        return None
    between = np.ones(sound.n, bool)
    for w in said:
        between[sound.i(w['start'] - 0.05):sound.i(w['end'] + 0.05) + 1] = False
    if between.all() or not between.any():
        return None
    return float(np.percentile(sound.db[~between], 75) - np.median(sound.db[between]))

def expected_length(word):
    """About how long a word takes to say, by its letters: longer than this, a word holds a hesitation too."""
    return 0.06 * len(''.join(c for c in word if c.isalnum())) + 0.12

def clear_of(v, words, margin=0.03):
    """A held vowel v = (start, end) is clear of the words said: outside them, or in the end of a word Whisper
    stretched over the hesitation after it (a word lasting longer than it takes to say: "et" timed over "et, euh").
    The start of a word is never taken: a word that is a vowel ("et", "eh"), held, is that word."""
    for w in words:
        if w['start'] < v[1] - margin and w['end'] > v[0] + margin:
            if v[0] >= w['start'] + expected_length(w['w']) - margin:
                continue
            return False
    return True

def found(words, sound):
    """The hesitations of a clip, from its words (Whisper, with a transcription primed to write them) and its sound:
    [{'start', 'end', 'by': 'whisper'|'sound'|'both', 'word': index of the word it is, or None}]. A held vowel
    counts when it is between two words (Whisper left it out) or is where Whisper wrote a hesitation (which gives
    its true place); one inside a word, or at the end of a word said slowly ("et..."), is part of what is said."""
    vowels = sound.held_vowels()
    out, used = [], set()
    spoken = [w for w in words if not (w.get('filler') or w.get('suspect'))]

    for k, w in enumerate(words):
        if not w.get('filler') or w.get('sound'):
            continue
        near = [v for v in vowels if v[1] > w['start'] - 0.15 and v[0] < w['end'] + 0.15 and clear_of(v, spoken)]
        if near:
            v = min(near, key=lambda v: abs((v[0] + v[1]) / 2 - (w['start'] + w['end']) / 2))
            used.add(v)
            out.append({'start': v[0], 'end': v[1], 'by': 'both', 'word': k})
        else:
            out.append({'start': w['start'], 'end': w['end'], 'by': 'whisper', 'word': k})
    for v in vowels:
        if v in used or not clear_of(v, spoken):
            continue
        before = [w for w in spoken if w['start'] < v[0]]
        after = [w for w in spoken if w['end'] > v[1]]
        if before and after and min(w['start'] for w in after) - max(w['end'] for w in before) < 3.0:  # amid speech
            out.append({'start': v[0], 'end': v[1], 'by': 'sound', 'word': None})
    return sorted(out, key=lambda h: h['start'])

def main():
    parser = argparse.ArgumentParser(description='Finds the held vowels of hesitations in the sound of a clip.')
    parser.add_argument('clip')
    parser.add_argument('--words', help='the clip\'s .words.json, to tell hesitations from words said slowly')
    args = parser.parse_args()
    sound = Sound(read(args.clip))
    if args.words:
        with open(args.words, encoding='utf-8') as f:
            words = json.load(f)['words']
        for h in found(words, sound):
            print(f'{h["start"]:8.2f} {h["end"]:8.2f}  {h["by"]:7s} {words[h["word"]]["w"] if h["word"] is not None else h.get("after", "")}')
    else:
        for a, b, p in sound.held_vowels():
            print(f'{a:8.2f} {b:8.2f}  {p:6.1f} Hz')

if __name__ == '__main__':
    main()
