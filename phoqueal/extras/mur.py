"""Mur d'écrans : une seule vidéo découpée sur plusieurs écrans, selon leur taille et leur place réelles.

- Réglage (« ⋯ → Mur d'écrans… ») : chaque écran est dessiné à l'échelle (taille physique lue sur
  l'écran, modifiable en cm) ; on le glisse à sa vraie place. L'espace entre deux écrans (cadres)
  est compté comme de l'image cachée : les lignes restent droites d'un écran à l'autre.
- La mire de test se met à jour en direct pendant qu'on déplace les écrans.
- Format forcé : par écran (16:9, 16:10, 21:9, 4:3…, ou diagonale en pouces) quand Windows annonce
  une mauvaise taille ; pour la vidéo aussi (pixels non carrés des DVD, vidéos mal étiquetées).
- Lecture : une seule décodeuse VLC ; chaque écran affiche son morceau. Son normal.
  Espace pause · ←/→ 10 s · ↑/↓ volume · M muet · Échap fermer · touche panique.
"""
import ctypes
import json
import math
import re
import subprocess
import sys
import threading

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

from .. import db
from .common import log, safe
from .ecrans import key as screen_key

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
SESSION = None


# ----------------------------------------------------------------------------------------
# Géométrie (pure : testable sans écran)
# ----------------------------------------------------------------------------------------
def mapping(screens, vw, vh, fit="fill"):
    """screens : {cle: {x, y, w, h} en cm}. -> {cle: (sx, sy, sw, sh)} rectangle source en pixels vidéo."""
    if not screens or vw <= 0 or vh <= 0:
        return {}
    x0 = min(s["x"] for s in screens.values()); y0 = min(s["y"] for s in screens.values())
    x1 = max(s["x"] + s["w"] for s in screens.values()); y1 = max(s["y"] + s["h"] for s in screens.values())
    bw, bh = x1 - x0, y1 - y0
    k = (max if fit == "fill" else min)(bw / vw, bh / vh)          # cm par pixel video
    ox = x0 + (bw - vw * k) / 2
    oy = y0 + (bh - vh * k) / 2
    return {c: ((s["x"] - ox) / k, (s["y"] - oy) / k, s["w"] / k, s["h"] / k) for c, s in screens.items()}


def piece(src, vw, vh, tw, th):
    """Partie visible : (rect source dans l'image, rect cible dans la fenêtre) ou None."""
    sx, sy, sw, sh = src
    ix0, iy0 = max(sx, 0.0), max(sy, 0.0)
    ix1, iy1 = min(sx + sw, float(vw)), min(sy + sh, float(vh))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    fx, fy = tw / sw, th / sh
    return ((ix0, iy0, ix1 - ix0, iy1 - iy0),
            ((ix0 - sx) * fx, (iy0 - sy) * fy, (ix1 - ix0) * fx, (iy1 - iy0) * fy))


SCREEN_RATIOS = [("auto", "Automatique (lu par Windows)"), ("16:9", "16:9"), ("16:10", "16:10"),
                 ("21:9", "21:9 (ultra-large)"), ("32:9", "32:9 (super ultra-large)"), ("4:3", "4:3"),
                 ("5:4", "5:4"), ("3:2", "3:2"), ("9:16", "9:16 (vertical)"), ("10:16", "10:16 (vertical)"),
                 ("free", "Libre (largeur et hauteur séparées)")]
VIDEO_RATIOS = [("auto", "Automatique (format de la vidéo)"), ("16:9", "16:9"), ("4:3", "4:3"),
                ("21:9", "21:9 / 2,39:1 (cinéma)"), ("1.85", "1,85:1 (cinéma)"), ("16:10", "16:10"),
                ("1:1", "1:1 (carré)"), ("9:16", "9:16 (vertical)")]
_NAMED = {"21:9": 64 / 27, "1.85": 1.85}


def ratio_of(key):
    """'16:9' -> 1.777… ; 'auto' / 'free' / inconnu -> None."""
    if key in _NAMED:
        return _NAMED[key]
    try:
        a, b = key.split(":")
        return float(a) / float(b)
    except Exception:
        return None


def pixel_ratio(screen):
    g = screen.geometry()
    return g.width() / g.height() if g.height() > 0 else None


def size_for(ratio, w=None, h=None, diag_in=None):
    """Largeur / hauteur (cm) d'après un format et une largeur, une hauteur ou une diagonale (pouces)."""
    if diag_in:
        d = diag_in * 2.54
        w = d * ratio / math.sqrt(1 + ratio * ratio)
        return round(w, 1), round(w / ratio, 1)
    if w is not None:
        return round(w, 1), round(w / ratio, 1)
    return round(h * ratio, 1), round(h, 1)


def diag_in(w, h):
    return math.hypot(w, h) / 2.54


def physical_cm(screen):
    ps = screen.physicalSize()
    w, h = ps.width() / 10.0, ps.height() / 10.0
    g = screen.geometry()
    if w < 5 or h < 5 or w > 400:                               # taille inconnue : estimation
        try:
            dpi = screen.physicalDotsPerInch() or 96
        except Exception:
            dpi = 96
        r = screen.devicePixelRatio() or 1
        w, h = g.width() * r / dpi * 2.54, g.height() * r / dpi * 2.54
    # garde les proportions de l'image affichee
    if g.height() > 0 and h > 0:
        ar = g.width() / g.height()
        if abs(w / h - ar) > 0.05:
            h = w / ar
    return round(w, 1), round(h, 1)


def auto_layout(scr, old=None):
    """Écrans côte à côte (ordre de Windows), cadres de 1 cm par défaut, alignés en bas."""
    old = old or {}
    out = {}
    x = 0.0
    items = sorted(scr, key=lambda s: (s.geometry().x(), s.geometry().y()))
    sizes = []
    for s in items:
        k = screen_key(s)
        o = old.get(k, {})
        w, h = (o["w"], o["h"]) if o.get("w") else physical_cm(s)
        sizes.append((s, k, w, h, o.get("b", 1.0), o.get("on", True)))
    hmax = max((h for _, _, _, h, _, _ in sizes), default=0)
    prev_b = 0.0
    for i, (s, k, w, h, b, on) in enumerate(sizes):
        if i:
            x += prev_b + b
        out[k] = {"x": round(x, 1), "y": round(hmax - h, 1), "w": w, "h": h, "b": b, "on": on}
        x += w
        prev_b = b
    return out


# ----------------------------------------------------------------------------------------
# Murs enregistrés
# ----------------------------------------------------------------------------------------
def walls(conn):
    try:
        d = json.loads(db.get_setting(conn, "walls", "") or "{}")
        return d if isinstance(d, dict) and isinstance(d.get("walls"), list) else {"walls": [], "last": None}
    except Exception:
        return {"walls": [], "last": None}


def save_walls(conn, d):
    db.set_setting(conn, "walls", json.dumps(d))


def get_wall(conn, name=None):
    d = walls(conn)
    name = name or d.get("last")
    for w in d["walls"]:
        if w["name"] == name:
            return w
    return d["walls"][0] if d["walls"] else None


def present(wall):
    """Écrans du mur actuellement branchés et inclus : {cle: (QScreen, rect cm)}."""
    by_key = {screen_key(s): s for s in QApplication.screens()}
    return {k: (by_key[k], v) for k, v in wall["screens"].items() if v.get("on", True) and k in by_key}


# ----------------------------------------------------------------------------------------
# Sources d'image
# ----------------------------------------------------------------------------------------
class Pattern:
    """Mire de test : quadrillage, diagonales et cercles (doivent rester continus d'un écran à l'autre)."""

    def __init__(self, w=1920, h=1080):
        self.w, self.h = w, h
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor("#101320"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#2f3a5c"), 2))
        for x in range(0, w + 1, 60):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + 1, 60):
            p.drawLine(0, y, w, y)
        p.setPen(QPen(QColor("#5b8def"), 6))
        p.drawLine(0, 0, w, h)
        p.drawLine(0, h, w, 0)
        p.setPen(QPen(QColor("#ffcc33"), 6))
        p.setBrush(Qt.NoBrush)
        for r in (180, 360, 520):
            p.drawEllipse(QPointF(w / 2, h / 2), r, r)
        p.setPen(QPen(QColor("#ff4d5e"), 8))
        p.drawRect(4, 4, w - 8, h - 8)
        f = QFont(); f.setPointSize(40); f.setBold(True)
        p.setFont(f); p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, h / 2 - 50, w, 100), Qt.AlignCenter, "PHOQUE AL · MIRE")
        p.end()
        self.img = img

    def frame(self):
        return self.img

    def stop(self):
        pass


def video_size(path):
    from ..config import find_tool
    ff = find_tool("ffmpeg")
    if ff:
        try:
            out = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True,
                                 errors="replace", timeout=20, creationflags=_FLAGS).stderr
            m = re.search(r"Stream #.*?Video:.*?\b(\d{2,5})x(\d{2,5})\b(.*)", out)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                dar = re.search(r"DAR (\d+):(\d+)", m.group(3))          # pixels non carrés (DVD…)
                if dar and int(dar.group(2)) > 0:
                    r = int(dar.group(1)) / int(dar.group(2))
                    if 0.2 < r < 5 and abs(w / h - r) > 0.01:
                        w = int(round(h * r))
                rot = re.search(r"rotat(?:e|ion)\s*[:=]\s*(-?\d+)", out)
                if rot and abs(int(rot.group(1))) % 180 == 90:
                    w, h = h, w
                return w, h
        except Exception as e:
            log("mur : taille %r" % e)
    return 1920, 1080


class VideoFrames:
    """Une seule lecture VLC qui écrit ses images dans une mémoire partagée par les écrans."""

    def __init__(self, path, start=0.0, volume=80, ratio=None):
        import vlc
        w, h = video_size(path)
        if ratio:                                             # format forcé : VLC étire l'image
            w = int(round(h * ratio))
        if w > 3840:                                          # limite raisonnable pour le processeur
            h, w = int(h * 3840 / w), 3840
        self.w, self.h = w - w % 2, h - h % 2
        self.size = self.w * self.h * 4
        self.buf = bytearray(self.size)
        self.cbuf = (ctypes.c_char * self.size).from_buffer(self.buf)
        self.addr = ctypes.addressof(self.cbuf)
        self.dirty = False
        self.img = QImage(self.w, self.h, QImage.Format_RGB32)
        self.img.fill(QColor("black"))
        self.lock = threading.Lock()
        self.inst = vlc.Instance("--aout=directsound,any", "--no-video-title-show", "--quiet")
        self.mp = self.inst.media_player_new()
        media = self.inst.media_new(path)
        if start > 1:
            media.add_option(":start-time=%.1f" % start)
        self.mp.set_media(media)

        @vlc.CallbackDecorators.VideoLockCb
        def _lock(opaque, planes):
            self.lock.acquire()
            planes[0] = self.addr
            return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def _unlock(opaque, picture, planes):
            self.lock.release()

        @vlc.CallbackDecorators.VideoDisplayCb
        def _display(opaque, picture):
            self.dirty = True

        self._cbs = (_lock, _unlock, _display)                  # garde les references
        self.mp.video_set_callbacks(_lock, _unlock, _display, None)
        self.mp.video_set_format("RV32", self.w, self.h, self.w * 4)
        self.mp.audio_set_volume(int(volume))
        self.mp.play()

    def frame(self):
        if self.dirty:
            with self.lock:
                self.dirty = False
                self.img = QImage(bytes(self.buf), self.w, self.h, self.w * 4, QImage.Format_RGB32).copy()
        return self.img

    # commandes
    def toggle(self):
        self.mp.pause()

    def seek(self, s):
        t = self.mp.get_time()
        if t is not None and t >= 0:
            n = self.mp.get_length() or 0
            self.mp.set_time(int(max(0, min(t + s * 1000, (n - 1000) if n > 1000 else t + s * 1000))))

    def volume(self, d):
        v = max(0, min(150, (self.mp.audio_get_volume() or 0) + d))
        self.mp.audio_set_volume(v)
        return v

    def mute(self):
        self.mp.audio_toggle_mute()

    def ended(self):
        import vlc
        return self.mp.get_state() in (vlc.State.Ended, vlc.State.Error)

    def stop(self):
        try:
            self.mp.stop()
            self.mp.release()
            self.inst.release()
        except Exception:
            pass


# ----------------------------------------------------------------------------------------
# Fenêtres
# ----------------------------------------------------------------------------------------
class WallWindow(QWidget):
    def __init__(self, session, k, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.session, self.k, self.scr = session, k, screen
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setStyleSheet("background:black")
        self.setCursor(Qt.BlankCursor)
        self.osd_text, self.osd_timer = "", QTimer(self)
        self.osd_timer.setSingleShot(True)
        self.osd_timer.timeout.connect(lambda: (setattr(self, "osd_text", ""), self.update()))
        for seq, fn in (("Space", session.toggle), ("Left", lambda: session.seek(-10)),
                        ("Right", lambda: session.seek(10)), ("Up", lambda: session.volume(10)),
                        ("Down", lambda: session.volume(-10)), ("M", session.mute), ("Escape", session.stop)):
            QShortcut(QKeySequence(seq), self).activated.connect(fn)
        for seq in session.panic_keys:
            QShortcut(QKeySequence(seq), self).activated.connect(session.panic)

    def show_on_screen(self):
        try:
            from ..players import place_on_screen
            place_on_screen(self, self.scr)
        except Exception:
            self.setGeometry(self.scr.geometry())
            self.showFullScreen()

    def osd(self, text, ms=1400):
        self.osd_text = text
        self.osd_timer.start(ms)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        s = self.session
        img = s.source.frame() if s.source else None
        src = s.map.get(self.k)
        if img is not None and src is not None:
            r = piece(src, img.width(), img.height(), self.width(), self.height())
            if r:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                p.drawImage(QRectF(*r[1]), img, QRectF(*r[0]))
        if s.label:
            f = QFont(); f.setPointSize(60); f.setBold(True)
            p.setFont(f); p.setPen(QColor(255, 255, 255, 200))
            p.drawText(self.rect(), Qt.AlignCenter, s.label(self.k))
        if self.osd_text:
            f = QFont(); f.setPointSize(22); f.setBold(True)
            p.setFont(f)
            r = QRectF(40, 40, 520, 64)
            p.fillRect(r, QColor(0, 0, 0, 170))
            p.setPen(Qt.white)
            p.drawText(r, Qt.AlignCenter, self.osd_text)


class WallSession:
    def __init__(self, main, wall, source, label=None):
        self.main, self.wall, self.source, self.label = main, wall, source, label
        self.panic_keys = []
        try:
            from .. import keymap
            for k, v in keymap.load(main.conn).items():
                if "panic" in k and v:
                    self.panic_keys.append(v)
        except Exception:
            pass
        self.wins = {}
        self.map = {}
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.set_layout(wall)

    def set_layout(self, wall):
        self.wall = wall
        here = present(wall)
        for k in list(self.wins):
            if k not in here:
                self.wins.pop(k).close()
        for k, (scr, _r) in here.items():
            if k not in self.wins:
                w = WallWindow(self, k, scr)
                self.wins[k] = w
                w.show_on_screen()
        img = self.source.frame()
        self.map = mapping({k: r for k, (_s, r) in here.items()}, img.width(), img.height(),
                           wall.get("fit", "fill"))
        for w in self.wins.values():
            w.update()

    def start(self):
        self.timer.start(15)
        first = next(iter(self.wins.values()), None)
        if first is not None:
            QTimer.singleShot(400, lambda: (first.raise_(), first.activateWindow()))

    def tick(self):
        if isinstance(self.source, VideoFrames):
            if self.source.dirty:
                if not self.map:
                    self.set_layout(self.wall)
                for w in self.wins.values():
                    w.update()
            if self.source.ended():
                self.stop()

    def osd(self, text):
        for w in self.wins.values():
            w.osd(text)

    # commandes
    def toggle(self):
        if isinstance(self.source, VideoFrames):
            self.source.toggle()

    def seek(self, s):
        if isinstance(self.source, VideoFrames):
            self.source.seek(s)
            self.osd(("+%d s" if s > 0 else "%d s") % s)

    def volume(self, d):
        if isinstance(self.source, VideoFrames):
            self.osd("Volume %d" % self.source.volume(d))

    def mute(self):
        if isinstance(self.source, VideoFrames):
            self.source.mute()
            self.osd("Son coupé / rétabli")

    def panic(self):
        self.stop()
        try:
            self.main.ctrl.panic()
        except Exception:
            pass

    def stop(self):
        global SESSION
        self.timer.stop()
        for w in self.wins.values():
            w.close()
        self.wins = {}
        self.source.stop()
        if SESSION is self:
            SESSION = None


def stop_current():
    if SESSION is not None:
        SESSION.stop()


def free_screens(main, wall):
    """Ferme les lecteurs ouverts sur les écrans du mur."""
    keys = set(present(wall))
    ctrl = main.ctrl
    try:
        scr = ctrl.screens()
    except Exception:
        return
    for n, w in list(ctrl.players.items()):
        if 1 <= n <= len(scr) and screen_key(scr[n - 1]) in keys:
            try:
                w.close()
            except Exception:
                pass


def play(main, vid, wall=None):
    global SESSION
    wall = wall or get_wall(main.conn)
    if wall is None:
        if open_setup(main) and get_wall(main.conn):
            return play(main, vid)
        return
    if len(present(wall)) < 2:
        QMessageBox.information(main, "Mur d'écrans", "Il faut au moins deux écrans branchés et inclus dans le mur.")
        return
    row, path = main.ctrl.resolve(vid)
    if not path:
        QMessageBox.information(main, "Mur d'écrans", "Fichier introuvable (disque débranché ?).")
        return
    stop_current()
    free_screens(main, wall)
    try:
        src = VideoFrames(path, 0, int(db.get_setting(main.conn, "wall_volume", "80") or 80),
                          ratio_of(wall.get("vr", "auto")))
    except Exception as e:
        log("mur : %r" % e)
        QMessageBox.warning(main, "Mur d'écrans", "Lecture impossible : %s" % e)
        return
    SESSION = WallSession(main, wall, src)
    SESSION.start()
    try:
        main.conn.execute("UPDATE videos SET play_count=COALESCE(play_count,0)+1 WHERE id=?", (vid,))
        main.conn.commit()
    except Exception:
        pass


# ----------------------------------------------------------------------------------------
# Réglage
# ----------------------------------------------------------------------------------------
class Canvas(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setMinimumSize(700, 330)
        self.setMouseTracking(True)
        self.drag = None
        self.k = 1.0
        self.o = QPointF(0, 0)

    def frame(self):
        sc = self.dlg.layout_cm
        if not sc:
            return
        x0 = min(s["x"] - s["b"] for s in sc.values()); y0 = min(s["y"] - s["b"] for s in sc.values())
        x1 = max(s["x"] + s["w"] + s["b"] for s in sc.values()); y1 = max(s["y"] + s["h"] + s["b"] for s in sc.values())
        m = 40
        self.k = min((self.width() - 2 * m) / max(1, x1 - x0), (self.height() - 2 * m) / max(1, y1 - y0))
        self.o = QPointF((self.width() - (x1 - x0) * self.k) / 2 - x0 * self.k,
                         (self.height() - (y1 - y0) * self.k) / 2 - y0 * self.k)

    def rect_of(self, s, bezel=False):
        b = s["b"] if bezel else 0
        return QRectF(self.o.x() + (s["x"] - b) * self.k, self.o.y() + (s["y"] - b) * self.k,
                      (s["w"] + 2 * b) * self.k, (s["h"] + 2 * b) * self.k)

    def paintEvent(self, _e):
        if self.drag is None:
            self.frame()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#15151a"))
        sc = self.dlg.layout_cm
        on = {k: v for k, v in sc.items() if v.get("on", True)}
        num = self.dlg.numbers()
        if on:
            img_w, img_h = self.dlg.preview_size
            mp = mapping(on, img_w, img_h, self.dlg.fit())
            # cadre de l'image video sur le mur
            any_k = next(iter(on))
            sx, sy, sw, sh = mp[any_k]
            s = on[any_k]
            kk = s["w"] / sw
            vx, vy = s["x"] - sx * kk, s["y"] - sy * kk
            r = QRectF(self.o.x() + vx * self.k, self.o.y() + vy * self.k, img_w * kk * self.k, img_h * kk * self.k)
            p.setPen(QPen(QColor("#5b8def"), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
        for k, s in sc.items():
            sel = k == self.dlg.sel
            inc = s.get("on", True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2c2d38"))
            p.drawRoundedRect(self.rect_of(s, True), 5, 5)
            p.setBrush(QColor("#1d2540") if inc else QColor("#17181f"))
            r = self.rect_of(s)
            p.drawRect(r)
            if sel:
                p.setPen(QPen(QColor("#5b8def"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(self.rect_of(s, True).adjusted(-3, -3, 3, 3))
            f = QFont(); f.setPointSize(max(10, int(min(r.height() * 0.28, 30)))); f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#ffffff") if inc else QColor("#6b6c78"))
            p.drawText(r.adjusted(0, -r.height() * 0.12, 0, 0), Qt.AlignCenter, str(num.get(k, "?")))
            f2 = QFont(); f2.setPointSize(9)
            p.setFont(f2)
            p.setPen(QColor("#c9d3f0") if inc else QColor("#6b6c78"))
            p.drawText(r.adjusted(0, r.height() * 0.38, 0, 0), Qt.AlignCenter,
                       "%.0f × %.0f cm" % (s["w"], s["h"]) if inc else "non inclus")
        p.setPen(QColor("#9a9ba8"))
        f = QFont(); f.setPointSize(9); p.setFont(f)
        p.drawText(QRectF(12, self.height() - 24, self.width() - 24, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   "Glisse les écrans à leur place réelle · bordure grise = cadre · pointillés = image de la vidéo")

    def to_cm(self, pos):
        return (pos.x() - self.o.x()) / self.k, (pos.y() - self.o.y()) / self.k

    def mousePressEvent(self, e):
        pos = e.position()
        for k, s in reversed(list(self.dlg.layout_cm.items())):
            if self.rect_of(s, True).contains(pos):
                cx, cy = self.to_cm(pos)
                self.drag = (k, cx - s["x"], cy - s["y"])
                self.dlg.select(k)
                return

    def mouseMoveEvent(self, e):
        if self.drag is None:
            return
        k, dx, dy = self.drag
        cx, cy = self.to_cm(e.position())
        s = self.dlg.layout_cm[k]
        x, y = snap(self.dlg.layout_cm, k, cx - dx, cy - dy, 2.0)
        s["x"], s["y"] = round(x, 1), round(y, 1)
        self.update()
        self.dlg.changed(live=True)

    def mouseReleaseEvent(self, _e):
        if self.drag is not None:
            self.drag = None
            self.update()
            self.dlg.changed()


def ratio_name(r):
    """1.777 -> '16:9' (format courant le plus proche, sinon '1,78:1')."""
    best = None
    for k, _lab in SCREEN_RATIOS:
        v = ratio_of(k)
        if v and abs(v - r) / v < 0.02 and (best is None or abs(v - r) < abs(ratio_of(best) - r)):
            best = k
    return best or ("%.2f:1" % r).replace(".", ",")


def snap(sc, k, x, y, tol):
    """Aimante un écran contre les autres (cadres compris) et aligne hauts / bas / centres."""
    s = sc[k]
    bx, by = None, None
    for o_k, o in sc.items():
        if o_k == k:
            continue
        gap = o["b"] + s["b"]
        for cand in (o["x"] + o["w"] + gap, o["x"] - s["w"] - gap, o["x"], o["x"] + o["w"] - s["w"]):
            if abs(x - cand) <= tol and (bx is None or abs(x - cand) < abs(x - bx)):
                bx = cand
        for cand in (o["y"], o["y"] + o["h"] - s["h"], o["y"] + (o["h"] - s["h"]) / 2,
                     o["y"] + o["h"] + gap, o["y"] - s["h"] - gap):
            if abs(y - cand) <= tol and (by is None or abs(y - cand) < abs(y - by)):
                by = cand
    return (x if bx is None else bx), (y if by is None else by)


class WallDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Mur d'écrans — place tes écrans comme sur ton bureau")
        self.setMinimumSize(900, 640)
        self.data = walls(main.conn)
        self.preview = None
        self.preview_size = (1920, 1080)
        self.sel = None
        self.loading = False
        lay = QVBoxLayout(self)
        info = QLabel("Tailles lues sur les écrans (modifiables). Glisse chaque écran à sa vraie place : "
                      "la vidéo est découpée en conséquence, cadres compris. Lance la mire pour vérifier "
                      "que les lignes restent droites d'un écran à l'autre.")
        info.setWordWrap(True)
        lay.addWidget(info)
        top = QHBoxLayout()
        top.addWidget(QLabel("Mur :"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self.load_wall)
        top.addWidget(self.combo, 1)
        b = QPushButton("Nouveau…"); b.clicked.connect(self.new_wall); top.addWidget(b)
        b = QPushButton("Supprimer"); b.clicked.connect(self.delete_wall); top.addWidget(b)
        lay.addLayout(top)
        self.canvas = Canvas(self)
        lay.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        g1 = QGroupBox("Écran sélectionné")
        f = QFormLayout(g1)
        self.sw, self.sh, self.sb = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for sp, lo, hi in ((self.sw, 5, 400), (self.sh, 5, 300), (self.sb, 0, 10)):
            sp.setRange(lo, hi); sp.setDecimals(1); sp.setSuffix(" cm"); sp.setSingleStep(0.5)
            sp.valueChanged.connect(self.edit)
        self.ratio = QComboBox()
        for k, lab in SCREEN_RATIOS:
            self.ratio.addItem(lab, k)
        self.ratio.setToolTip("Si l'image est écrasée ou étirée sur cet écran, force son vrai format")
        self.ratio.currentIndexChanged.connect(self.edit_ratio)
        self.sd = QDoubleSpinBox()
        self.sd.setRange(5, 200); self.sd.setDecimals(1); self.sd.setSuffix(" pouces"); self.sd.setSingleStep(0.5)
        self.sd.setToolTip("Diagonale de l'écran (sur la fiche ou l'étiquette) : calcule largeur et hauteur")
        self.sw.valueChanged.disconnect(); self.sh.valueChanged.disconnect()
        self.sw.valueChanged.connect(lambda _v: self.edit_dim("w"))
        self.sh.valueChanged.connect(lambda _v: self.edit_dim("h"))
        self.sd.valueChanged.connect(lambda _v: self.edit_dim("d"))
        self.detected = QLabel()
        self.detected.setStyleSheet("color:#8a8a96")
        self.inc = QCheckBox("Inclus dans le mur")
        self.inc.toggled.connect(self.edit)
        f.addRow("Format", self.ratio)
        f.addRow("Diagonale", self.sd)
        f.addRow("Largeur visible", self.sw)
        f.addRow("Hauteur visible", self.sh)
        f.addRow("Cadre (bordure)", self.sb)
        f.addRow(self.detected)
        ab = QPushButton("Même format pour tous les écrans")
        ab.setToolTip("Applique le format de cet écran aux autres (leur largeur est gardée)")
        ab.clicked.connect(self.ratio_all)
        f.addRow(self.inc, ab)
        row.addWidget(g1)
        g2 = QGroupBox("Remplissage")
        v = QVBoxLayout(g2)
        self.r_fill = QRadioButton("Remplir tout le mur (rogne un peu la vidéo)")
        self.r_fit = QRadioButton("Image entière (bandes noires)")
        grp = QButtonGroup(self); grp.addButton(self.r_fill); grp.addButton(self.r_fit)
        self.r_fill.toggled.connect(lambda _on: self.changed())
        v.addWidget(self.r_fill); v.addWidget(self.r_fit)
        hv = QHBoxLayout()
        hv.addWidget(QLabel("Format de la vidéo :"))
        self.vratio = QComboBox()
        for k, lab in VIDEO_RATIOS:
            self.vratio.addItem(lab, k)
        self.vratio.setToolTip("Pour une vidéo écrasée ou étirée sur le mur (mauvaise information dans le fichier)")
        self.vratio.currentIndexChanged.connect(lambda _i: self.loading or self.changed())
        hv.addWidget(self.vratio, 1)
        v.addLayout(hv)
        b = QPushButton("Placement automatique"); b.clicked.connect(self.auto); v.addWidget(b)
        row.addWidget(g2)
        lay.addLayout(row)

        bottom = QHBoxLayout()
        self.mire = QPushButton("Afficher la mire de test")
        self.mire.setCheckable(True)
        self.mire.toggled.connect(self.toggle_mire)
        bottom.addWidget(self.mire)
        b = QPushButton("Identifier les écrans"); b.clicked.connect(self.identify); bottom.addWidget(b)
        bottom.addStretch(1)
        b = QPushButton("Fermer"); b.clicked.connect(self.reject); bottom.addWidget(b)
        b = QPushButton("Enregistrer le mur"); b.setDefault(True); b.clicked.connect(self.save); bottom.addWidget(b)
        lay.addLayout(bottom)

        if not self.data["walls"]:
            self.data["walls"].append({"name": "Mon mur", "fit": "fill", "screens": auto_layout(QApplication.screens())})
        self.fill_combo(self.data.get("last"))

    # --- murs
    def fill_combo(self, name=None):
        self.loading = True
        self.combo.clear()
        for w in self.data["walls"]:
            self.combo.addItem(w["name"])
        self.loading = False
        names = [w["name"] for w in self.data["walls"]]
        self.combo.setCurrentIndex(names.index(name) if name in names else 0)
        self.load_wall()

    def wall(self):
        i = self.combo.currentIndex()
        return self.data["walls"][i] if 0 <= i < len(self.data["walls"]) else None

    def load_wall(self, *_):
        if self.loading:
            return
        w = self.wall()
        if w is None:
            return
        # ecrans branches absents du mur : ajoutes a droite, non inclus
        here = {screen_key(s): s for s in QApplication.screens()}
        missing = [s for k, s in here.items() if k not in w["screens"]]
        if missing:
            right = max((v["x"] + v["w"] + v["b"] for v in w["screens"].values()), default=0)
            for s in missing:
                cw, ch = physical_cm(s)
                w["screens"][screen_key(s)] = {"x": right + 2, "y": 0, "w": cw, "h": ch, "b": 1.0,
                                               "on": not w["screens"]}
                right += cw + 4
        # ecrans du mur non branches : caches pendant le reglage
        self.layout_cm = {k: v for k, v in w["screens"].items() if k in here}
        self.r_fill.blockSignals(True)
        (self.r_fill if w.get("fit", "fill") == "fill" else self.r_fit).setChecked(True)
        self.r_fill.blockSignals(False)
        self.loading = True
        i = self.vratio.findData(w.get("vr", "auto"))
        self.vratio.setCurrentIndex(max(0, i))
        self.loading = False
        self.select(next(iter(self.layout_cm), None))
        self.changed()

    def new_wall(self):
        name, ok = QInputDialog.getText(self, "Nouveau mur", "Nom du mur :")
        if ok and name.strip():
            self.data["walls"].append({"name": name.strip()[:40], "fit": "fill",
                                       "screens": auto_layout(QApplication.screens())})
            self.fill_combo(name.strip()[:40])

    def delete_wall(self):
        w = self.wall()
        if w and len(self.data["walls"]) > 1:
            self.data["walls"].remove(w)
            self.fill_combo()

    def numbers(self):
        try:
            return {screen_key(s): i + 1 for i, s in enumerate(self.main.ctrl.screens())}
        except Exception:
            return {}

    # --- edition
    def fit(self):
        return "fill" if self.r_fill.isChecked() else "fit"

    def select(self, k):
        self.sel = k
        s = self.layout_cm.get(k)
        self.loading = True
        for sp, f in ((self.sw, "w"), (self.sh, "h"), (self.sb, "b")):
            sp.setEnabled(s is not None)
            if s:
                sp.setValue(s[f])
        self.ratio.setEnabled(s is not None)
        self.sd.setEnabled(s is not None)
        if s:
            self.ratio.setCurrentIndex(max(0, self.ratio.findData(s.get("r", "auto"))))
            self.sd.setValue(round(diag_in(s["w"], s["h"]), 1))
        scr = self.screen_obj(k)
        if scr is not None:
            g = scr.geometry()
            dpr = scr.devicePixelRatio() or 1
            pw, ph = int(round(g.width() * dpr)), int(round(g.height() * dpr))
            self.detected.setText("Windows annonce %d × %d px (%s)" % (pw, ph, ratio_name(pw / ph if ph else 0)))
        else:
            self.detected.setText("")
        self.inc.setEnabled(s is not None)
        self.inc.setChecked(bool(s and s.get("on", True)))
        self.loading = False
        self.canvas.update()

    def edit(self, *_):
        if self.loading or self.sel not in self.layout_cm:
            return
        s = self.layout_cm[self.sel]
        s["w"], s["h"], s["b"] = self.sw.value(), self.sh.value(), self.sb.value()
        s["on"] = self.inc.isChecked()
        self.changed()

    # --- format des écrans
    def screen_obj(self, k):
        for scr in QApplication.screens():
            if screen_key(scr) == k:
                return scr
        return None

    def ratio_for(self, s, k):
        """Format imposé à l'écran (largeur / hauteur), None si libre."""
        key = s.get("r", "auto")
        if key == "free":
            return None
        if key == "auto":
            scr = self.screen_obj(k)
            return pixel_ratio(scr) if scr is not None else None
        return ratio_of(key)

    def put(self, s):
        """Recopie largeur / hauteur / diagonale dans les cases sans relancer les calculs."""
        self.loading = True
        self.sw.setValue(s["w"]); self.sh.setValue(s["h"])
        self.sd.setValue(round(diag_in(s["w"], s["h"]), 1))
        self.loading = False

    def edit_dim(self, which):
        if self.loading or self.sel not in self.layout_cm:
            return
        s = self.layout_cm[self.sel]
        r = self.ratio_for(s, self.sel)
        w, h = self.sw.value(), self.sh.value()
        if which == "d":
            r = r or (w / h if h else 16 / 9)                    # libre : garde les proportions actuelles
            w, h = size_for(r, diag_in=self.sd.value())
        elif r:
            w, h = size_for(r, w=w) if which == "w" else size_for(r, h=h)
        s["w"], s["h"] = max(5.0, min(400.0, w)), max(5.0, min(300.0, h))
        self.put(s)
        self.changed()

    def edit_ratio(self, *_):
        if self.loading or self.sel not in self.layout_cm:
            return
        s = self.layout_cm[self.sel]
        s["r"] = self.ratio.currentData()
        r = self.ratio_for(s, self.sel)
        if r:                                                     # largeur gardée, hauteur recalculée
            s["w"], s["h"] = size_for(r, w=s["w"])
            if s["h"] > 300:
                s["w"], s["h"] = size_for(r, h=300.0)
        self.put(s)
        self.changed()

    def ratio_all(self):
        if self.sel not in self.layout_cm:
            return
        key = self.layout_cm[self.sel].get("r", "auto")
        for k, s in self.layout_cm.items():
            s["r"] = key
            r = self.ratio_for(s, k)
            if r:
                s["w"], s["h"] = size_for(r, w=s["w"])
        self.select(self.sel)
        self.changed()

    def auto(self):
        w = self.wall()
        here = [s for s in QApplication.screens()]
        w["screens"].update(auto_layout(here, w["screens"]))
        self.load_wall()

    def changed(self, live=False):
        w = self.wall()
        if w is None:
            return
        w["fit"] = self.fit()
        w["vr"] = self.vratio.currentData() or "auto"
        r = ratio_of(w["vr"]) or 16 / 9
        self.preview_size = (1920, int(round(1920 / r))) if r >= 1 else (int(round(1920 * r)), 1920)
        self.canvas.update()
        if self.preview is not None:
            src = self.preview.source
            if isinstance(src, Pattern) and (src.w, src.h) != self.preview_size:
                self.preview.source = Pattern(*self.preview_size)
            self.preview.set_layout(w)

    # --- mire / identification
    def toggle_mire(self, on):
        global SESSION
        if on:
            if len(present(self.wall())) < 1:
                self.mire.setChecked(False)
                return
            stop_current()
            pat = Pattern(*self.preview_size)
            self.preview = WallSession(self.main, self.wall(), pat,
                                       label=lambda k: str(self.numbers().get(k, "")))
            SESSION = self.preview
            self.mire.setText("Masquer la mire")
            self.raise_(); self.activateWindow()
        else:
            if self.preview is not None:
                self.preview.stop()
                self.preview = None
            self.mire.setText("Afficher la mire de test")

    def identify(self):
        try:
            self.main.ctrl.identify(2500)
        except Exception:
            pass

    def save(self):
        w = self.wall()
        self.data["last"] = w["name"] if w else None
        save_walls(self.main.conn, self.data)
        self.main.status.setText("Mur d'écrans « %s » enregistré" % (w["name"] if w else ""))
        self.accept()

    def done(self, r):
        if self.preview is not None:
            self.preview.stop()
            self.preview = None
        super().done(r)


def open_setup(main):
    return bool(safe(lambda: WallDialog(main).exec()))


# ----------------------------------------------------------------------------------------
# Intégration
# ----------------------------------------------------------------------------------------
def card_menu(main, menu, vids):
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if len(vids) != 1 or len(QApplication.screens()) < 2:
        return {}
    d = walls(main.conn)
    if len(d["walls"]) > 1:
        sub = menu.addMenu("▦  Lire en mur d'écrans")
        out = {}
        for w in d["walls"]:
            a = sub.addAction(w["name"])
            out[a] = (lambda v=vids[0], w=w: play(main, v, w))
        return out
    a = menu.addAction("▦  Lire en mur d'écrans")
    return {a: (lambda v=vids[0]: play(main, v))}


def install(main):
    from ..players import Controller
    if getattr(Controller, "_extras_wall", False):
        return
    orig = Controller.panic

    def panic(self, *a, **kw):
        if SESSION is not None:
            try:
                SESSION.stop()
            except Exception:
                pass
        return orig(self, *a, **kw)

    Controller.panic = panic
    Controller._extras_wall = True
