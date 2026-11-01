"""Frise de rythme néon + effets sur les temps, en direct.

- Écran principal : le plus grand (taille physique), puis le plus central ; réglable (⋯ → Rythme).
- S'affiche pour les vidéos « pmv » (tag pmv, ou « PMV » dans le titre / nom de fichier) jouées sur
  l'écran principal ; Y force / coupe la frise pour la vidéo en cours.
- Pendant le montage au rythme de la musique, la frise suit la musique (option).
- Les temps sont détectés une fois (FFmpeg) puis gardés en mémoire (table beats).
- Styles : Convergence, Défilement, Autoroute synthwave, Compteur. Effets : bordure qui pulse
  (écran principal ou tous), vignette, flash (désactivé par défaut), glitch, lignes de balayage.
"""
import math
import random
import re
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .. import db
from . import beats as B
from .common import log, owner, safe

CYAN, MAG, VIO, JAU = QColor("#22f3ff"), QColor("#ff2bd6"), QColor("#8a5cff"), QColor("#ffe94d")
STYLES = [("conv", "Convergence"), ("defil", "Défilement"), ("synth", "Autoroute synthwave"), ("compteur", "Compteur")]
SIZES = [("s", "Petite", 0.10), ("m", "Moyenne", 0.15), ("l", "Grande", 0.21)]

ACTIONS = [
    ("p_rythme", "player", "Frise de rythme : afficher / masquer", "Y"),
    ("p_rythme_style", "player", "Frise de rythme : style suivant", "Shift+Y"),
    ("p_rythme_moins", "player", "Frise de rythme : décaler plus tôt (−20 ms)", "Alt+Left"),
    ("p_rythme_plus", "player", "Frise de rythme : décaler plus tard (+20 ms)", "Alt+Right"),
]

ENGINE = None


def S(main, k, d):
    return db.get_setting(main.conn, "ry_" + k, d)


def setS(main, k, v):
    db.set_setting(main.conn, "ry_" + k, str(v))
    if ENGINE:
        ENGINE.reload()


# ----------------------------------------------------------------------------------------
# Écran principal
# ----------------------------------------------------------------------------------------
def main_screen_number(main):
    scr = main.ctrl.screens()
    if not scr:
        return 1
    try:
        forced = int(S(main, "screen", "0") or 0)
    except ValueError:
        forced = 0
    if 1 <= forced <= len(scr):
        return forced
    geo = [s.geometry() for s in scr]
    x0 = min(g.x() for g in geo); x1 = max(g.x() + g.width() for g in geo)
    y0 = min(g.y() for g in geo); y1 = max(g.y() + g.height() for g in geo)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    diag = math.hypot(x1 - x0, y1 - y0) or 1

    def score(i):
        s = scr[i]
        ps = s.physicalSize()
        area = ps.width() * ps.height()
        if area < 100:                                  # taille inconnue : pixels
            area = geo[i].width() * geo[i].height() / 1000.0
        c = geo[i].center()
        dist = math.hypot(c.x() - cx, c.y() - cy) / diag
        return area * (1.0 - 0.35 * dist)
    best = max(range(len(scr)), key=score)
    return best + 1


# ----------------------------------------------------------------------------------------
# Vidéos pmv
# ----------------------------------------------------------------------------------------
_PMV = {"t": 0, "ids": set()}
PMV_WORD = re.compile(r"(^|[^a-z])pmvs?(haven)?([^a-z]|$)", re.I)       # pmv, PMVs, PMVHaven


def pmv_ids(conn):
    if time.time() - _PMV["t"] < 60:
        return _PMV["ids"]
    ids = set()
    for q in ("SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE lower(t.name)='pmv' AND vt.manual>=0",
              "SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE lower(t.name)='pmv'",
              "SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE lower(t.label)='pmv'"):
        try:
            ids = {r[0] for r in conn.execute(q)}
            break
        except Exception:
            continue
    _PMV.update(t=time.time(), ids=ids)
    return ids


def is_pmv(main, win):
    o = owner(win)
    vid = getattr(o, "video_id", None)
    if vid and vid in pmv_ids(main.conn):
        return True
    for txt in (getattr(o, "title", "") or "", getattr(o, "path", "") or ""):
        if PMV_WORD.search(str(txt)):
            return True
    return False


def media_path(win):
    o = owner(win)
    for k in ("path", "current_path", "file"):
        v = getattr(o, k, None)
        if isinstance(v, str) and v:
            return v
    try:
        m = o.player.get_media()
        mrl = m.get_mrl() if m else ""
        if mrl.startswith("file:"):
            from urllib.parse import unquote, urlparse
            p = unquote(urlparse(mrl).path)
            return p[1:] if re.match(r"^/[A-Za-z]:", p) else p
    except Exception:
        pass
    vid = getattr(o, "video_id", None)
    if vid and vid > 0:
        try:
            return win.ctrl.resolve(vid)[1]
        except Exception:
            pass
    return None


# ----------------------------------------------------------------------------------------
# Analyse en tâche de fond
# ----------------------------------------------------------------------------------------
class Analyzer:
    def __init__(self):
        self.lock = threading.Lock()
        self.mem = {}                 # chemin -> donnees (ou None si en cours / echec)
        self.queue = []
        self.thread = None

    def get(self, path):
        with self.lock:
            if path in self.mem:
                return self.mem[path]
            self.mem[path] = None
            self.queue.insert(0, path)
        self._start()
        return None

    def prefetch(self, paths):
        with self.lock:
            for p in paths:
                if p not in self.mem:
                    self.mem[p] = None
                    self.queue.append(p)
        self._start()

    def _start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        from ..config import find_tool
        ffmpeg = find_tool("ffmpeg")
        conn = db.connect()
        while True:
            with self.lock:
                if not self.queue:
                    return
                path = self.queue.pop(0)
            try:
                d = B.get(conn, ffmpeg, path) if ffmpeg else None
            except Exception as e:
                log("rythme analyse %s : %r" % (path, e))
                d = None
            with self.lock:
                self.mem[path] = d or {"beats": []}


ANALYZER = Analyzer()


# ----------------------------------------------------------------------------------------
# Fenêtres transparentes
# ----------------------------------------------------------------------------------------
def overlay_flags():
    f = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    if hasattr(Qt, "WindowTransparentForInput"):
        f |= Qt.WindowTransparentForInput
    return f


class Overlay(QWidget):
    def __init__(self, engine, painter):
        super().__init__(None, overlay_flags())
        self.engine, self.painter_fn = engine, painter
        self.broken = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.broken:
            return
        try:
            self.painter_fn(p, self)
        except Exception:
            import traceback
            self.broken = True                     # une seule trace dans le journal, pas 60 par seconde
            log("frise : " + traceback.format_exc())


def neon(p, path_or_fn, color, width, glow=3):
    """Trait lumineux : passes larges et transparentes puis trait net."""
    for k in range(glow, 0, -1):
        c = QColor(color); c.setAlphaF(0.10 + 0.05 * (glow - k))
        p.setPen(QPen(c, width + k * width * 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path_or_fn()
    p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path_or_fn()


def glow_fill(p, shape_fn, color, spread):
    for k in (3, 2, 1):
        c = QColor(color); c.setAlphaF(0.12)
        p.setPen(QPen(c, spread * k, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        shape_fn()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    shape_fn()


# ----------------------------------------------------------------------------------------
# Styles de frise (dessinés dans la bande du bas, coordonnées de la fenêtre)
# ----------------------------------------------------------------------------------------
def band_bg(p, w, h, alpha):
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(7, 6, 13, 0))
    g.setColorAt(0.45, QColor(7, 6, 13, int(140 * alpha)))
    g.setColorAt(1, QColor(7, 6, 13, int(215 * alpha)))
    p.fillRect(QRectF(0, 0, w, h), QBrush(g))


def style_conv(e, p, w, h, b, hit, acc):
    cy, cx, W, half = h * 0.58, w / 2, 2.6, w * 0.44
    neon(p, lambda: p.drawLine(QPointF(w * 0.05, cy), QPointF(w * 0.95, cy)), VIO, 2, 2)
    for k, dt, a, s in e.upcoming(b, W):
        off = dt / W * half
        mh = h * (0.62 if a else 0.38) * (0.8 + 0.2 * s)
        mw = max(5.0, h * (0.07 if a else 0.045))
        col = QColor(MAG if a else CYAN)
        col.setAlphaF(max(0.0, min(1.0, 1.25 - dt / W)))
        for x in (cx - off, cx + off):
            r = QRectF(x - mw / 2, cy - mh / 2, mw, mh)
            glow_fill(p, lambda r=r: p.drawRoundedRect(r, mw / 2, mw / 2), col, mw * 0.6)
    r = h * 0.28 * (1 + 0.45 * hit * (1.4 if acc else 1))
    col = MAG if acc and hit > 0.05 else CYAN
    neon(p, lambda: (p.setBrush(Qt.NoBrush), p.drawEllipse(QPointF(cx, cy), r, r)), col, 3)
    c = QColor(col); c.setAlphaF(0.25 + 0.6 * hit)
    p.setPen(Qt.NoPen); p.setBrush(c); p.drawEllipse(QPointF(cx, cy), r * 0.55, r * 0.55)


def style_defil(e, p, w, h, b, hit, acc):
    cy, gx, W, ln = h * 0.58, w * 0.16, 3.2, w * 0.78
    for yy in (cy - h * 0.25, cy + h * 0.25):
        neon(p, lambda yy=yy: p.drawLine(QPointF(gx, yy), QPointF(w * 0.96, yy)), CYAN, 1.5, 2)
    for k, dt, a, s in e.upcoming(b, W):
        x = gx + dt / W * ln
        sz = (1.0 if a else 0.65) * (0.8 + 0.2 * s) * h * 0.24
        col = MAG if a else CYAN
        tr = QLinearGradient(x, 0, x + h * 1.2, 0)
        c0 = QColor(col); c0.setAlphaF(0.4); tr.setColorAt(0, c0); tr.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(x, cy - sz * 0.35, h * 1.2, sz * 0.7), QBrush(tr))
        path = QPainterPath()
        path.moveTo(x, cy - sz); path.lineTo(x + sz * 0.7, cy); path.lineTo(x, cy + sz); path.lineTo(x - sz * 0.7, cy)
        path.closeSubpath()
        glow_fill(p, lambda path=path: p.drawPath(path), col, sz * 0.25)
    e.sparks_draw(p, gx, cy, w, h, acc)
    col = MAG if acc and hit > 0.05 else JAU
    neon(p, lambda: p.drawLine(QPointF(gx, cy - h * 0.4), QPointF(gx, cy + h * 0.4)), col, 3 + 3 * hit)


def style_synth(e, p, w, h, b, hit, acc):
    vx, vy, W = w / 2, -h * 0.35, 3.0
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(10, 4, 24, 40)); g.setColorAt(1, QColor(10, 4, 24, 215))
    p.fillRect(QRectF(0, 0, w, h), QBrush(g))
    p.save()
    p.setClipRect(QRectF(0, 0, w, h))
    c = QColor(VIO); c.setAlphaF(0.55)
    p.setPen(QPen(c, 1.2))
    for i in range(-10, 11):
        p.drawLine(QPointF(vx, vy), QPointF(vx + i * w * 0.09, h))
    ph = b % 1
    for i in range(8):
        z = (i + ph) / 8.0
        y = h * z * z
        c.setAlphaF(0.2 + 0.55 * z); p.setPen(QPen(c, 1.2))
        p.drawLine(QPointF(0, y), QPointF(w, y))
    for k, dt, a, s in e.upcoming(b, W):
        z = 1 - dt / W
        y = h * z * z
        half = w * 0.05 + w * 0.32 * z * z
        col = QColor(MAG if a else CYAN); col.setAlphaF(min(1.0, z * 1.6))
        neon(p, lambda y=y, half=half: p.drawLine(QPointF(vx - half, y), QPointF(vx + half, y)), col, (5 if a else 3) * (0.4 + z), 2)
    p.restore()
    hy = h * 0.9
    col = MAG if acc and hit > 0.05 else JAU
    neon(p, lambda: p.drawLine(QPointF(w * 0.12, hy), QPointF(w * 0.88, hy)), col, 2 + 4 * hit)


def style_compteur(e, p, w, h, b, hit, acc):
    n, x0 = 40, w * 0.07
    bw, base = w * 0.86 / n, h * 0.9
    for i in range(n):
        f = 0.25 + 0.75 * abs(math.sin(i * 1.3 + b * 0.9) * math.cos(i * 0.37 - b * 0.5))
        hh = f * (0.35 + 0.65 * hit) * h * 0.5
        col = QColor(CYAN if i < n / 2 else MAG); col.setAlphaF(0.85)
        p.fillRect(QRectF(x0 + i * bw + 1, base - hh, bw - 3, hh), col)
    cur = int(math.floor(b - e.phase)) % 4
    fs = max(10, int(h * 0.26))
    f = QFont("Segoe UI", 10); f.setPixelSize(fs); f.setBold(True)
    p.setFont(f)
    for i in range(4):
        x = w / 2 + (i - 1.5) * fs * 1.6
        on = i == cur
        r = QRectF(x - fs, h * 0.08, fs * 2, fs * 1.3)
        if on:
            c = QColor(MAG if i == 0 else CYAN)
            for k in (3, 2):
                g = QColor(c); g.setAlphaF(0.18)
                p.setPen(g)
                p.drawText(r.adjusted(-k, -k, k, k), Qt.AlignCenter, str(i + 1))
            p.setPen(c)
        else:
            p.setPen(QColor(236, 232, 255, 60))
        p.drawText(r, Qt.AlignCenter, str(i + 1))


STYLE_FN = {"conv": style_conv, "defil": style_defil, "synth": style_synth, "compteur": style_compteur}


# ----------------------------------------------------------------------------------------
# Moteur
# ----------------------------------------------------------------------------------------
class Engine:
    def __init__(self, main):
        self.main = main
        self.bar = None
        self.fx = None
        self.edges = {}                      # numero d'ecran -> [4 fenetres]
        self.data = None
        self.src_key = None
        self.forced = {}                     # video_id -> True/False (Ctrl+B)
        self.t_ref = (0.0, 0.0, 1.0)         # (temps media, horloge, vitesse)
        self.last_media = None
        self.sparks = []
        self.last_beat = None
        self.phase = 0
        self.fx_until = 0.0
        self.glitch = []
        self.timer = QTimer()
        self.timer.timeout.connect(lambda: safe(self.tick))
        self.slow = QTimer()
        self.slow.timeout.connect(lambda: safe(self.pick_source))
        self.reload()
        self.timer.start(16)
        self.slow.start(300)

    # --- reglages
    def reload(self):
        m = self.main
        self.on = S(m, "on", "1") == "1"
        self.style = S(m, "style", "conv")
        self.size = {k: f for k, _, f in SIZES}.get(S(m, "size", "m"), 0.15)
        self.opacity = float(S(m, "opacity", "0.9") or 0.9)
        self.offset = float(S(m, "offset", "0") or 0) / 1000.0
        self.e_border = S(m, "fx_border", "1") == "1"
        self.e_border_all = S(m, "fx_border_all", "0") == "1"
        self.e_vig = S(m, "fx_vig", "0") == "1"
        self.e_flash = S(m, "fx_flash", "0") == "1"
        self.e_glitch = S(m, "fx_glitch", "1") == "1"
        self.e_scan = S(m, "fx_scan", "0") == "1"
        self.with_montage = S(m, "montage", "1") == "1"
        self.place()

    def place(self):
        self.screen_n = main_screen_number(self.main)
        scr = self.main.ctrl.screens()
        self.qscreen = scr[self.screen_n - 1] if 0 < self.screen_n <= len(scr) else None
        if self.bar is not None and self.qscreen is not None:
            self.geom()

    def geom(self):
        g = self.qscreen.geometry()
        bh = int(g.height() * self.size)
        self.bar.setGeometry(g.x(), g.y() + g.height() - bh, g.width(), bh)
        if self.fx is not None:
            self.fx.setGeometry(g)

    # --- source : video pmv de l'ecran principal, ou musique du montage
    def pick_source(self):
        m = self.main
        if not self.on or self.qscreen is None:
            return self.set_source(None)
        try:
            if self.with_montage and m.montage_btn.isChecked() and m.music.is_playing():
                path = m.music.current_path()
                mp = music_player(m.music)
                if path and mp is not None:
                    return self.set_source(("music", path), mp)
        except Exception:
            pass
        win = m.ctrl.players.get(self.screen_n)
        if win is None or getattr(win, "is_remote", False):
            return self.set_source(None)
        o = owner(win)
        vid = getattr(o, "video_id", None)
        want = self.forced.get(vid)
        if want is None:
            want = S(m, "auto_pmv", "1") == "1" and is_pmv(m, win)
        if not want or not getattr(o, "isVisible", lambda: True)():
            return self.set_source(None)
        path = media_path(win)
        if not path:
            return self.set_source(None)
        self.set_source(("video", path), o.player)

    def set_source(self, key, player=None):
        self.player = player
        if key is None:
            self.src_key, self.data = None, None
            self.hide_all()
            return
        if key != self.src_key:
            self.src_key = key
            self.data = None
            self.last_media = None
            self.last_beat = None
        if self.data is None:
            d = ANALYZER.get(key[1])
            if d is not None:
                self.data = d if d.get("beats") else None
                if self.data:
                    self.phase = next((i for i, a in enumerate(self.data["acc"]) if a), 0) % 4
        if self.data:
            self.show_all()
        else:
            self.hide_all()

    # --- temps
    def media_time(self):
        mp = self.player
        try:
            t = mp.get_time()
            playing = bool(mp.is_playing())
        except Exception:
            return None
        if t is None or t < 0:
            return None
        now = time.monotonic()
        rate = 1.0
        try:
            rate = float(mp.get_rate() or 1.0)
        except Exception:
            pass
        t /= 1000.0
        if t != self.last_media:
            # VLC rafraichit son temps par paliers : on recale en douceur
            pred = self.t_ref[0] + (now - self.t_ref[1]) * self.t_ref[2] if self.last_media is not None else t
            if abs(pred - t) > 0.25 or not playing:
                self.t_ref = (t, now, rate)
            else:
                self.t_ref = (pred + (t - pred) * 0.15, now, rate)
            self.last_media = t
        if not playing:
            self.t_ref = (self.t_ref[0], now, 0.0)
            return self.t_ref[0]
        if self.t_ref[2] == 0.0:
            self.t_ref = (self.t_ref[0], now, rate)
        return self.t_ref[0] + (now - self.t_ref[1]) * self.t_ref[2]

    def beat_pos(self, t):
        """Position en temps (float) : indice du temps + fraction jusqu'au suivant."""
        bt = self.data["beats"]
        import bisect
        i = bisect.bisect_right(bt, t) - 1
        if i < 0:
            p = bt[1] - bt[0] if len(bt) > 1 else 0.5
            return (t - bt[0]) / p
        if i >= len(bt) - 1:
            p = bt[-1] - bt[-2] if len(bt) > 1 else 0.5
            return i + (t - bt[-1]) / p
        return i + (t - bt[i]) / (bt[i + 1] - bt[i])

    def upcoming(self, b, window):
        bt, acc, st = self.data["beats"], self.data["acc"], self.data["str"]
        i0 = max(0, int(math.floor(b)))
        out = []
        for k in range(i0, min(len(bt), int(b + window) + 1)):
            dt = k - b
            if 0 <= dt <= window:
                out.append((k, dt, bool(acc[k]), st[k] if k < len(st) else 0.7))
        return out

    # --- boucle
    def tick(self):
        if self.data is None or self.bar is None or not self.bar.isVisible():
            return
        t = self.media_time()
        if t is None:
            return
        t += self.offset
        b = self.beat_pos(t)
        kb = int(math.floor(b))
        bt = self.data["beats"]
        if 0 <= kb < len(bt):
            since = t - bt[kb]
            acc = bool(self.data["acc"][kb])
        else:
            since, acc = 9.0, False
        self.hit = math.exp(-max(0.0, since) * 9.0)
        self.acc = acc
        self.b = b
        if kb != self.last_beat and 0 <= kb < len(bt) and since < 0.15:
            self.on_beat(acc)
        self.last_beat = kb
        self.bar.update()
        if self.fx is not None and (time.monotonic() < self.fx_until):
            self.fx.update()
        elif self.fx is not None and self.fx_until:
            self.fx_until = 0.0
            self.glitch = []
            self.fx.update()
        for n, ws in self.edges.items():
            for w in ws:
                w.update()

    def on_beat(self, acc):
        self.new_beat = True
        if self.e_vig or (self.e_flash and acc):
            self.fx_until = time.monotonic() + 0.35
        if self.e_glitch and acc:
            self.glitch = [(random.random(), 0.006 + random.random() * 0.05, (random.random() - 0.5) * 0.05, i % 2)
                           for i in range(7)]
            self.fx_until = max(self.fx_until, time.monotonic() + 0.13)

    def sparks_draw(self, p, gx, cy, w, h, acc):
        if getattr(self, "new_beat", False):
            self.new_beat = False
            for _ in range(26 if acc else 12):
                self.sparks.append([gx, cy, (random.random() * 2 - 0.4) * w * 0.006, (random.random() * 2 - 1) * h * 0.05,
                                    1.0, MAG if acc else CYAN])
        keep = []
        for s in self.sparks:
            s[0] += s[2]; s[1] += s[3]; s[4] -= 0.045
            if s[4] > 0:
                c = QColor(s[5]); c.setAlphaF(s[4])
                p.fillRect(QRectF(s[0], s[1], 3, 3), c)
                keep.append(s)
        self.sparks = keep[-200:]

    # --- dessin
    def paint_bar(self, p, wdg):
        if self.data is None or not hasattr(self, "b"):
            return
        w, h = wdg.width(), wdg.height()
        p.setOpacity(self.opacity)
        if self.style != "synth":
            band_bg(p, w, h, 1.0)
        STYLE_FN.get(self.style, style_conv)(self, p, w, h, self.b, self.hit, self.acc)

    def paint_fx(self, p, wdg):
        if self.data is None or not hasattr(self, "hit"):
            return
        w, h = wdg.width(), wdg.height()
        hit = self.hit
        if self.e_scan:
            c = QColor(0, 0, 0, 40)
            for y in range(0, h, 4):
                p.fillRect(0, y, w, 1, c)
        if self.e_vig and hit > 0.02:
            r = QRadialGradient(w / 2, h / 2, w * 0.62, w / 2, h / 2)
            r.setColorAt(0.55, QColor(0, 0, 0, 0))
            r.setColorAt(1.0, QColor(40, 0, 60, int(200 * (0.15 + 0.5 * hit))))
            p.fillRect(QRectF(0, 0, w, h), QBrush(r))
        if self.e_flash and self.acc and hit > 0.02:
            c = QColor(MAG); c.setAlphaF(0.22 * hit)
            p.fillRect(QRectF(0, 0, w, h), c)
        for y, hh, dx, k in self.glitch:
            c = QColor(CYAN if k else MAG); c.setAlphaF(0.22)
            p.fillRect(QRectF(dx * w, y * h, w, hh * h), c)
            c2 = QColor(255, 255, 255, 40)
            p.fillRect(QRectF(dx * w * 2, y * h + hh * h * 0.4, w * 0.4, 2), c2)

    def paint_edge(self, p, wdg):
        if self.data is None or not hasattr(self, "hit"):
            return
        col = QColor(MAG if self.acc else CYAN)
        col.setAlphaF(0.30 + 0.70 * self.hit)
        p.fillRect(wdg.rect(), col)

    # --- fenetres
    def ensure(self):
        if self.bar is None:
            self.bar = Overlay(self, self.paint_bar)
        want_fx = self.e_vig or self.e_flash or self.e_glitch or self.e_scan
        if want_fx and self.fx is None:
            self.fx = Overlay(self, self.paint_fx)
        if not want_fx and self.fx is not None:
            self.fx.close(); self.fx = None
        self.geom()
        # bordures
        want = set()
        if self.e_border:
            want = set(self.main.ctrl.players) if self.e_border_all else {self.screen_n}
        scr = self.main.ctrl.screens()
        for n in list(self.edges):
            if n not in want:
                for w in self.edges.pop(n):
                    w.close()
        for n in want:
            if n in self.edges or not (1 <= n <= len(scr)):
                continue
            g = scr[n - 1].geometry()
            t = max(3, int(g.height() * 0.006))
            rects = [(g.x(), g.y(), g.width(), t), (g.x(), g.y() + g.height() - t, g.width(), t),
                     (g.x(), g.y(), t, g.height()), (g.x() + g.width() - t, g.y(), t, g.height())]
            ws = []
            for x, y, ww, hh in rects:
                o = Overlay(self, self.paint_edge)
                o.setGeometry(x, y, ww, hh)
                ws.append(o)
            self.edges[n] = ws

    def show_all(self):
        if self.qscreen is None:
            return
        self.ensure()
        wins = [self.bar] + ([self.fx] if self.fx else []) + [w for ws in self.edges.values() for w in ws]
        for w in wins:
            if not w.isVisible():
                w.show()
            w.raise_()

    def hide_all(self):
        for w in [self.bar, self.fx] + [w for ws in self.edges.values() for w in ws]:
            if w is not None and w.isVisible():
                w.hide()

    def panic(self):
        self.set_source(None)


def music_player(mu):
    for k in ("player", "mp", "media_player", "_player", "_mp"):
        p = getattr(mu, k, None)
        if p is not None and hasattr(p, "get_time"):
            return p
    return None


# ----------------------------------------------------------------------------------------
# Commandes (clavier, téléphone, menu)
# ----------------------------------------------------------------------------------------
def toggle(win=None):
    e = ENGINE
    if e is None:
        return
    m = e.main
    w = win or m.ctrl.players.get(e.screen_n)
    if w is None:
        return
    o = owner(w)
    vid = getattr(o, "video_id", None)
    cur = e.src_key is not None and e.data is not None
    e.forced[vid] = not cur
    if not e.on:
        setS(m, "on", "1")
    e.pick_source()
    try:
        o.osd("Frise de rythme : " + ("analyse…" if (not cur and e.data is None) else ("affichée" if not cur else "masquée")), 1500)
    except Exception:
        pass


def next_style(win=None):
    e = ENGINE
    if e is None:
        return
    keys = [k for k, _ in STYLES]
    i = (keys.index(e.style) + 1) % len(keys) if e.style in keys else 0
    setS(e.main, "style", keys[i])
    _osd(win, "Frise : " + dict(STYLES)[keys[i]])


def shift(win, ms):
    e = ENGINE
    if e is None:
        return
    v = int(round(e.offset * 1000)) + ms
    setS(e.main, "offset", v)
    _osd(win, "Frise : décalage %+d ms" % v)


def _osd(win, text):
    try:
        if win is not None:
            owner(win).osd(text, 1300)
    except Exception:
        pass


PLAYER_KEYS = {
    "p_rythme": lambda w: toggle(w),
    "p_rythme_style": lambda w: next_style(w),
    "p_rythme_moins": lambda w: shift(w, -20),
    "p_rythme_plus": lambda w: shift(w, 20),
}
MOSAIC_KEYS = {}


def remote(cmd, value):
    """Commandes venant du téléphone."""
    if cmd == "toggle":
        toggle()
    elif cmd == "style":
        next_style()
    elif cmd == "shift":
        shift(None, int(value or 0))
    elif cmd == "fx":
        e = ENGINE
        if e:
            on = not (e.e_border or e.e_glitch)
            setS(e.main, "fx_border", "1" if on else "0")
            setS(e.main, "fx_glitch", "1" if on else "0")


def menu(main, m):
    sub = m.addMenu("Rythme (frise néon, PMV)")

    def chk(label, key, default):
        a = sub.addAction(label)
        a.setCheckable(True)
        a.setChecked(S(main, key, default) == "1")
        a.triggered.connect(lambda on: setS(main, key, "1" if on else "0"))
        return a

    chk("Frise activée", "on", "1")
    chk("Automatique pour les vidéos pmv (tag ou titre)", "auto_pmv", "1")
    chk("Suivre la musique pendant le montage au rythme", "montage", "1")
    st = sub.addMenu("Style")
    cur = S(main, "style", "conv")
    for k, lab in STYLES:
        a = st.addAction(lab); a.setCheckable(True); a.setChecked(k == cur)
        a.triggered.connect(lambda _=False, k=k: setS(main, "style", k))
    sz = sub.addMenu("Taille")
    cur = S(main, "size", "m")
    for k, lab, _f in SIZES:
        a = sz.addAction(lab); a.setCheckable(True); a.setChecked(k == cur)
        a.triggered.connect(lambda _=False, k=k: setS(main, "size", k))
    op = sub.addMenu("Opacité")
    cur = float(S(main, "opacity", "0.9") or 0.9)
    for v in (0.5, 0.7, 0.9, 1.0):
        a = op.addAction("%d %%" % int(v * 100)); a.setCheckable(True); a.setChecked(abs(v - cur) < 0.01)
        a.triggered.connect(lambda _=False, v=v: setS(main, "opacity", v))
    sc = sub.addMenu("Écran de la frise")
    cur = S(main, "screen", "0")
    a = sc.addAction("Automatique (le plus grand, au centre) — écran %d" % main_screen_number_auto(main))
    a.setCheckable(True); a.setChecked(cur in ("0", ""))
    a.triggered.connect(lambda: setS(main, "screen", "0"))
    for n in range(1, len(main.ctrl.screens()) + 1):
        a = sc.addAction("Écran %d" % n); a.setCheckable(True); a.setChecked(cur == str(n))
        a.triggered.connect(lambda _=False, n=n: setS(main, "screen", n))
    fx = sub.addMenu("Effets sur les temps")
    for label, key, d in (("Bordure néon qui pulse", "fx_border", "1"),
                          ("… sur tous les écrans en lecture", "fx_border_all", "0"),
                          ("Glitch sur les temps forts", "fx_glitch", "1"),
                          ("Vignette qui respire", "fx_vig", "0"),
                          ("Flash magenta sur les temps forts (attention si sensible à la lumière)", "fx_flash", "0"),
                          ("Lignes de balayage (écran cathodique)", "fx_scan", "0")):
        a = fx.addAction(label); a.setCheckable(True); a.setChecked(S(main, key, d) == "1")
        a.triggered.connect(lambda on, key=key: setS(main, key, "1" if on else "0"))
    off = int(float(S(main, "offset", "0") or 0))
    a = sub.addAction("Décalage : %+d ms   (Alt+← / Alt+→ pendant la lecture)" % off)
    a.setEnabled(False)
    sub.addAction("Remettre le décalage à 0").triggered.connect(lambda: setS(main, "offset", 0))
    sub.addSeparator()
    sub.addAction("Analyser maintenant toutes les vidéos pmv").triggered.connect(lambda: safe(prefetch_all, main))
    from . import pmvgen
    sub.addAction("Créer un PMV…").triggered.connect(lambda: pmvgen.open_dialog(main))


def main_screen_number_auto(main):
    keep = S(main, "screen", "0")
    try:
        db.set_setting(main.conn, "ry_screen", "0")
        return main_screen_number(main)
    finally:
        db.set_setting(main.conn, "ry_screen", keep)


def prefetch_all(main):
    ids = pmv_ids(main.conn)
    paths = []
    for vid in ids:
        try:
            pth = main.ctrl.resolve(vid)[1]
            if pth:
                paths.append(pth)
        except Exception:
            pass
    ANALYZER.prefetch(paths)
    main.status.setText("Rythme : analyse de %d vidéo(s) pmv en tâche de fond" % len(paths))


def card_menu(main, menu_, vids):
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if not vids:
        return {}
    from . import pmvgen
    a = menu_.addAction("♫  Créer un PMV avec %s" % ("cette vidéo" if len(vids) == 1 else "ces %d vidéos" % len(vids)))
    return {a: (lambda v=list(vids): pmvgen.open_dialog(main, v))}


def install(main):
    global ENGINE
    from ..players import Controller
    B_conn = main.conn
    try:
        B_conn.execute(B.SCHEMA)
        B_conn.commit()
    except Exception:
        pass
    ENGINE = Engine(main)
    if not getattr(Controller, "_extras_rythme", False):
        orig_panic = Controller.panic

        def panic(self, *a, **kw):
            if ENGINE is not None:
                safe(ENGINE.panic)
            return orig_panic(self, *a, **kw)
        Controller.panic = panic
        orig_changed = Controller.screens_changed

        def screens_changed(self, *a, **kw):
            r = orig_changed(self, *a, **kw)
            if ENGINE is not None:
                safe(ENGINE.place)
            return r
        Controller.screens_changed = screens_changed
        Controller._extras_rythme = True
    try:
        QApplication.instance().screenAdded.connect(lambda *_: safe(ENGINE.place))
        QApplication.instance().screenRemoved.connect(lambda *_: safe(ENGINE.place))
    except Exception:
        pass
