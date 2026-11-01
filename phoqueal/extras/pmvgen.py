"""Générateur de PMV : monte des vidéos (sans musique) sur une musique, coupe sur les temps.

- Densité des coupes selon l'énergie de la musique : calme = toutes les 4 à 8 mesures de temps,
  montée = tous les 2 temps, refrain = à chaque temps (ou rythme fixe au choix).
- Extraits pris de préférence sur les débuts de scène et les moments favoris, sinon au hasard
  dans les 80 % de fin de la vidéo.
- Effets calés sur les temps : flash néon, zoom « punch », glitch RGB, secousse, fondu au noir
  sur les passages calmes, look néon (couleurs). Intensité réglable.
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
        segs.append({"t0": round(t0, 3), "t1": round(t1, 3), "fx": f, "calm": calm, "acc": a, "e": e})
    return segs


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

    def one(need):
        cands = [c for c in pool if c["dur"] > need + 2 and c["vid"] not in recent] or \
                [c for c in pool if c["dur"] > need + 1] or pool
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
        good = [x for x in c.get("starts", []) if lo <= x <= hi and (c["vid"], round(x, 1)) not in used]
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
        c, ss = one(need)
        s["src"], s["ss"], s["vid"] = c["path"], ss, c["vid"]
        n = NEED.get(s.get("style"), 1)
        if n > 1:
            s["srcs"] = [(c["path"], ss)] + [(lambda r: (r[0]["path"], r[1]))(one(need)) for _ in range(n - 1)]
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
    out.append("tpad=stop_mode=clone:stop_duration=3")
    return out


def vf_chain(seg, W, H, fps, look, intensity, effects=True):
    """Chaîne de filtres d'un segment à une seule vidéo."""
    st = seg.get("style") if effects else None
    s2 = dict(seg, style=st) if effects else dict(seg, style=None)
    parts = base_chain(W, H, fps, seg.get("speed", 1.0)) + (style_chain(s2, W, H, fps, intensity) if effects else []) \
        + post_chain(s2, W, H, fps, look, intensity, effects)
    return ",".join(parts)


def seg_args(seg, W, H, fps, look, intensity, effects=True):
    """Arguments FFmpeg (entrées + filtres) d'un segment, selon son style."""
    n = NEED.get(seg.get("style"), 1)
    srcs = seg.get("srcs") or []
    if effects and n > 1 and len(srcs) >= n:
        tw, th = (W // 2, H) if n == 2 else (W // 2, H // 2)
        args, g = [], []
        for i, (path, ss) in enumerate(srcs[:n]):
            args += ["-ss", "%.2f" % ss, "-i", path]
            g.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,fps=%d[v%d]"
                     % (i, tw, th, tw, th, fps, i))
        if n == 2:
            g.append("[v0][v1]hstack=inputs=2[s]")
        else:
            g.append("[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[s]")
        g.append("[s]" + ",".join(post_chain(seg, W, H, fps, look, intensity, True)) + "[out]")
        return args + ["-filter_complex", ";".join(g), "-map", "[out]"]
    return ["-ss", "%.2f" % seg["ss"], "-i", seg["src"], "-vf", vf_chain(seg, W, H, fps, look, intensity, effects)]


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
                           capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=_FLAGS)
        return r.returncode == 0, r.stderr[-400:]

    def _run(self):
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
        dur = segs[-1]["t1"]
        W, H = o.get("size", (1920, 1080))
        fps = o.get("fps", 30)
        tmp = tempfile.mkdtemp(prefix=".focal_pmv_", dir=os.path.dirname(self.out) or None)
        self.total = len(segs) + 1
        self.step = "Montage des extraits…"
        try:
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
                ok, err = self._ff(["-f", "concat", "-safe", "0", "-i", lst, "-i", self.music,
                                    "-f", "concat", "-safe", "0", "-i", alst, "-filter_complex", g,
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
        pool.append({"vid": vid, "path": path, "dur": float(d), "starts": starts_for(conn, vid)})
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
    f.addRow("Vidéos", src)

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
    for lab, v in (("Morceau entier", 0), ("1 min", 1), ("2 min", 2), ("3 min", 3), ("5 min", 5)):
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
    look = QCheckBox("Couleurs néon (contraste, saturation, teinte magenta / cyan)")
    look.setChecked(db.get_setting(main.conn, "pmv_look", "1") == "1")
    f.addRow("", look)
    inten = QComboBox()
    for lab, v in (("Douce", 0.6), ("Normale", 1.0), ("Forte", 1.4)):
        inten.addItem(lab, v)
    inten.setCurrentIndex(1)
    f.addRow("Intensité", inten)
    qual = QComboBox()
    for lab, v in (("1080p (conseillé)", (1920, 1080)), ("720p (plus rapide)", (1280, 720)), ("4K (lent)", (3840, 2160))):
        qual.addItem(lab, v)
    f.addRow("Qualité", qual)

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

    bar = QProgressBar(); bar.setVisible(False)
    status = QLabel(""); status.setStyleSheet("color:#8b8c99")
    lay.addWidget(bar); lay.addWidget(status)
    btns = QHBoxLayout(); btns.addStretch(1)
    close = QPushButton("Fermer"); go = QPushButton("Créer le PMV"); go.setDefault(True)
    btns.addWidget(close); btns.addWidget(go)
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
        if kind in ("moments", "hot"):
            pool = fixed_pool(main, kind)
        else:
            ids = list(ident) if kind == "ids" else source_ids(main, kind, ident)
            pool = build_pool(main, ids)
        if not pool:
            QMessageBox.information(d, "PMV", "Aucune vidéo utilisable (fichiers introuvables ou trop courts).")
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
        opts = {"minutes": length.currentData(), "mode": mode.currentData(), "style": style.currentData(),
                "fx": {k: c.isChecked() for k, c in boxes.items()}, "look": look.isChecked(),
                "intensity": inten.currentData(), "size": qual.currentData(), "fps": 30,
                "workers": max(2, min(4, (os.cpu_count() or 4) // 2)), "seed": random.randrange(10 ** 9)}
        job = Render(ffmpeg, mpath, pool, os.path.join(folder, name), opts, conn_factory=db.connect)
        state["job"] = job
        threading.Thread(target=job.run, daemon=True).start()
        go.setEnabled(False); close.setText("Annuler"); bar.setVisible(True)
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
        go.setEnabled(True); close.setText("Fermer")
        if job.error:
            status.setText("Échec : %s" % job.error)
            return
        r = job.result
        status.setText("Terminé : %d coupes, %.0f s, %.0f BPM — %s" % (r["segments"], r["dur"], r["bpm"], os.path.basename(r["path"])))
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
