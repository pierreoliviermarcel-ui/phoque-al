"""Transitions entre deux videos d'un meme ecran (playlist, suivant, theme, bobine, montage).

Une fenetre transparente, toujours au premier plan, se pose sur l'ecran le temps de la transition :
elle montre la derniere image de l'ancienne video (capture) puis la fait disparaitre selon l'effet choisi.
"""
import os
import random

from PySide6.QtCore import QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .. import db
from ..config import DATA_DIR
from .common import log

MODES = [
    ("cut", "Coupe nette (aucune)"),
    ("cross", "Fondu enchaîné"),
    ("black", "Fondu au noir"),
    ("white", "Flash blanc"),
    ("slide", "Glissement"),
    ("zoom", "Zoom avant"),
    ("blur", "Flou"),
    ("random", "Au hasard (varie à chaque fois)"),
]
LABELS = dict(MODES)
ORDER = [m for m, _ in MODES]
EFFECTS = ["cross", "black", "white", "slide", "zoom", "blur"]
ACTIONS = [("p_transition", "player", "Transition entre vidéos : changer d'effet", "T")]
SNAP_DIR = DATA_DIR / "transitions"


def setting(conn):
    mode = db.get_setting(conn, "trans_mode", "cross")
    try:
        dur = float(db.get_setting(conn, "trans_seconds", "0.8"))
    except ValueError:
        dur = 0.8
    return mode, max(0.2, min(3.0, dur))


def cycle(win):
    conn = win.ctrl.conn
    mode, _ = setting(conn)
    new = ORDER[(ORDER.index(mode) + 1) % len(ORDER)] if mode in ORDER else "cross"
    db.set_setting(conn, "trans_mode", new)
    win.osd("Transition : " + LABELS[new], 1500)


PLAYER_KEYS = {"p_transition": cycle}


class Overlay(QWidget):
    def __init__(self, win, effect, pixmap, seconds, hold=True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.effect = effect
        self.pm = pixmap
        self.p = 0.0                      # progression 0 -> 1 (l'ancienne image disparait)
        self.view = getattr(win, "view_mode", "original")
        self.setGeometry(win.frameGeometry())
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(int(seconds * 1000))
        self.anim.valueChanged.connect(self._set)
        self.anim.finished.connect(self.close)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()

    def _set(self, v):
        self.p = float(v)
        self.update()

    def go(self, delay_ms=0):
        QTimer.singleShot(delay_ms, self.anim.start)

    def image_rect(self, pm, scale=1.0):
        W, H = self.width(), self.height()
        if pm is None or pm.isNull():
            return QRectF(0, 0, W, H)
        if self.view == "stretch":
            w, h = W, H
        else:
            k = (max if self.view == "fill" else min)(W / pm.width(), H / pm.height())
            w, h = pm.width() * k, pm.height() * k
        w, h = w * scale, h * scale
        return QRectF((W - w) / 2, (H - h) / 2, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        t = self.p
        e = self.effect
        if e in ("black", "white"):
            # 1re moitie : l'ancienne image s'efface vers la couleur ; 2e moitie : la couleur s'efface
            col = QColor("black" if e == "black" else "white")
            if t < 0.5 and self.pm is not None:
                p.fillRect(self.rect(), col)
                p.setOpacity(1 - t * 2)
                p.drawPixmap(self.image_rect(self.pm), self.pm, QRectF(self.pm.rect()))
            else:
                p.setOpacity(1.0 if t < 0.5 else 2 - t * 2)
                p.fillRect(self.rect(), col)
        elif self.pm is not None:
            if e == "slide":
                r = self.image_rect(self.pm)
                p.fillRect(r.translated(-self.width() * t, 0), QColor("black"))
                p.drawPixmap(r.translated(-self.width() * t, 0), self.pm, QRectF(self.pm.rect()))
            elif e == "zoom":
                p.setOpacity(1 - t)
                r = self.image_rect(self.pm, 1 + 0.6 * t)
                p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
            else:                                   # cross / blur
                p.setOpacity(1 - t)
                r = self.image_rect(self.pm)
                p.fillRect(r, QColor("black"))
                p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        p.end()


def snapshot(player, blur=False):
    """Derniere image affichee par le lecteur VLC (QPixmap) ou None."""
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path = str(SNAP_DIR / ("%d.png" % id(player)))
        if player.video_take_snapshot(0, path, 960, 0) != 0:
            return None
        img = QImage(path)
        try:
            os.remove(path)
        except OSError:
            pass
        if img.isNull():
            return None
        if blur:
            small = img.scaled(48, max(1, 48 * img.height() // max(1, img.width())),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            img = small.scaled(img.width(), img.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return QPixmap.fromImage(img)
    except Exception as e:
        log("capture transition : %r" % e)
        return None


def begin(win, seconds=None, during_montage=False):
    """A appeler juste AVANT de changer de video. Retourne l'overlay (ou None)."""
    conn = win.ctrl.conn
    mode, dur = setting(conn)
    if seconds is not None:
        dur = seconds
    if mode == "cut" or not win.isVisible() or win.isMinimized():
        return None
    effect = random.choice(EFFECTS) if mode == "random" else mode
    if during_montage:
        dur = min(dur, 0.4)
    pm = snapshot(win.player, blur=(effect == "blur"))
    if pm is None and effect not in ("black", "white"):
        effect = "black"
    try:
        return Overlay(win, effect, pm, dur)
    except Exception as e:
        log("overlay : %r" % e)
        return None


def install(main):
    from ..players import PlayerWindow
    if getattr(PlayerWindow, "_extras_trans", False):
        return
    orig_load = PlayerWindow.load
    orig_swap = PlayerWindow.swap_alt

    def load(self, video_id, path, title, start_s):
        ov = None
        if getattr(self, "video_id", None) is not None and self.player.get_time() > 0:
            ov = begin(self)
        r = orig_load(self, video_id, path, title, start_s)
        if ov is not None:
            ov.go(450 if ov.effect not in ("black", "white") else 0)   # le temps que la nouvelle video demarre
        return r

    def swap_alt(self):
        if not self.alt_ready:
            return orig_swap(self)
        on = db.get_setting(self.ctrl.conn, "trans_montage", "0") == "1"
        ov = begin(self, during_montage=True) if on else None
        r = orig_swap(self)
        if ov is not None:
            ov.go(0)
        return r

    PlayerWindow.load = load
    PlayerWindow.swap_alt = swap_alt
    PlayerWindow._extras_trans = True


def menu(main, parent_menu):
    """Sous-menu « Transitions » (menu Plus)."""
    sub = parent_menu.addMenu("Transitions entre vidéos")
    mode, dur = setting(main.conn)
    for key, label in MODES:
        a = sub.addAction(label)
        a.setCheckable(True)
        a.setChecked(key == mode)
        a.triggered.connect(lambda _=False, k=key: db.set_setting(main.conn, "trans_mode", k))
    sub.addSeparator()
    dsub = sub.addMenu("Durée : %.1f s" % dur)
    for v in (0.3, 0.5, 0.8, 1.2, 2.0):
        a = dsub.addAction("%.1f s" % v)
        a.setCheckable(True)
        a.setChecked(abs(v - dur) < 0.01)
        a.triggered.connect(lambda _=False, v=v: db.set_setting(main.conn, "trans_seconds", str(v)))
    m = sub.addAction("Aussi pendant le montage au rythme (transitions courtes)")
    m.setCheckable(True)
    m.setChecked(db.get_setting(main.conn, "trans_montage", "0") == "1")
    m.triggered.connect(lambda on: db.set_setting(main.conn, "trans_montage", "1" if on else "0"))
    return sub
