"""Générateur de PMV : monte des vidéos (sans musique) sur une musique, coupe sur les temps.

- Densité des coupes selon l'énergie de la musique : calme = toutes les 4 à 8 mesures de temps,
  montée = tous les 2 temps, refrain = à chaque temps (ou rythme fixe au choix).
- Extraits pris de préférence sur les débuts de scène et les moments favoris, sinon au hasard
  dans les 80 % de fin de la vidéo.
- Effets calés sur les temps : flash néon, zoom « punch », glitch RGB, secousse, fondu au noir
  sur les passages calmes, look néon (couleurs). Intensité réglable.
- v2.5 : VHS, contours néon, traînées, boomerang, arrêt sur image, pixels, bandes cinéma, négatif éclair,
  transitions (balayage, tourbillon, brûlure), mots néon sur les temps forts et titre d'intro, images
  intenses sur les refrains (passages forts), format vertical, boucle de 30 s, plusieurs versions d'un coup.
- Le fichier final s'appelle « PMV Phoque Al - … » : la frise néon s'affiche toute seule à la lecture
  (ses temps sont déjà connus, aucune analyse à refaire).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import db
from . import beats as B
from .common import log, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
NEON = "0xFF2BD6"
CYANX = "0x22F3FF"
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")


# ----------------------------------------------------------------------------------------
# Plan de montage (pur, testable)
# ----------------------------------------------------------------------------------------
def build_plan(data, total=None, mode="auto", fx=None, intensity=1.0, rng=None):
    """-> liste de segments {t0, t1, fx: [...], calm: bool}. Les coupes tombent sur les temps."""
    rng = rng or random.Random()
    fx = fx or {}
    bt, acc, en = data["beats"], data["acc"], data.get("energy") or [0.5] * len(data["beats"])
    dur = data.get("dur") or (bt[-1] + 1 if bt else 0)
    if total:
        dur = min(dur, total)
    bt = [t for t in bt if t < dur - 0.25]
    if len(bt) < 2:
        return [{"t0": 0.0, "t1": dur, "fx": [], "calm": True}]
    cuts = [0.0]
    i = 0
    if bt[0] > 0.6:                                     # intro avant le premier temps
        cuts.append(bt[0])
    while i < len(bt):
        e = en[i] if i < len(en) else 0.5
        if mode == "auto":
            step = 8 if e < 0.15 else 4 if e < 0.40 else 2 if e < 0.72 else 1
            if step == 1 and rng.random() < 0.25:
                step = 2                                 # un peu de respiration dans les refrains
            if step >= 4:                                # les plans longs partent d'un temps fort
                j = i + step
                while j < len(bt) and not acc[j] and j < i + step + 3:
                    j += 1
                step = j - i
        else:
            step = max(1, int(mode))
        i += step
        if i < len(bt):
            cuts.append(bt[i])
    if dur - cuts[-1] < 0.25:
        cuts[-1] = dur
    else:
        cuts.append(dur)
    segs = []
    bset = {round(t, 3): k for k, t in enumerate(bt)}
    for t0, t1 in zip(cuts, cuts[1:]):
        if t1 - t0 < 0.08:
            continue
        k = bset.get(round(t0, 3))
        a = bool(acc[k]) if k is not None else False
        e = en[k] if k is not None and k < len(en) else 0.3
        calm = e < 0.40 and (t1 - t0) > 1.2
        f = []
        if k is not None:
            if fx.get("flash") and a and e >= 0.40:
                f.append("flash")
            if fx.get("zoom") and (a or e >= 0.72) and rng.random() < 0.85 * intensity:
                f.append("zoom")
            if fx.get("glitch") and e >= 0.60 and (a or rng.random() < 0.25 * intensity):
                f.append("glitch")
            if fx.get("shake") and e >= 0.72 and not a and rng.random() < 0.5 * intensity:
                f.append("shake")
        if fx.get("fade") and calm:
            f.append("fade")
        f += extra_fx(fx, a, e, t1 - t0, calm, intensity, rng)
        segs.append({"t0": round(t0, 3), "t1": round(t1, 3), "fx": f, "calm": calm, "acc": a, "e": e})
    return segs


TIME_FX = ("boom", "freeze")
TRANS_FX = ("whip", "spin", "burn")


def extra_fx(fx, a, e, d, calm, intensity, rng):
    """Effets ajoutés en v2.5 (chacun activable dans la fenêtre)."""
    k = max(0.3, min(1.6, intensity))
    f = []
    if fx.get("neon") and a and e >= 0.70 and rng.random() < 0.55 * k:
        f.append("neon")
    if fx.get("invert") and a and e >= 0.85 and rng.random() < 0.30 * k:
        f.append("invert")
    if fx.get("pixel") and e >= 0.60 and not a and rng.random() < 0.22 * k:
        f.append("pixel")
    if fx.get("ghost") and 0.30 <= e < 0.75 and d >= 0.8 and rng.random() < 0.35:
        f.append("ghost")
    if fx.get("vhs") and e < 0.55 and rng.random() < 0.40:
        f.append("vhs")
    if fx.get("bars") and a and e >= 0.50 and rng.random() < 0.30 * k:
        f.append("bars")
    # un seul effet « de temps » par extrait
    if fx.get("boom") and 0.6 <= d <= 2.4 and e >= 0.50 and rng.random() < 0.18 * k:
        f.append("boom")
    elif fx.get("freeze") and a and e >= 0.60 and 0.4 <= d <= 1.6 and rng.random() < 0.14 * k:
        f.append("freeze")
    # une transition d'entrée au plus
    tr = [t for t in TRANS_FX if fx.get(t) and (t != "burn" or e < 0.6)]
    if tr and rng.random() < 0.30 * k:
        f.append(rng.choice(tr))
    return f


STYLES = [
    ("classique", "Classique : une vidéo, coupes sur les temps"),
    ("stutter", "Stutter : extraits répétés et hachés sur les temps forts"),
    ("split", "Écran partagé : 2 vidéos côte à côte"),
    ("grille", "Grille 2×2 : 4 vidéos qui changent ensemble"),
    ("miroir", "Miroir / kaléidoscope"),
    ("pulse", "Pulse : l'image bat avec la musique (zoom à chaque temps)"),
    ("vitesse", "Ralenti / turbo : la vitesse suit l'énergie"),
    ("mix", "Mix : le style change avec la musique (conseillé)"),
]
NEED = {"split": 2, "grille": 4}


def apply_style(segs, style, period, rng=None):
    """Attribue un style à chaque segment (et découpe / répète pour le stutter)."""
    rng = rng or random.Random()
    out = []
    for s in segs:
        e = s.get("e", 0.5)
        st = style
        if style == "mix":
            pool = (["miroir", "vitesse", "classique"] if e < 0.40 else
                    ["split", "pulse", "classique", "miroir"] if e < 0.72 else
                    ["grille", "stutter", "pulse", "split"])
            st = rng.choice(pool)
        s["style"] = st
        s["period"] = period
        s["speed"] = 1.0
        if st == "vitesse":
            s["speed"] = 0.5 if e < 0.40 else 1.0 if e < 0.72 else 1.6
        if st == "miroir":
            s["mirror"] = rng.choice((2, 2, 4))
        if st == "stutter":
            d = s["t1"] - s["t0"]
            if d <= period * 1.3 and e >= 0.55:
                # un temps coupé en deux (ou en quatre sur les temps forts) avec le MEME extrait
                parts = 4 if s.get("acc") and d >= period * 0.9 else 2
                step = d / parts
                for k in range(parts):
                    p = dict(s)
                    p["t0"], p["t1"] = round(s["t0"] + k * step, 3), round(s["t0"] + (k + 1) * step, 3)
                    p["rep"] = k > 0
                    if k > 0:
                        p["fx"] = [f for f in s.get("fx", []) if f in ("glitch",)]
                    out.append(p)
                continue
            if out and rng.random() < 0.3:
                s["rep"] = True                     # reprend l'extrait precedent depuis son debut
        out.append(s)
    return out


def pick_clips(segs, pool, rng=None):
    """pool : [{vid, path, dur, starts: [s]}]. Ajoute src/ss (et srcs pour les styles à plusieurs vidéos)."""
    rng = rng or random.Random()
    recent = []
    used = set()

    def one(need, e=0.5):
        cands = [c for c in pool if c["dur"] > need + 2 and c["vid"] not in recent] or \
                [c for c in pool if c["dur"] > need + 1] or pool
        if e >= 0.72 and rng.random() < 0.7:
            hot = [c for c in cands if c.get("hot")]
            if hot:
                cands = hot                     # refrain : de préférence une vidéo qui a des passages forts
        c = rng.choice(cands)
        lo, hi = max(0.0, c["dur"] * 0.2), max(0.0, c["dur"] - need - 0.5)
        if c.get("fixed") and c.get("starts"):
            # moment favori / passage fort : on part de cet instant (un peu decale s'il revient)
            base = c["starts"][0]
            ss = base + (rng.uniform(0, 3) if (c["vid"], round(base, 1)) in used else 0.0)
            ss = max(0.0, min(ss, max(0.0, c["dur"] - need - 0.5)))
            used.add((c["vid"], round(base, 1)))
            recent.append(c["vid"])
            del recent[:-max(1, min(6, len(pool) - 1))]
            return c, round(ss, 2)
        hot = [x for x in c.get("hot", []) if lo <= x <= hi and (c["vid"], round(x, 1)) not in used]
        if e >= 0.72 and hot and rng.random() < 0.8:
            # les images les plus intenses sur les moments les plus intenses de la musique
            ss = rng.choice(hot) + rng.uniform(0, 2.0)
            ss = max(0.0, min(ss, hi))
            used.add((c["vid"], round(ss, 1)))
            recent.append(c["vid"])
            del recent[:-max(1, min(6, len(pool) - 1))]
            return c, round(ss, 2)
        good = [x for x in c.get("starts", []) if lo <= x <= hi and (c["vid"], round(x, 1)) not in used]
        if e < 0.40 and c.get("hot"):
            calm = [x for x in good if all(abs(x - h) > 15 for h in c["hot"])]
            good = calm or good                 # couplets calmes : on évite les passages forts
        ss = rng.choice(good) if good and rng.random() < 0.7 else (rng.uniform(lo, hi) if hi > lo else 0.0)
        used.add((c["vid"], round(ss, 1)))
        recent.append(c["vid"])
        del recent[:-max(1, min(6, len(pool) - 1))]
        return c, round(ss, 2)

    prev = None
    for s in segs:
        if s.get("rep") and prev is not None:
            s["src"], s["ss"], s["vid"] = prev["src"], prev["ss"], prev["vid"]
            if "srcs" in prev:
                s["srcs"] = prev["srcs"]
            continue
        need = (s["t1"] - s["t0"]) * s.get("speed", 1.0) + 0.5
        e = s.get("e", 0.5)
        c, ss = one(need, e)
        s["src"], s["ss"], s["vid"] = c["path"], ss, c["vid"]
        n = NEED.get(s.get("style"), 1)
        if n > 1:
            s["srcs"] = [(c["path"], ss)] + [(lambda r: (r[0]["path"], r[1]))(one(need, e)) for _ in range(n - 1)]
        prev = s
    return segs


def base_chain(W, H, fps, speed=1.0):
    out = ["scale=%d:%d:force_original_aspect_ratio=increase" % (W, H), "crop=%d:%d" % (W, H), "setsar=1"]
    if abs(speed - 1.0) > 0.01:
        out.append("setpts=PTS/%.3f" % speed)
    out.append("fps=%d" % fps)
    return out


def style_chain(seg, W, H, fps, intensity):
    st = seg.get("style")
    k = max(0.3, min(1.6, intensity))
    if st == "miroir":
        if seg.get("mirror") == 4:
            return ["crop=iw/2:ih/2:0:0,split[a][b];[b]hflip[c];[a][c]hstack,split[d][e];[e]vflip[f];[d][f]vstack"]
        return ["crop=iw/2:ih:0:0,split[a][b];[b]hflip[c];[a][c]hstack"]
    if st == "pulse":
        p = max(2.0, seg.get("period", 0.5) * fps)
        return ["zoompan=z='1+%.3f*exp(-mod(on,%.2f)/%.2f)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=%dx%d:fps=%d"
                % (0.10 * k, p, p / 4.0, W, H, fps)]
    return []


def time_chain(seg, W, H, fps):
    """Boomerang (aller-retour) ou arrêt sur image avec zoom : change le déroulé de l'extrait."""
    f = seg.get("fx", [])
    d = max(0.1, seg["t1"] - seg["t0"])
    if "boom" in f:
        return ["trim=duration=%.3f,setpts=PTS-STARTPTS,split[ba][bb];[bb]reverse[br];[ba][br]concat=n=2:v=1:a=0" % (d / 2 + 0.05)]
    if "freeze" in f:
        n = max(2, int(round(d * fps)) + 2)
        return ["trim=end_frame=1,loop=loop=%d:size=1:start=0,setpts=N/%d/TB" % (n, fps),
                "zoompan=z='1+0.18*on/%d':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=%dx%d:fps=%d" % (n, W, H, fps)]
    return []


def font_path():
    for p in ("C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.isfile(p):
            return p
    return None


def text_chain(seg, W, H):
    """Texte néon : fichiers relatifs au dossier de travail (aucun « : » à échapper dans les chemins)."""
    out = []
    if seg.get("titlefile"):
        out.append("drawtext=fontfile=font.ttf:textfile=%s:expansion=none:fontsize=%d:fontcolor=white:borderw=%d:"
                   "bordercolor=0x22F3FF:x=(w-text_w)/2:y=(h-text_h)/2:alpha='min(1,t/0.4)':enable='lt(t,%.2f)'"
                   % (seg["titlefile"], max(24, min(W, H) // 9), max(2, min(W, H) // 180), min(2.8, seg["t1"] - seg["t0"])))
    if seg.get("wordfile"):
        out.append("drawtext=fontfile=font.ttf:textfile=%s:expansion=none:fontsize=%d:fontcolor=white:borderw=%d:"
                   "bordercolor=0xFF2BD6:x=(w-text_w)/2:y=(h-text_h)/2:"
                   "alpha='if(lt(t,0.04),t/0.04,max(0,1-(t-0.30)/0.15))':enable='lt(t,0.45)'"
                   % (seg["wordfile"], max(24, min(min(W, H) // 6, int(W * 1.5 / max(4, len(seg.get("word") or "xxxx"))))),
                      max(3, min(W, H) // 120)))
    return out


def post_chain(seg, W, H, fps, look, intensity, effects=True):
    out = []
    if look:
        out.append("eq=contrast=1.08:saturation=1.35,colorbalance=rs=0.05:gs=-0.04:bs=0.10:rh=0.04:bh=0.07")
    if effects:
        k = max(0.3, min(1.6, intensity))
        f = seg.get("fx", [])
        if "zoom" in f and seg.get("style") != "pulse":
            z0 = 1 + 0.16 * k
            out.append("zoompan=z='if(lt(on,9),%.3f-(%.3f-1)*on/9,1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
                       ":d=1:s=%dx%d:fps=%d" % (z0, z0, W, H, fps))
        if "shake" in f:
            a = 0.014 * k
            out.append("crop=w=iw*0.95:h=ih*0.95:x='(iw-ow)/2+if(lt(t,0.3),iw*%.4f*sin(t*70),0)'"
                       ":y='(ih-oh)/2+if(lt(t,0.3),ih*%.4f*cos(t*55),0)',scale=%d:%d,setsar=1" % (a, a, W, H))
        if "glitch" in f:
            px = int(10 * k) + 4
            out.append("rgbashift=rh=-%d:bh=%d:gv=%d:enable='lt(t,0.13)'" % (px, px, px // 2))
            out.append("noise=alls=%d:allf=t:enable='lt(t,0.1)'" % int(30 * k))
        if "flash" in f:
            out.append("fade=t=in:st=0:d=%.2f:color=%s" % (0.12 + 0.08 * k, NEON if seg.get("acc") else CYANX))
        if "fade" in f:
            out.append("fade=t=in:st=0:d=0.35")
        if "vhs" in f:
            out.append("chromashift=cbh=%d:crh=-%d,noise=alls=%d:allf=t,eq=saturation=1.2:contrast=1.06,"
                       "gblur=sigma=0.6,drawgrid=w=iw:h=4:t=1:c=black@0.30" % (3 + int(3 * k), 3 + int(3 * k), int(14 * k) + 4))
        if "ghost" in f:
            out.append("tmix=frames=4:weights='1 0.7 0.45 0.25'")
        if "neon" in f:
            out.append("format=gbrp,split[na][nb];[nb]edgedetect=low=0.04:high=0.12:mode=wires,"
                       "colorchannelmixer=rr=1:rg=0:rb=0:gr=0.17:gg=0:gb=0:br=0.84:bg=0:bb=0,gblur=sigma=1.5,"
                       "lutrgb=r='min(255,val*3)':g='min(255,val*3)':b='min(255,val*3)'[ne];"
                       "[na][ne]blend=all_mode=screen:enable='lt(t,%.2f)',format=yuv420p" % (0.25 + 0.15 * k))
        if "pixel" in f:
            out.append("split[pa][pb];[pb]scale=%d:%d:flags=neighbor,scale=%d:%d:flags=neighbor[pp];"
                       "[pa][pp]overlay=enable='lt(t,%.2f)'" % (max(8, W // 28), max(6, H // 28), W, H, 0.12 + 0.08 * k))
        if "invert" in f:
            out.append("negate=enable='lt(t,0.07)'")
        if "bars" in f:
            hb = int(H * 0.10 * k)
            out.append("drawbox=x=0:y=0:w=iw:h=%d:color=black:t=fill:enable='lt(t,0.22)',"
                       "drawbox=x=0:y=ih-%d:w=iw:h=%d:color=black:t=fill:enable='lt(t,0.22)'" % (hb, hb, hb))
        if "whip" in f:
            out.append("gblur=sigma=%d:sigmaV=0.01:enable='lt(t,0.10)'" % int(30 * k + 10))
        if "spin" in f:
            out.append("rotate=a='%.3f*exp(-t*14)':fillcolor=black" % (0.35 * k))
        if "burn" in f:
            out.append("fade=t=in:st=0:d=0.28:color=0xFF6A00")
        out += text_chain(seg, W, H)
    out.append("tpad=stop_mode=clone:stop_duration=3")
    return out


def vf_chain(seg, W, H, fps, look, intensity, effects=True):
    """Chaîne de filtres d'un segment à une seule vidéo."""
    st = seg.get("style") if effects else None
    s2 = dict(seg, style=st) if effects else dict(seg, style=None)
    parts = base_chain(W, H, fps, seg.get("speed", 1.0)) + (style_chain(s2, W, H, fps, intensity) if effects else []) \
        + (time_chain(s2, W, H, fps) if effects and st not in ("pulse",) else []) \
        + post_chain(s2, W, H, fps, look, intensity, effects)
    return ",".join(parts)


def seg_args(seg, W, H, fps, look, intensity, effects=True):
    """Arguments FFmpeg (entrées + filtres) d'un segment, selon son style."""
    n = NEED.get(seg.get("style"), 1)
    srcs = seg.get("srcs") or []
    if effects and n > 1 and len(srcs) >= n:
        vert = H > W
        tw, th = ((W, H // 2) if vert else (W // 2, H)) if n == 2 else (W // 2, H // 2)
        args, g = [], []
        for i, (path, ss) in enumerate(srcs[:n]):
            args += ["-ss", "%.2f" % ss, "-i", path]
            g.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,fps=%d[v%d]"
                     % (i, tw, th, tw, th, fps, i))
        if n == 2:
            g.append("[v0][v1]%s=inputs=2[s]" % ("vstack" if vert else "hstack"))
        else:
            g.append("[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[s]")
        g.append("[s]" + ",".join(post_chain(seg, W, H, fps, look, intensity, True)) + "[out]")
        return args + ["-filter_complex", ";".join(g), "-map", "[out]"]
    return ["-ss", "%.2f" % seg["ss"], "-i", seg["src"], "-vf", vf_chain(seg, W, H, fps, look, intensity, effects)]


def add_words(segs, words, title=None, intensity=1.0, rng=None):
    """Mots qui claquent sur les temps forts (en alternance), titre sur le premier extrait."""
    rng = rng or random.Random()
    words = [w.strip() for w in words if w and w.strip()]
    if title and segs:
        segs[0]["title"] = title.strip()[:60]
    if not words:
        return segs
    k, last = 0, -9
    for i, s in enumerate(segs):
        if s.get("title") or s.get("rep"):
            continue
        if s.get("acc") and s.get("e", 0) >= 0.55 and i - last >= 2 and rng.random() < 0.45 * max(0.5, intensity):
            s["word"] = words[k % len(words)][:30]
            k += 1
            last = i
    return segs


# ----------------------------------------------------------------------------------------
# Rendu (FFmpeg)
# ----------------------------------------------------------------------------------------
class Render:
    def __init__(self, ffmpeg, music, pool, out_path, opts, conn_factory=None):
        self.ffmpeg, self.music, self.pool, self.out = ffmpeg, music, pool, out_path
        self.o = opts
        self.conn_factory = conn_factory
        self.done = 0
        self.total = 1
        self.step = "Analyse de la musique…"
        self.error = None
        self.finished = False
        self.cancel = False
        self.result = None

    def run(self):
        try:
            self._run()
        except Exception as e:
            log("pmv : %r" % e)
            self.error = str(e)
        self.finished = True

    def seg_audio(self, seg, dur, out):
        """Son d'origine de l'extrait (même durée exacte que l'image), ou silence."""
        src, ss = seg.get("src"), seg.get("ss", 0)
        if seg.get("srcs"):
            src, ss = seg["srcs"][0]
        sp = seg.get("speed", 1.0) or 1.0
        af = ["aresample=48000"]
        if abs(sp - 1.0) > 0.01:
            af.append("atempo=%.3f" % max(0.5, min(2.0, sp)))
        af.append("apad")
        ok, _err = self._ff(["-ss", "%.2f" % ss, "-i", src, "-vn", "-af", ",".join(af), "-t", "%.4f" % dur,
                             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out])
        if not ok:
            ok, _err = self._ff(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "%.4f" % dur,
                                 "-c:a", "pcm_s16le", out])
        return out if ok else None

    def _ff(self, args, timeout=300):
        r = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"] + args,
                           capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=_FLAGS,
                           cwd=getattr(self, "cwd", None))
        return r.returncode == 0, r.stderr[-400:]

    def plan(self):
        """Analyse la musique et prépare la liste des extraits (sans rien rendre)."""
        o = self.o
        conn = self.conn_factory() if self.conn_factory else None
        data = B.get(conn, self.ffmpeg, self.music) if conn is not None else B.analyse(self.ffmpeg, self.music)
        if not data or len(data.get("beats", [])) < 4:
            raise RuntimeError("rythme de la musique introuvable")
        rng = random.Random(o.get("seed"))
        total = o.get("minutes") and o["minutes"] * 60
        segs = build_plan(data, total, o.get("mode", "auto"), o.get("fx"), o.get("intensity", 1.0), rng)
        bt = data["beats"]
        period = float(sorted(b - a for a, b in zip(bt, bt[1:]))[len(bt) // 2]) if len(bt) > 2 else 0.5
        segs = apply_style(segs, o.get("style", "classique"), period, rng)
        segs = pick_clips(segs, self.pool, rng)
        add_words(segs, o.get("words") or [], o.get("title"), o.get("intensity", 1.0), rng)
        self.data, self.segs, self.rng = data, segs, rng
        return segs

    def reroll(self, i):
        """Autre extrait pour le segment i (et ses répétitions de stutter)."""
        s = self.segs[i]
        others = [c for c in self.pool if c["vid"] != s.get("vid")] or self.pool
        tmp = [dict(s, rep=False)]
        pick_clips(tmp, others, self.rng)
        for j in range(i, len(self.segs)):
            if j > i and not self.segs[j].get("rep"):
                break
            for k in ("src", "ss", "vid", "srcs"):
                if k in tmp[0]:
                    self.segs[j][k] = tmp[0][k]

    def drop_video(self, vid):
        """Retire une vidéo de ce PMV : tous ses extraits sont remplacés."""
        self.pool = [c for c in self.pool if c["vid"] != vid] or self.pool
        for i, s in enumerate(self.segs):
            if s.get("vid") == vid and not s.get("rep"):
                self.reroll(i)

    def _run(self):
        o = self.o
        conn = self.conn_factory() if self.conn_factory else None
        if getattr(self, "segs", None) is None:
            self.plan()
        data, segs, rng = self.data, self.segs, self.rng
        dur = segs[-1]["t1"]
        W, H = o.get("size", (1920, 1080))
        fps = o.get("fps", 30)
        tmp = tempfile.mkdtemp(prefix=".focal_pmv_", dir=os.path.dirname(self.out) or None)
        self.total = len(segs) + 1
        self.step = "Montage des extraits…"
        self.cwd = tmp
        try:
            fp = font_path()
            if fp and any(sg.get("word") or sg.get("title") for sg in segs):
                shutil.copyfile(fp, os.path.join(tmp, "font.ttf"))
                for i, sg in enumerate(segs):
                    for key in ("word", "title"):
                        if sg.get(key):
                            name = "%s%04d.txt" % (key[0], i)
                            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                                fh.write(sg[key])
                            sg[key + "file"] = name
            files = [None] * len(segs)
            auds = [None] * len(segs)

            def one(i):
                if self.cancel:
                    return
                s = segs[i]
                n = round(s["t1"] * fps) - round(s["t0"] * fps)
                if n <= 0:
                    files[i] = None
                    self.done += 1
                    return
                out = os.path.join(tmp, "s%04d.mp4" % i)
                common = ["-frames:v", str(n), "-an", "-c:v", "libx264", "-preset", o.get("preset", "veryfast"),
                          "-crf", str(o.get("crf", 20)), "-pix_fmt", "yuv420p", "-r", str(fps), "-video_track_timescale", "%d" % (fps * 1000), out]
                alt = rng.choice(self.pool)
                tries = [dict(s), dict(s, srcs=None), dict(s, style=None, speed=1.0)]
                tries.append(dict(s, src=alt["path"], ss=round(alt["dur"] * 0.4, 2), style=None, speed=1.0, srcs=None))
                ok, err = False, ""
                for k, t in enumerate(tries):
                    args = seg_args(t, W, H, fps, o.get("look"), o.get("intensity", 1.0), effects=k < 2)
                    ok, err = self._ff(args + common)
                    if ok:
                        break
                    log("pmv segment %d essai %d : %s" % (i, k, err))
                if not ok:
                    log("pmv segment %d : %s" % (i, err))
                    ok, err = self._ff(["-f", "lavfi", "-i", "color=c=black:s=%dx%d:r=%d" % (W, H, fps)] + common)
                files[i] = out if ok else None
                if ok and o.get("clip_audio", True):
                    auds[i] = self.seg_audio(t if ok else s, n / float(fps), os.path.join(tmp, "a%04d.wav" % i))
                self.done += 1

            with ThreadPoolExecutor(max_workers=o.get("workers", 3)) as ex:
                list(ex.map(one, range(len(segs))))
            if self.cancel:
                raise RuntimeError("annulé")
            self.step = "Assemblage avec la musique…"
            lst = os.path.join(tmp, "liste.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p in files:
                    if p:
                        f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
            fade = max(0.0, dur - 2.0)
            have_clips = o.get("clip_audio", True) and all(a for a, f in zip(auds, files) if f)
            if have_clips:
                alst = os.path.join(tmp, "sons.txt")
                with open(alst, "w", encoding="utf-8") as f:
                    for a, v in zip(auds, files):
                        if v:
                            f.write("file '%s'\n" % a.replace("\\", "/").replace("'", "'\\''"))
                # 3 pistes : musique + sons (par defaut), musique seule, sons des videos seuls
                g = ("[1:a]atrim=0:%.3f,afade=t=out:st=%.2f:d=2,asplit=2[m1][m2];"
                     "[2:a]dynaudnorm=f=150:g=15,atrim=0:%.3f,afade=t=out:st=%.2f:d=2,asplit=2[c1][c2];"
                     "[m1][c1]amix=inputs=2:weights='1 0.6':normalize=0:duration=first[mix]" % (dur, fade, dur, fade))
                extra_in = []
                if o.get("sfx"):
                    try:
                        sp = os.path.join(tmp, "sfx.wav")
                        write_wav(sp, sfx_track(segs, dur))
                        extra_in = ["-i", sp]
                        g = g.replace("[mix]", "[mx0]") + ";[3:a]atrim=0:%.3f[sx];[mx0][sx]amix=inputs=2:weights='1 0.5':normalize=0:duration=first[mix]" % dur
                    except Exception as e:
                        log("pmv bruitages : %r" % e)
                ok, err = self._ff(["-f", "concat", "-safe", "0", "-i", lst, "-i", self.music,
                                    "-f", "concat", "-safe", "0", "-i", alst] + extra_in + ["-filter_complex", g,
                                    "-map", "0:v", "-map", "[mix]", "-map", "[m2]", "-map", "[c2]",
                                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                    "-metadata:s:a:0", "title=Musique et sons des vidéos", "-metadata:s:a:0", "handler_name=Musique et sons des vidéos",
                                    "-metadata:s:a:1", "title=Musique seule", "-metadata:s:a:1", "handler_name=Musique seule",
                                    "-metadata:s:a:2", "title=Sons des vidéos (sans musique)", "-metadata:s:a:2", "handler_name=Sons des vidéos (sans musique)",
                                    "-disposition:a:0", "default", "-disposition:a:1", "0", "-disposition:a:2", "0",
                                    "-t", "%.3f" % dur, "-movflags", "+faststart", self.out], timeout=1800)
                if not ok:
                    log("pmv 3 pistes : %s" % err)
            if not have_clips or not ok:
                ok, err = self._ff(["-f", "concat", "-safe", "0", "-i", lst, "-i", self.music, "-map", "0:v", "-map", "1:a",
                                    "-c:v", "copy", "-af", "afade=t=out:st=%.2f:d=2" % fade, "-c:a", "aac", "-b:a", "192k",
                                    "-t", "%.3f" % dur, "-movflags", "+faststart", self.out], timeout=1800)
            if not ok:
                raise RuntimeError("assemblage : " + err)
            self.done = self.total
            # les temps de la musique sont ceux de la video : on les garde pour la frise
            keep = dict(data)
            n = len([t for t in data["beats"] if t < dur])
            for k in ("beats", "acc", "str", "energy"):
                keep[k] = data[k][:n]
            keep["dur"] = dur
            if conn is not None:
                try:
                    B.store(conn, self.out, keep)
                except Exception as e:
                    log("pmv beats : %r" % e)
            self.result = {"path": self.out, "segments": len(segs), "dur": dur, "bpm": data["bpm"]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def sfx_track(segs, dur, sr=48000, level=0.6):
    """Bruitages synthétisés calés sur le montage : « whoosh » avant les transitions,
    impact sourd sur les temps les plus forts, montée (riser) avant les gros refrains. -> tableau (n, 2)."""
    import numpy as np
    n = int(dur * sr) + sr
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(1234)

    def put(sig, t):
        i = int(t * sr)
        if i < 0:
            sig, i = sig[-i:], 0
        j = min(n, i + len(sig))
        if j > i:
            out[i:j] += sig[:j - i]

    def whoosh(d=0.35):
        m = int(d * sr)
        x = rng.standard_normal(m).astype(np.float32)
        # bruit filtré qui monte puis s'éteint (passe-bas glissant)
        env = np.sin(np.linspace(0, np.pi, m)) ** 2
        y = np.zeros(m, np.float32)
        a = np.linspace(0.02, 0.35, m)
        acc = 0.0
        for k in range(m):
            acc += a[k] * (x[k] - acc)
            y[k] = acc
        return (y * env * 1.8).astype(np.float32)

    def impact(d=0.5):
        m = int(d * sr)
        t = np.arange(m) / sr
        f = 48 + 90 * np.exp(-t * 18)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 7)
        y += 0.25 * rng.standard_normal(m) * np.exp(-t * 40)
        return (y * 0.9).astype(np.float32)

    def riser(d=1.8):
        m = int(d * sr)
        t = np.arange(m) / sr
        f = 300 + 1500 * (t / d) ** 2
        y = 0.25 * np.sin(2 * np.pi * np.cumsum(f) / sr) + 0.35 * rng.standard_normal(m) * (t / d)
        return (y * (t / d) ** 1.5 * 0.6).astype(np.float32)

    W, I, R = whoosh(), impact(), riser()
    prev_e = 0.0
    for sg in segs:
        f = sg.get("fx", [])
        t0, e = sg["t0"], sg.get("e", 0.5)
        if any(x in f for x in ("whip", "spin", "burn")):
            put(W, t0 - 0.28)
        if sg.get("acc") and e >= 0.85 and any(x in f for x in ("neon", "invert", "freeze", "zoom")):
            put(I, t0)
        if prev_e < 0.55 and e >= 0.80 and t0 > 3:
            put(R, t0 - len(R) / sr)
        prev_e = e
    out = np.tanh(out * level)
    return np.stack([out, out], axis=1)


def write_wav(path, data, sr=48000):
    import wave
    import numpy as np
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def review_dialog(parent, main, job):
    """Liste des extraits prévus : changer un extrait, retirer une vidéo, puis lancer le rendu."""
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                                   QVBoxLayout, QAbstractItemView)
    d = QDialog(parent)
    d.setWindowTitle("Extraits du PMV")
    d.resize(860, 560)
    lay = QVBoxLayout(d)
    lay.addWidget(QLabel("Double-clic sur une ligne : voir l'extrait. Les effets et les coupes restent calés sur la musique."))
    tab = QTableWidget(0, 6)
    tab.setHorizontalHeaderLabels(["Temps", "Durée", "Vidéo", "Départ", "Style", "Effets"])
    tab.setSelectionBehavior(QAbstractItemView.SelectRows)
    tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tab.horizontalHeader().setStretchLastSection(True)
    lay.addWidget(tab, 1)
    names = {}

    def title_of(vid):
        if vid not in names:
            try:
                r = main.conn.execute("SELECT title FROM videos WHERE id=?", (vid,)).fetchone()
                names[vid] = ("Vidéo %d" % vid) if main.discreet else (r[0] if r else str(vid))
            except Exception:
                names[vid] = str(vid)
        return names[vid]

    def fill():
        tab.setRowCount(len(job.segs))
        for i, s in enumerate(job.segs):
            m, sec = divmod(s["t0"], 60)
            fx = list(s.get("fx", [])) + (["« %s »" % s["word"]] if s.get("word") else [])
            vals = ["%d:%05.2f" % (m, sec), "%.2f s" % (s["t1"] - s["t0"]),
                    ("↻ " if s.get("rep") else "") + title_of(s.get("vid")), "%.1f s" % s.get("ss", 0),
                    s.get("style") or "", ", ".join(fx)]
            for c, v in enumerate(vals):
                tab.setItem(i, c, QTableWidgetItem(v))
        tab.resizeColumnsToContents()

    def sel():
        r = tab.currentRow()
        return r if 0 <= r < len(job.segs) else None

    def reroll():
        r = sel()
        if r is not None:
            while r > 0 and job.segs[r].get("rep"):
                r -= 1
            job.reroll(r)
            fill()
            tab.selectRow(r)

    def drop():
        r = sel()
        if r is not None:
            job.drop_video(job.segs[r].get("vid"))
            fill()

    def watch(row, _col=None):
        s = job.segs[row]
        try:
            main.ctrl.play(s.get("vid"))
            QTimerSingle = __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer
            QTimerSingle.singleShot(900, lambda: [w.player.set_time(int(s.get("ss", 0) * 1000))
                                                  for w in list(main.ctrl.players.values())
                                                  if getattr(w, "video_id", None) == s.get("vid") and hasattr(w, "player")])
        except Exception as e:
            log("pmv aperçu : %r" % e)
    tab.cellDoubleClicked.connect(watch)
    row = QHBoxLayout()
    b1 = QPushButton("Changer cet extrait"); b1.clicked.connect(reroll)
    b2 = QPushButton("Retirer cette vidéo du PMV"); b2.clicked.connect(drop)
    row.addWidget(b1); row.addWidget(b2); row.addStretch(1)
    cancel = QPushButton("Annuler"); ok = QPushButton("Lancer le rendu"); ok.setDefault(True)
    cancel.clicked.connect(d.reject); ok.clicked.connect(d.accept)
    row.addWidget(cancel); row.addWidget(ok)
    lay.addLayout(row)
    fill()
    return d.exec() == QDialog.Accepted


RUNNING = []          # rendus en cours (panneau « traitements en cours »)


class Batch:
    """Plusieurs versions à la suite (même interface que Render pour la fenêtre)."""
    def __init__(self, jobs):
        self.jobs = jobs
        self.cur = 0
        self.finished = False
        self.error = None
        self.result = None
        self._cancel = False

    @property
    def total(self):
        return sum(j.total for j in self.jobs)

    @property
    def done(self):
        return sum(j.done for j in self.jobs)

    @property
    def step(self):
        j = self.jobs[min(self.cur, len(self.jobs) - 1)]
        return j.step + ("  (version %d/%d)" % (self.cur + 1, len(self.jobs)) if len(self.jobs) > 1 else "")

    @property
    def cancel(self):
        return self._cancel

    @cancel.setter
    def cancel(self, v):
        self._cancel = v
        for j in self.jobs:
            j.cancel = v

    def run(self):
        for i, j in enumerate(self.jobs):
            self.cur = i
            if self._cancel:
                break
            j.run()
            if j.error:
                self.error = j.error
                break
            self.result = self.result or j.result
        self.finished = True


# ----------------------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------------------
def starts_for(conn, vid):
    out = []
    for q in ("SELECT t FROM scenes WHERE video_id=?", "SELECT t FROM moments WHERE video_id=?",
              "SELECT t0 FROM hot WHERE video_id=?", "SELECT t0 FROM hot WHERE video_id=?"):
        try:
            out += [r[0] for r in conn.execute(q, (vid,))]
        except Exception:
            pass
    return out


def build_pool(main, vids, limit=400):
    conn = main.conn
    vids = list(dict.fromkeys(vids))
    random.shuffle(vids)
    pool = []
    for vid in vids:
        if len(pool) >= limit:
            break
        try:
            row, path = main.ctrl.resolve(vid)
        except Exception:
            continue
        if not path or not os.path.isfile(path):
            continue
        d = (row["duration"] if row is not None and "duration" in row.keys() else 0) or 0
        if d < 6:
            continue
        try:
            hot = [r[0] for r in conn.execute("SELECT t0 FROM hot WHERE video_id=? ORDER BY score DESC LIMIT 8", (vid,))]
        except Exception:
            hot = []
        pool.append({"vid": vid, "path": path, "dur": float(d), "starts": starts_for(conn, vid), "hot": hot})
    return pool


def fixed_pool(main, kind, limit=600):
    """Une entrée par moment favori (ou par passage fort) : chaque extrait part de cet instant."""
    conn = main.conn
    if kind == "moments":
        q = "SELECT video_id, t FROM moments ORDER BY created_at DESC LIMIT ?"
    else:
        q = ("SELECT h.video_id, h.t0 FROM hot h JOIN videos v ON v.id=h.video_id "
             "WHERE v.liked=1 OR v.rating>=4 OR h.score>=1.2 ORDER BY h.score DESC LIMIT ?")
    pool = []
    cache = {}
    try:
        rows = conn.execute(q, (limit,)).fetchall()
    except Exception as e:
        log("pmv %s : %r" % (kind, e))
        rows = []
    for vid, t in rows:
        if vid not in cache:
            try:
                row, path = main.ctrl.resolve(vid)
            except Exception:
                row, path = None, None
            d = (row["duration"] if row is not None and "duration" in row.keys() else 0) or 0
            cache[vid] = (path, float(d)) if path and os.path.isfile(path) and d > 6 else None
        if cache[vid]:
            path, d = cache[vid]
            start = max(0.0, float(t) - (1.0 if kind == "moments" else 0.0))
            pool.append({"vid": vid, "path": path, "dur": d, "starts": [start], "fixed": True})
    return pool


def source_ids(main, kind, ident=None):
    conn = main.conn
    try:
        if kind == "playlist":
            return [r["id"] for r in db.playlist_videos(conn, int(ident))]
        if kind == "theme":
            return list(db.category_video_ids(conn, str(ident)))
        if kind == "likes":
            return [r[0] for r in conn.execute("SELECT id FROM videos WHERE liked=1 AND missing=0 AND kind='video'")]
        if kind == "stars":
            return [r[0] for r in conn.execute("SELECT id FROM videos WHERE rating>=4 AND missing=0 AND kind='video'")]
        if kind == "all":
            return [r[0] for r in conn.execute("SELECT id FROM videos WHERE missing=0 AND kind='video' ORDER BY RANDOM() LIMIT 600")]
    except Exception as e:
        log("pmv source : %r" % e)
    return []


def music_files(main):
    try:
        files = list(main.music.files or [])
    except Exception:
        files = []
    return [f for f in files if isinstance(f, str) and f.lower().endswith(AUDIO_EXT)]


# ----------------------------------------------------------------------------------------
# Fenêtre « Créer un PMV »
# ----------------------------------------------------------------------------------------
def pick_videos(main, parent, preselected=None):
    """Fenêtre de choix manuel : recherche, filtre, vignettes à cocher. -> liste d'ids (ordre des clics) ou None."""
    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                   QListWidgetItem, QPushButton, QVBoxLayout)
    conn = main.conn
    discreet = bool(main.discreet)
    chosen = list(dict.fromkeys(preselected or []))
    d = QDialog(parent)
    d.setWindowTitle("Choisir les vidéos du PMV")
    d.resize(980, 680)
    lay = QVBoxLayout(d)
    top = QHBoxLayout()
    q = QLineEdit()
    q.setPlaceholderText("Rechercher : titre, thème, personne…")
    q.setClearButtonEnabled(True)
    flt = QComboBox()
    flt.addItem("Toute la bibliothèque", ("all", None))
    flt.addItem("Coups de cœur", ("likes", None))
    flt.addItem("Les mieux notées (4-5 étoiles)", ("stars", None))
    try:
        for p in db.list_playlists(conn):
            flt.addItem("Playlist : %s" % ("Playlist %d" % p["id"] if discreet else p["name"]), ("playlist", p["id"]))
    except Exception:
        pass
    try:
        for i, t in enumerate(db.categories(conn), 1):
            flt.addItem("Thème : %s" % ("Collection %d" % i if discreet else t["category"]), ("theme", t["category"]))
    except Exception:
        pass
    only = QCheckBox("Seulement la sélection")
    top.addWidget(q, 2); top.addWidget(flt, 1); top.addWidget(only)
    lay.addLayout(top)
    lst = QListWidget()
    lst.setViewMode(QListWidget.IconMode)
    lst.setIconSize(QSize(176, 99))
    lst.setGridSize(QSize(196, 150))
    lst.setResizeMode(QListWidget.Adjust)
    lst.setMovement(QListWidget.Static)
    lst.setWordWrap(True)
    lst.setUniformItemSizes(True)
    lay.addWidget(lst, 1)
    bottom = QHBoxLayout()
    count = QLabel()
    clear = QPushButton("Tout décocher")
    allb = QPushButton("Tout cocher (liste affichée)")
    cancel = QPushButton("Annuler")
    ok = QPushButton("Valider")
    ok.setDefault(True)
    bottom.addWidget(count, 1); bottom.addWidget(allb); bottom.addWidget(clear); bottom.addWidget(cancel); bottom.addWidget(ok)
    lay.addLayout(bottom)
    blank = QPixmap(176, 99)
    blank.fill(Qt.black)
    state = {"rows": [], "next": 0, "filling": False}
    loader = QTimer(d)

    def upd_count():
        count.setText("%d vidéo(s) choisie(s)" % len(chosen))
        ok.setEnabled(len(chosen) > 0)

    def rows_for():
        text = q.text().strip()
        kind, ident = flt.currentData()
        if only.isChecked():
            ids = list(chosen)
        elif kind == "all" and not text:
            ids = None
        else:
            ids = source_ids(main, kind, ident) if kind != "all" else None
        if text:
            found = db.search(conn, text, 2000)
            if ids is not None:
                keep = set(ids)
                found = [r for r in found if r["id"] in keep]
            return [r for r in found if r["kind"] == "video"][:1500]
        if ids is None:
            return conn.execute("SELECT * FROM videos WHERE missing=0 AND kind='video' "
                                "ORDER BY added_at DESC LIMIT 1500").fetchall()
        out = []
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            out += conn.execute("SELECT * FROM videos WHERE missing=0 AND kind='video' AND id IN (%s)"
                                % ",".join("?" * len(part)), part).fetchall()
        order = {v: k for k, v in enumerate(ids)}
        out.sort(key=lambda r: order.get(r["id"], 0))
        return out[:1500]

    def fill():
        loader.stop()
        state["filling"] = True
        lst.clear()
        try:
            state["rows"] = rows_for()
        except Exception as e:
            log("pmv choix : %r" % e)
            state["rows"] = []
        state["next"] = 0
        sel = set(chosen)
        for n, r in enumerate(state["rows"], 1):
            it = QListWidgetItem(QIcon(blank), ("Vidéo %d" % n) if discreet else (r["title"] or ""))
            it.setData(Qt.UserRole, r["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if r["id"] in sel else Qt.Unchecked)
            if not discreet:
                it.setToolTip(r["title"] or "")
            lst.addItem(it)
        state["filling"] = False
        if not discreet:
            loader.start(0)
        upd_count()

    def load_some():
        rows = state["rows"]
        k = state["next"]
        for i in range(k, min(k + 24, len(rows))):
            t = rows[i]["thumb"]
            if t and os.path.isfile(t):
                pm = QPixmap(t)
                if not pm.isNull():
                    lst.item(i).setIcon(QIcon(pm.scaled(176, 99, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)))
        state["next"] = k + 24
        if state["next"] >= len(rows):
            loader.stop()
    loader.timeout.connect(load_some)

    def changed(it):
        if state["filling"]:
            return
        vid = it.data(Qt.UserRole)
        if it.checkState() == Qt.Checked:
            if vid not in chosen:
                chosen.append(vid)
        elif vid in chosen:
            chosen.remove(vid)
        upd_count()
    lst.itemChanged.connect(changed)
    lst.itemDoubleClicked.connect(lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))

    def set_all(on):
        for i in range(lst.count()):
            lst.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        if not on:
            chosen.clear()
            upd_count()
    clear.clicked.connect(lambda: set_all(False))
    allb.clicked.connect(lambda: set_all(True))
    deb = QTimer(d)
    deb.setSingleShot(True)
    deb.setInterval(300)
    deb.timeout.connect(fill)
    q.textChanged.connect(lambda _t: deb.start())
    flt.currentIndexChanged.connect(lambda _i: fill())
    only.toggled.connect(lambda _b: fill())
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    if chosen:
        only.setChecked(True)
    else:
        fill()
    if d.exec() != QDialog.Accepted:
        return None
    return list(chosen)


def open_dialog(main, vids=None, source=None):
    safe(lambda: PmvDialog(main, vids, source).exec())


def PmvDialog(main, vids=None, source=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                                   QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

    d = QDialog(main)
    d.setWindowTitle("Créer un PMV")
    d.setMinimumWidth(620)
    lay = QVBoxLayout(d)
    info = QLabel("Phoque Al monte tes vidéos sur une musique : les coupes et les effets tombent sur les temps, "
                  "plus serrés quand la musique monte. Le fichier créé lance la frise néon tout seul.")
    info.setWordWrap(True)
    lay.addWidget(info)
    f = QFormLayout()
    lay.addLayout(f)

    src = QComboBox()
    if vids:
        src.addItem("Vidéos sélectionnées (%d)" % len(vids), ("ids", vids))
    src.addItem("Coups de cœur", ("likes", None))
    src.addItem("Les mieux notées (4-5 étoiles)", ("stars", None))
    try:
        for p in db.list_playlists(main.conn):
            src.addItem("Playlist : %s" % ("Playlist %d" % p["id"] if main.discreet else p["name"]), ("playlist", p["id"]))
    except Exception:
        pass
    try:
        for i, t in enumerate(db.categories(main.conn), 1):
            src.addItem("Thème : %s" % ("Collection %d" % i if main.discreet else t["category"]), ("theme", t["category"]))
    except Exception:
        pass
    src.addItem("Mes moments favoris (compilation)", ("moments", None))
    src.addItem("Passages forts de mes coups de cœur et mieux notées", ("hot", None))
    src.addItem("Toute la bibliothèque (au hasard)", ("all", None))
    if source:
        for i in range(src.count()):
            if src.itemData(i)[0] == source:
                src.setCurrentIndex(i)
                break
    choose = QPushButton("Choisir les vidéos…")
    choose.setToolTip("Coche toi-même les vidéos du PMV (recherche, thème, coups de cœur…)")

    def choose_videos():
        cur = src.currentData()
        pre = list(cur[1]) if cur and cur[0] == "ids" else []
        picked = pick_videos(main, d, pre)
        if picked is None:
            return
        for i in range(src.count()):
            if src.itemData(i)[0] == "ids":
                src.removeItem(i)
                break
        if picked:
            src.insertItem(0, "Vidéos choisies (%d)" % len(picked), ("ids", picked))
            src.setCurrentIndex(0)
    choose.clicked.connect(choose_videos)
    srow = QHBoxLayout()
    srow.addWidget(src, 1)
    srow.addWidget(choose)
    f.addRow("Vidéos", srow)
    from . import definition
    defin = QComboBox()
    for k, lab, _ok in definition.FILTERS:
        defin.addItem(lab, k)
    defin.setCurrentIndex(max(0, defin.findData(db.get_setting(main.conn, "pmv_def", "all"))))
    defin.setToolTip("Mélanger HD et basse définition donne des coupes inégales : garde une seule qualité")
    f.addRow("Définition", defin)

    mrow = QHBoxLayout()
    music = QComboBox()
    music.setEditable(False)
    files = music_files(main)
    last = db.get_setting(main.conn, "pmv_music", "")
    if last and last not in files and os.path.isfile(last):
        files.insert(0, last)
    for p in files:
        music.addItem(os.path.basename(p), p)
    if last in files:
        music.setCurrentIndex(files.index(last))
    browse = QPushButton("Choisir…")

    def pick_music():
        p, _ = QFileDialog.getOpenFileName(d, "Musique", os.path.dirname(music.currentData() or "") or "",
                                           "Audio (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma)")
        if p:
            music.insertItem(0, os.path.basename(p), p)
            music.setCurrentIndex(0)
    browse.clicked.connect(pick_music)
    rnd = QPushButton("Au hasard")
    rnd.clicked.connect(lambda: music.setCurrentIndex(random.randrange(music.count())) if music.count() else None)
    mrow.addWidget(music, 1); mrow.addWidget(browse); mrow.addWidget(rnd)
    f.addRow("Musique", mrow)

    length = QComboBox()
    for lab, v in (("Morceau entier", 0), ("30 s (boucle courte)", 0.5), ("1 min", 1), ("2 min", 2), ("3 min", 3), ("5 min", 5)):
        length.addItem(lab, v)
    f.addRow("Durée", length)
    mode = QComboBox()
    for lab, v in (("Automatique (suit l'énergie de la musique)", "auto"), ("À chaque temps", "1"),
                   ("Tous les 2 temps", "2"), ("Toutes les mesures (4 temps)", "4"), ("Toutes les 2 mesures", "8")):
        mode.addItem(lab, v)
    f.addRow("Coupes", mode)
    style = QComboBox()
    for k, lab in STYLES:
        style.addItem(lab, k)
    last_style = db.get_setting(main.conn, "pmv_style", "mix")
    style.setCurrentIndex(max(0, [k for k, _l in STYLES].index(last_style) if last_style in [k for k, _l in STYLES] else 0))
    f.addRow("Style", style)

    fxrow = QHBoxLayout()
    boxes = {}
    for k, lab, on in (("zoom", "Zoom punch", True), ("glitch", "Glitch RGB", True), ("shake", "Secousse", True),
                       ("flash", "Flash néon", False), ("fade", "Fondus (calme)", True)):
        c = QCheckBox(lab); c.setChecked(db.get_setting(main.conn, "pmv_fx_" + k, "1" if on else "0") == "1")
        boxes[k] = c; fxrow.addWidget(c)
    f.addRow("Effets", fxrow)
    fxrow2 = QHBoxLayout()
    for k, lab, on in (("vhs", "VHS", True), ("neon", "Contours néon", True), ("ghost", "Traînées", True),
                       ("boom", "Boomerang", True), ("freeze", "Arrêt sur image", True), ("pixel", "Pixels", True),
                       ("bars", "Bandes cinéma", True), ("invert", "Négatif éclair", False)):
        c = QCheckBox(lab); c.setChecked(db.get_setting(main.conn, "pmv_fx_" + k, "1" if on else "0") == "1")
        boxes[k] = c; fxrow2.addWidget(c)
    f.addRow("", fxrow2)
    trrow = QHBoxLayout()
    for k, lab, on in (("whip", "Balayage", True), ("spin", "Tourbillon", True), ("burn", "Brûlure de pellicule", True)):
        c = QCheckBox(lab); c.setChecked(db.get_setting(main.conn, "pmv_fx_" + k, "1" if on else "0") == "1")
        boxes[k] = c; trrow.addWidget(c)
    trrow.addStretch(1)
    f.addRow("Transitions", trrow)
    preview = QCheckBox("Voir et retoucher la liste des extraits avant le rendu")
    preview.setChecked(db.get_setting(main.conn, "pmv_preview", "0") == "1")
    sfx = QCheckBox("Bruitages synthétisés (whoosh, impacts, montées) dans la piste « musique et sons »")
    sfx.setChecked(db.get_setting(main.conn, "pmv_sfx", "0") == "1")
    f.addRow("", sfx)
    f.addRow("", preview)
    words = QLineEdit(db.get_setting(main.conn, "pmv_words", "") or "")
    words.setPlaceholderText("Mots qui claquent sur les temps forts, séparés par des virgules (vide = aucun)")
    f.addRow("Mots", words)
    title = QLineEdit(db.get_setting(main.conn, "pmv_title", "") or "")
    title.setPlaceholderText("Titre affiché au début (vide = aucun)")
    f.addRow("Titre", title)
    look = QCheckBox("Couleurs néon (contraste, saturation, teinte magenta / cyan)")
    look.setChecked(db.get_setting(main.conn, "pmv_look", "1") == "1")
    f.addRow("", look)
    inten = QComboBox()
    for lab, v in (("Douce", 0.6), ("Normale", 1.0), ("Forte", 1.4)):
        inten.addItem(lab, v)
    inten.setCurrentIndex(1)
    f.addRow("Intensité", inten)
    qual = QComboBox()
    for lab, v in (("1080p (conseillé)", (1920, 1080)), ("720p (plus rapide)", (1280, 720)), ("4K (lent)", (3840, 2160)),
                   ("Vertical téléphone 1080×1920", (1080, 1920)), ("Vertical 720×1280 (rapide)", (720, 1280))):
        qual.addItem(lab, v)
    f.addRow("Qualité", qual)
    versions = QComboBox()
    for lab, v in (("1 version", 1), ("2 versions différentes", 2), ("3 versions différentes", 3)):
        versions.addItem(lab, v)
    f.addRow("Versions", versions)

    orow = QHBoxLayout()
    outdir = QLineEdit(db.get_setting(main.conn, "pmv_dir", "") or os.path.expanduser("~/Videos"))
    ob = QPushButton("Dossier…")
    ob.clicked.connect(lambda: (lambda p: outdir.setText(p) if p else None)(
        QFileDialog.getExistingDirectory(d, "Dossier des PMV", outdir.text())))
    orow.addWidget(outdir, 1); orow.addWidget(ob)
    f.addRow("Enregistrer dans", orow)
    hint = QLabel("Conseil : choisis un dossier de ta bibliothèque, le PMV y apparaîtra après « Actualiser ».")
    hint.setStyleSheet("color:#8b8c99")
    f.addRow("", hint)
    rescan = QCheckBox("Actualiser la bibliothèque à la fin")
    rescan.setChecked(True)
    f.addRow("", rescan)

    # préréglages : style, coupes, effets, couleurs, intensité, qualité, mots
    prow = QHBoxLayout()
    preset = QComboBox()
    psave = QPushButton("Enregistrer…")
    pdel = QPushButton("Supprimer")
    prow.addWidget(preset, 1); prow.addWidget(psave); prow.addWidget(pdel)
    f.insertRow(0, "Préréglage", prow)

    def presets():
        try:
            v = json.loads(db.get_setting(main.conn, "pmv_presets", "") or "{}")
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def fill_presets(sel=None):
        preset.blockSignals(True)
        preset.clear()
        preset.addItem("(réglages actuels)", None)
        for k in sorted(presets()):
            preset.addItem(k, k)
        if sel:
            i = preset.findData(sel)
            preset.setCurrentIndex(max(0, i))
        preset.blockSignals(False)

    def snapshot():
        return {"style": style.currentData(), "mode": mode.currentData(), "look": look.isChecked(),
                "intensity": inten.currentData(), "size": list(qual.currentData()), "sfx": sfx.isChecked(),
                "words": words.text(), "fx": {k: c.isChecked() for k, c in boxes.items()}}

    def apply_preset(_=None):
        k = preset.currentData()
        p = presets().get(k) if k else None
        if not p:
            return
        for combo, key in ((style, "style"), (mode, "mode"), (inten, "intensity")):
            i = combo.findData(p.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        if p.get("size"):
            i = qual.findData(tuple(p["size"]))
            if i >= 0:
                qual.setCurrentIndex(i)
        look.setChecked(bool(p.get("look", True)))
        sfx.setChecked(bool(p.get("sfx", False)))
        words.setText(p.get("words", ""))
        for kk, c in boxes.items():
            if kk in p.get("fx", {}):
                c.setChecked(bool(p["fx"][kk]))

    def save_preset():
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(d, "Préréglage", "Nom du préréglage :", text=preset.currentData() or "")
        name = (name or "").strip()[:40]
        if not ok or not name:
            return
        allp = presets()
        allp[name] = snapshot()
        db.set_setting(main.conn, "pmv_presets", json.dumps(allp, ensure_ascii=False))
        fill_presets(name)

    def del_preset():
        k = preset.currentData()
        if not k:
            return
        allp = presets()
        allp.pop(k, None)
        db.set_setting(main.conn, "pmv_presets", json.dumps(allp, ensure_ascii=False))
        fill_presets()
    fill_presets()
    preset.currentIndexChanged.connect(apply_preset)
    psave.clicked.connect(save_preset)
    pdel.clicked.connect(del_preset)

    bar = QProgressBar(); bar.setVisible(False)
    status = QLabel(""); status.setStyleSheet("color:#8b8c99")
    lay.addWidget(bar); lay.addWidget(status)
    btns = QHBoxLayout(); btns.addStretch(1)
    close = QPushButton("Fermer"); go = QPushButton("Créer le PMV"); go.setDefault(True)
    bg = QPushButton("Continuer en arrière-plan"); bg.setVisible(False)
    bg.setToolTip("Ferme la fenêtre : le rendu continue, son avancement s'affiche en bas à gauche de l'accueil")
    bg.clicked.connect(d.hide)
    btns.addWidget(bg); btns.addWidget(close); btns.addWidget(go)
    lay.addLayout(btns)
    state = {"job": None}

    def start():
        from ..config import find_tool
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            QMessageBox.warning(d, "PMV", "FFmpeg introuvable.")
            return
        mpath = music.currentData()
        if not mpath or not os.path.isfile(mpath):
            QMessageBox.information(d, "PMV", "Choisis une musique.")
            return
        kind, ident = src.currentData()
        status.setText("Préparation des vidéos…")
        flt = defin.currentData() or "all"
        db.set_setting(main.conn, "pmv_def", flt)

        def def_progress(i, n):
            from PySide6.QtWidgets import QApplication
            status.setText("Lecture de la définition des vidéos… %d / %d" % (i, n))
            QApplication.processEvents()
        if kind in ("moments", "hot"):
            pool = fixed_pool(main, kind)
            if flt != "all":
                good = set(definition.keep(main, list(dict.fromkeys(p["vid"] for p in pool)), flt, def_progress))
                pool = [p for p in pool if p["vid"] in good]
        else:
            ids = list(ident) if kind == "ids" else source_ids(main, kind, ident)
            if flt != "all":
                ids = definition.keep(main, ids, flt, def_progress)
            pool = build_pool(main, ids)
        status.setText("")
        if not pool:
            QMessageBox.information(d, "PMV", "Aucune vidéo utilisable (fichiers introuvables ou trop courts)."
                                    + ("\nAucune vidéo de cette définition dans ce choix : essaie « Toutes »."
                                       if flt != "all" else ""))
            return
        folder = outdir.text().strip()
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(d, "PMV", "Dossier impossible : %s" % e)
            return
        for k, c in boxes.items():
            db.set_setting(main.conn, "pmv_fx_" + k, "1" if c.isChecked() else "0")
        db.set_setting(main.conn, "pmv_look", "1" if look.isChecked() else "0")
        db.set_setting(main.conn, "pmv_dir", folder)
        db.set_setting(main.conn, "pmv_music", mpath)
        base = os.path.splitext(os.path.basename(mpath))[0][:60]
        name = "PMV Phoque Al - %s - %s.mp4" % (base, time.strftime("%Y-%m-%d %Hh%M"))
        name = "".join(ch for ch in name if ch not in '<>:"/\\|?*')
        db.set_setting(main.conn, "pmv_style", style.currentData())
        db.set_setting(main.conn, "pmv_words", words.text().strip())
        db.set_setting(main.conn, "pmv_sfx", "1" if sfx.isChecked() else "0")
        db.set_setting(main.conn, "pmv_title", title.text().strip())
        opts = {"minutes": length.currentData(), "mode": mode.currentData(), "style": style.currentData(),
                "fx": {k: c.isChecked() for k, c in boxes.items()}, "look": look.isChecked(),
                "intensity": inten.currentData(), "size": qual.currentData(), "fps": 30,
                "workers": max(2, min(4, (os.cpu_count() or 4) // 2)), "seed": random.randrange(10 ** 9),
                "words": [w for w in words.text().split(",") if w.strip()], "title": title.text().strip(),
                "sfx": sfx.isChecked()}
        n = versions.currentData() or 1
        jobs = []
        for v in range(n):
            o2 = dict(opts, seed=opts["seed"] + v * 7919)
            nm = name if n == 1 else name.replace(".mp4", " - v%d.mp4" % (v + 1))
            jobs.append(Render(ffmpeg, mpath, pool, os.path.join(folder, nm), o2, conn_factory=db.connect))
        db.set_setting(main.conn, "pmv_preview", "1" if preview.isChecked() else "0")
        if preview.isChecked():
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt as _Qt
            QApplication.setOverrideCursor(_Qt.WaitCursor)
            try:
                for j in jobs:
                    j.plan()
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(d, "PMV", "Échec : %s" % e)
                return
            QApplication.restoreOverrideCursor()
            if not review_dialog(d, main, jobs[0]):
                status.setText("")
                return
        job = Batch(jobs)
        state["job"] = job
        RUNNING.append(job)
        threading.Thread(target=job.run, daemon=True).start()
        go.setEnabled(False); close.setText("Annuler"); bar.setVisible(True); bg.setVisible(True)
        timer.start(300)

    def poll():
        job = state["job"]
        if job is None:
            return
        bar.setMaximum(max(1, job.total)); bar.setValue(job.done)
        status.setText("%s  %d / %d" % (job.step, job.done, job.total))
        if not job.finished:
            return
        timer.stop()
        state["job"] = None
        go.setEnabled(True); close.setText("Fermer"); bg.setVisible(False)
        if job.error:
            status.setText("Échec : %s" % job.error)
            return
        r = job.result
        status.setText("Terminé : %d coupes, %.0f s, %.0f BPM — %s" % (r["segments"], r["dur"], r["bpm"], os.path.basename(r["path"]))
                       + ("  (+%d autre(s) version(s))" % (len(job.jobs) - 1) if len(job.jobs) > 1 else ""))
        main.status.setText("PMV créé : %s" % r["path"])
        if rescan.isChecked():
            try:
                main.start_scan()
            except Exception:
                pass

    def on_close():
        job = state["job"]
        if job is not None and not job.finished:
            job.cancel = True
            status.setText("Annulation…")
            return
        d.accept()

    timer = QTimer(d)
    timer.timeout.connect(poll)
    go.clicked.connect(start)
    close.clicked.connect(on_close)
    return d
