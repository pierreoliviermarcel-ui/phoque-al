"""Jingles du POP mode, synthétisés par le logiciel (aucun fichier son fourni, aucun droit à gérer).

make(n, path) écrit le jingle n (1 à 6) en WAV stéréo 44,1 kHz.
"""
import wave

import numpy as np

SR = 44100
NAMES = {
    1: "Bulle néon (lumineux)",
    2: "Double pop grave (club)",
    3: "Scintillement (cyberpunk)",
    4: "Laser rebond",
    5: "Goutte sensuelle (doux)",
    6: "Glitch néon",
}


def _env(n, a=0.005, r=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.exp(-t / r)


def _note(m):
    return 440 * 2 ** ((m - 69) / 12)


def _pop(rng, dur=0.14, f0=950, f1=140):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t * 38)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.001, 0.045)
    return s + rng.standard_normal(n) * _env(n, 0.0003, 0.004) * 0.5


def _tone(f, dur, a=0.004, r=0.25, det=0.004, saw=0.25):
    n = int(SR * dur)
    t = np.arange(n) / SR
    o = 0
    for d in (-det, 0, det):
        ph = 2 * np.pi * f * (1 + d) * t
        o = o + np.sin(ph) + saw * np.sin(2 * ph) / 2 + saw * np.sin(3 * ph) / 3 * 0.6
    return o / 3 * _env(n, a, r)


def _bass(f, dur):
    n = int(SR * dur)
    ph = 2 * np.pi * f * np.arange(n) / SR
    return (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)) * _env(n, 0.01, 0.35)


def _bell(f, dur, idx=2.2):
    n = int(SR * dur)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * f * t + idx * np.exp(-t * 9) * np.sin(2 * np.pi * f * 3.5 * t)) * _env(n, 0.002, 0.16)


def _rhodes(f, dur):
    n = int(SR * dur)
    t = np.arange(n) / SR
    trem = 1 + 0.18 * np.sin(2 * np.pi * 5.2 * t)
    s = np.sin(2 * np.pi * f * t + 0.9 * np.exp(-t * 4) * np.sin(2 * np.pi * f * t))
    return s * trem * _env(n, 0.006, 0.55)


def _drop(dur=0.22):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 600 + 1500 * (1 - np.exp(-t * 30))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.05)


def _zap(dur=0.5, f0=2400, f1=180):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t * 9)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sign(np.sin(ph)) * 0.35 + np.sin(ph) * 0.65) * _env(n, 0.002, 0.12)


def _mix(parts, total):
    out = np.zeros(int(SR * total))
    for st, s, g in parts:
        i = int(SR * st)
        k = min(len(s), len(out) - i)
        if k > 0:
            out[i:i + k] += g * s[:k]
    return out


def _reverb(x, amount=0.3):
    y = x.copy()
    for d, g in ((0.031, 0.5), (0.047, 0.42), (0.071, 0.35), (0.113, 0.27), (0.167, 0.2), (0.229, 0.14), (0.311, 0.09)):
        k = int(SR * d)
        y[k:] += g * x[:-k]
    return (1 - amount) * x + amount * y


def _echo(x, delay=0.16, fb=0.45, n=4):
    y = x.copy()
    k = int(SR * delay)
    for i in range(1, n + 1):
        y[k * i:] += (fb ** i) * x[:len(x) - k * i]
    return y


def _crush(x, bits=5, hold=6):
    q = np.round(x * (2 ** bits)) / (2 ** bits)
    return np.repeat(q[::hold], hold)[:len(x)]


def _stereo(x, w=0.012):
    k = int(SR * w)
    r = np.concatenate([np.zeros(k), x[:-k]])
    return np.stack([x * 0.9 + r * 0.1, x * 0.1 + r * 0.9], 1)


def build(n):
    rng = np.random.default_rng(3 + n)
    if n == 1:
        p = [(0, _pop(rng), 1.0)] + [(0.10 + i * 0.075, _tone(_note(m), 0.5, r=0.18), 0.32)
                                     for i, m in enumerate((76, 79, 83, 88))]
        p.append((0.42, _tone(_note(95), 0.9, r=0.35, saw=0.05), 0.18))
        x = _reverb(_mix(p, 1.5))
    elif n == 2:
        p = [(0, _pop(rng, 0.12, 800, 160), 0.9), (0.16, _pop(rng, 0.12, 1150, 200), 0.8),
             (0.30, _bass(_note(40), 0.9), 0.55)]
        p += [(0.30, _tone(_note(m), 0.9, r=0.4, saw=0.4), g) for m, g in ((64, 0.18), (67, 0.16), (71, 0.14))]
        x = _reverb(_mix(p, 1.6), 0.35)
    elif n == 3:
        p = [(0, _pop(rng, 0.15, 1000, 120), 1.0)] + [(0.09 + i * 0.045, _bell(_note(m), 0.4), 0.22)
                                                       for i, m in enumerate((84, 91, 88, 96, 93, 100))]
        x = _reverb(_mix(p, 1.4), 0.33)
    elif n == 4:
        p = [(0, _pop(rng, 0.13, 900, 150), 0.9), (0.08, _zap(), 0.32), (0.18, _bass(_note(36), 0.7), 0.45)]
        x = _reverb(_echo(_mix(p, 1.5), 0.14, 0.42, 4), 0.25)
    elif n == 5:
        p = [(0, _drop(), 0.8), (0.12, _drop(0.18), 0.45)]
        p += [(0.22 + i * 0.03, _rhodes(_note(m), 1.2), 0.22) for i, m in enumerate((57, 64, 67, 71, 74))]
        x = _reverb(_mix(p, 1.8), 0.4)
    else:
        a = _mix([(0, _pop(rng, 0.13, 1000, 140), 1.0)], 0.13)
        stut = np.concatenate([a[:int(SR * 0.04)]] * 3)
        p = [(0, a, 1.0), (0.14, _crush(stut, 4, 8), 0.7),
             (0.28, _crush(_tone(_note(69), 0.5, r=0.2, saw=0.8), 5, 4), 0.35),
             (0.28, _tone(_note(76), 0.6, r=0.25, saw=0.6), 0.2), (0.28, _bass(_note(45), 0.6), 0.4)]
        x = _reverb(_mix(p, 1.3), 0.25)
    x = _stereo(x)
    x = x / (np.max(np.abs(x)) or 1) * 0.85
    fade = int(SR * 0.05)
    x[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return x


def make(n, path):
    x = build(n)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    return path
