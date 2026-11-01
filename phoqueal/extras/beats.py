"""Détection du rythme d'un fichier audio ou vidéo (FFmpeg + numpy, sans Qt).

analyse(chemin) -> {"bpm", "beats": [s], "acc": [0/1], "str": [0..1], "energy": [0..1] par temps, "dur"}
- enveloppe d'attaques (flux spectral sur les basses et médiums)
- tempo par autocorrélation (préférence douce autour de 120 BPM)
- suivi des temps par programmation dynamique (méthode d'Ellis)
- temps forts : la phase (1 sur 4) la plus riche en grosse caisse
"""
import json
import os
import subprocess
import sys

import numpy as np

SR = 11025
HOP = 256                        # ~23 ms
NFFT = 1024
_FLAGS = 0x08000000 if sys.platform == "win32" else 0
VERSION = 2


def decode(ffmpeg, path, start=0.0, dur=None):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(SR)]
    if dur:
        cmd += ["-t", "%.3f" % dur]
    cmd += ["-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=_FLAGS).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(x):
    if len(x) < NFFT * 4:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    n = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(NFFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    freqs = np.fft.rfftfreq(NFFT, 1.0 / SR)
    logs = np.log1p(spec * 100.0)
    flux = np.maximum(0.0, np.diff(logs, axis=0, prepend=logs[:1]))
    low = flux[:, freqs < 160].sum(axis=1)          # grosse caisse
    mid = flux[:, (freqs >= 160) & (freqs < 4000)].sum(axis=1)
    # les basses (grosse caisse, basse) portent le temps ; les aigus (charleston) souvent le contretemps
    env = low / (low.std() + 1e-9) * 2.0 + mid / (mid.std() + 1e-9)
    rms = np.sqrt((x[idx] ** 2).mean(axis=1))
    # retire la tendance lente, normalise
    k = int(SR / HOP)                               # ~1 s
    if len(env) > k:
        trend = np.convolve(env, np.ones(k) / k, mode="same")
        env = np.maximum(0.0, env - trend)
    env = env / (env.std() + 1e-9)
    low = low / (low.std() + 1e-9)
    return env, low, rms


def tempo(env, lo=70.0, hi=180.0):
    fps = SR / HOP
    if len(env) < fps * 4:
        return 120.0
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(len(ac))
    bpm = np.zeros_like(ac, dtype=np.float64)
    bpm[1:] = 60.0 * fps / lags[1:]
    ok = (bpm >= lo) & (bpm <= hi)
    weight = np.exp(-0.5 * (np.log2(np.where(ok, bpm, 120) / 120.0) / 0.9) ** 2)
    score = np.where(ok, ac * weight, -np.inf)
    # renforce avec les harmoniques (2x, 4x le lag)
    for m in (2, 4):
        idx = np.minimum(lags * m, len(ac) - 1)
        score = score + np.where(ok, 0.5 * ac[idx] * weight, 0)
    lag = int(np.argmax(score))
    # affine sur le maximum local (interpolation parabolique)
    if 1 <= lag < len(ac) - 1:
        a, b, c = score[lag - 1], score[lag], score[lag + 1]
        if np.isfinite(a) and np.isfinite(c) and (a - 2 * b + c) != 0:
            lag = lag + 0.5 * (a - c) / (a - 2 * b + c)
    return 60.0 * fps / lag


def track(env, bpm, tightness=100.0):
    fps = SR / HOP
    period = 60.0 * fps / bpm
    n = len(env)
    score = env.astype(np.float64).copy()
    back = -np.ones(n, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    for t in range(lo, n):
        prev = np.arange(max(0, t - hi), t - lo + 1)
        if len(prev) == 0:
            continue
        pen = -tightness * (np.log((t - prev) / period)) ** 2
        cand = score[prev] + pen
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] += cand[j]
            back[t] = prev[j]
    # fin : meilleur score dans la derniere periode
    tail = np.arange(max(0, n - int(period) - 1), n)
    t = int(tail[np.argmax(score[tail])])
    beats = []
    while t >= 0:
        beats.append(t)
        t = int(back[t])
    beats = np.array(beats[::-1], dtype=np.float64)
    # complete le debut si la chaine s'arrete trop tot
    while len(beats) and beats[0] - period > 0:
        beats = np.insert(beats, 0, beats[0] - period)
    return beats


def analyse_signal(x):
    fps = SR / HOP
    env, low, rms = onset_envelope(x)
    if len(env) == 0:
        return None
    bpm = tempo(env)
    fr = track(env, bpm)
    fr = fr[(fr >= 0) & (fr < len(env))]
    if len(fr) < 4:
        return None
    fi = fr.astype(int)
    # temps forts : phase sur 4 la plus forte en basses
    best, phase = -1, 0
    for p in range(4):
        v = low[fi[p::4]].mean() if len(fi[p::4]) else 0
        if v > best:
            best, phase = v, p
    acc = [1 if (i - phase) % 4 == 0 else 0 for i in range(len(fi))]
    s = env[fi]
    s = np.clip(s / (np.percentile(s, 95) + 1e-9), 0, 1)
    # energie autour de chaque temps (sert au generateur : sections calmes / intenses)
    w = int(fps * 2)
    cs = np.concatenate([[0], np.cumsum(rms)])
    en = np.array([(cs[min(len(rms), f + w)] - cs[max(0, f - w)]) / max(1, min(len(rms), f + w) - max(0, f - w)) for f in fi])
    en = (en - en.min()) / (en.max() - en.min() + 1e-9)
    times = (fr * HOP + NFFT / 2) / SR + 0.02          # +20 ms : mesure sur pistes de test
    return {"v": VERSION, "bpm": round(float(60.0 / np.median(np.diff(times))), 2), "beats": [round(float(t), 3) for t in times],
            "acc": acc, "str": [round(float(v), 2) for v in s], "energy": [round(float(v), 2) for v in en],
            "dur": round(len(x) / SR, 2)}


def analyse(ffmpeg, path):
    return analyse_signal(decode(ffmpeg, path))


# ----------------------------------------------------------------------------------------
# Cache (table beats, par chemin + taille + date)
# ----------------------------------------------------------------------------------------
SCHEMA = "CREATE TABLE IF NOT EXISTS beats(path TEXT PRIMARY KEY, sig TEXT, data TEXT)"


def sig(path):
    st = os.stat(path)
    return "%d:%d:%d" % (st.st_size, int(st.st_mtime), VERSION)


def cached(conn, path):
    try:
        conn.execute(SCHEMA)
        r = conn.execute("SELECT sig, data FROM beats WHERE path=?", (path,)).fetchone()
        if r and r[0] == sig(path):
            return json.loads(r[1])
    except Exception:
        pass
    return None


def store(conn, path, data):
    conn.execute(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO beats(path, sig, data) VALUES (?,?,?)", (path, sig(path), json.dumps(data)))
    conn.commit()


def get(conn, ffmpeg, path):
    d = cached(conn, path)
    if d is None:
        d = analyse(ffmpeg, path)
        if d is not None:
            store(conn, path, d)
    return d
