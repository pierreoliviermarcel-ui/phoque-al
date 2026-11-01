"""Analyse du rythme d'un morceau : tempo, temps (beats) et debut de mesure.

Methode : enveloppe d'attaques (flux spectral) -> tempo par autocorrelation ->
suivi des temps par programmation dynamique (methode d'Ellis, 2007).
Resultats mis en cache dans data/beats (un fichier par morceau).
"""
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

from .config import DATA_DIR

BEAT_DIR = DATA_DIR / "beats"
SR = 11025
N_FFT = 1024
HOP = 256
FPS = SR / HOP
_FLAGS = 0x08000000 if sys.platform == "win32" else 0


def cache_file(path):
    st = os.stat(path)
    key = hashlib.sha1(("%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))).encode("utf-8"))
    return BEAT_DIR / (key.hexdigest() + ".json")


def load_cached(path):
    try:
        f = cache_file(path)
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _band_matrix():
    """Regroupe les frequences en 24 bandes logarithmiques (30 Hz - 5 kHz) de poids egal,
    pour que la grosse caisse et la basse comptent autant que les cymbales."""
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    edges = np.geomspace(30, 5000, 25)
    m = np.zeros((len(freqs), 24), dtype=np.float32)
    for b in range(24):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not sel.any():
            sel = np.abs(freqs - (edges[b] + edges[b + 1]) / 2) == np.abs(freqs - (edges[b] + edges[b + 1]) / 2).min()
        m[sel, b] = 1.0 / sel.sum()
    return m


def onset_envelope(signal):
    win = np.hanning(N_FFT).astype(np.float32)
    bands = _band_matrix()
    n_frames = 1 + (len(signal) - N_FFT) // HOP
    flux = np.zeros(n_frames, dtype=np.float32)
    prev = None
    for start in range(0, n_frames, 2000):
        stop = min(n_frames, start + 2000)
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(start, stop)[:, None]
        mag = np.abs(np.fft.rfft(signal[idx] * win, axis=1))
        spec = np.log1p(1000.0 * (mag @ bands))
        first = prev[None, :] if prev is not None else spec[:1]
        d = np.maximum(0.0, np.diff(np.vstack([first, spec]), axis=0)).sum(axis=1)
        flux[start:stop] = d
        prev = spec[-1]
    k = int(FPS * 0.5) | 1
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0.0, flux - local)
    sd = env.std()
    return env / sd if sd > 0 else env


def estimate_period(env):
    n = len(env)
    f = np.fft.rfft(env - env.mean(), 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    lags = np.arange(n, dtype=np.float64)
    lo, hi = int(FPS * 60 / 200), int(FPS * 60 / 60)
    lag120 = FPS * 60 / 120
    w = np.exp(-0.5 * (np.log2(np.maximum(lags[lo:hi], 1) / lag120) / 0.6) ** 2)
    best = lo + int(np.argmax(ac[lo:hi] * w))
    # affinage parabolique
    if 1 <= best < n - 1:
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        den = a - 2 * b + c
        if den != 0:
            return best + 0.5 * (a - c) / den
    return float(best)


def track_beats(env, period, tightness=100.0):
    n = len(env)
    score = env.astype(np.float64).copy()
    back = -np.ones(n, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(lo, hi + 1)
    cost = -tightness * np.log(offsets / period) ** 2
    for t in range(hi, n):
        prev = t - offsets
        cand = score[prev] + cost
        j = int(np.argmax(cand))
        score[t] = env[t] + cand[j]
        back[t] = prev[j]
    tail = max(0, n - int(round(period)))
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1])


def analyze(ffmpeg, path):
    out = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, timeout=300, creationflags=_FLAGS).stdout
    sig = np.frombuffer(out, dtype=np.float32)
    if len(sig) < SR * 5:
        return None
    env = onset_envelope(sig)
    period = estimate_period(env)
    frames = track_beats(env, period)
    if len(frames) < 8:
        return None
    strength = [float(env[frames[p::4]].mean()) for p in range(4)]
    result = {"bpm": round(60 * FPS / period, 1),
              "beats": [round(float(f) / FPS + N_FFT / 2 / SR, 3) for f in frames],
              "downbeat": int(np.argmax(strength))}
    try:
        BEAT_DIR.mkdir(parents=True, exist_ok=True)
        cache_file(path).write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass
    return result
