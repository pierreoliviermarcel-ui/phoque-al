"""Fond flou : quand la video ne remplit pas tout l'ecran, les bords sont combles par une version
floue et assombrie de l'image (comme sur les telephones / reseaux sociaux).

Leger : pas de deuxieme decodage. Une capture de l'image est prise toutes les ~1,5 s, reduite
puis agrandie (effet de flou), avec un fondu entre deux captures.
"""
import os
import time

from PySide6.QtCore import QRect, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPixmap

from . import db
from .config import DATA_DIR

BLUR_DIR = DATA_DIR / "blur"
FIXED = {"4:3": 4 / 3, "16:9": 16 / 9, "16:10": 16 / 10, "21:9": 21 / 9, "5:4": 5 / 4, "1:1": 1.0, "9:16": 9 / 16}


def blur_enabled(conn):
    return db.get_setting(conn, "blur_fill", "1") == "1"


class BlurMixin:
    """A melanger dans un widget qui possede : player, view_mode, layout() avec marges, ctrl.conn."""

    def blur_init(self, margin, interval_ms=1500):
        self._b_margin = margin
        self._b_on = blur_enabled(self.ctrl.conn)
        self._b_pm = None
        self._b_prev = None
        self._b_alpha = 1.0
        self._b_margins = None
        self._b_shot = 0
        self._b_anim = QVariantAnimation(self)
        self._b_anim.setDuration(600)
        self._b_anim.setStartValue(0.0)
        self._b_anim.setEndValue(1.0)
        self._b_anim.valueChanged.connect(self._b_fade)
        self._b_timer = QTimer(self)
        self._b_timer.setInterval(interval_ms)
        self._b_timer.timeout.connect(self.blur_tick)
        self._b_timer.start()
        BLUR_DIR.mkdir(parents=True, exist_ok=True)

    # --- reglage ------------------------------------------------------------------
    def set_blur(self, on, show=True):
        self._b_on = bool(on)
        if not on:
            self._b_pm = self._b_prev = None
        self.blur_layout()
        self.update()
        if show and hasattr(self, "osd"):
            self.osd("Fond flou activé" if on else "Fond flou désactivé", 1500)
        if on:
            QTimer.singleShot(300, self.blur_tick)

    def toggle_blur(self):
        self.set_blur(not self._b_on)

    # --- geometrie -----------------------------------------------------------------
    def blur_aspect(self):
        """Format de l'image affichee, ou None si elle remplit deja toute la surface."""
        mode = self.view_mode
        if mode in ("fill", "stretch"):
            return None
        if mode in FIXED:
            return FIXED[mode]
        try:
            w, h = self.player.video_get_size(0)
        except Exception:
            return None
        return (w / h) if w and h else None

    def blur_layout(self):
        m = self._b_margin
        margins = (m, m, m, m)
        a = self.blur_aspect() if self._b_on else None
        W, H = self.width() - 2 * m, self.height() - 2 * m
        if a and W > 20 and H > 20:
            w = min(W, int(H * a))
            h = min(H, int(w / a))
            mx, my = (W - w) // 2, (H - h) // 2
            if mx > 2 or my > 2:
                margins = (m + mx, m + my, m + (W - w - mx), m + (H - h - my))
        if margins != self._b_margins:
            self._b_margins = margins
            self.layout().setContentsMargins(*margins)
            for p in (getattr(self, "player", None), getattr(self, "alt_player", None)):
                try:
                    p.video_set_scale(0)
                except Exception:
                    pass
            self.update()

    def blur_active(self):
        if not self._b_on or not self._b_margins:
            return False
        m = self._b_margin
        return any(v > m + 2 for v in self._b_margins)

    # --- captures ---------------------------------------------------------------------
    def blur_tick(self):
        try:
            self.blur_layout()
            if not self.blur_active() or not self.isVisible():
                return
            self._b_shot ^= 1
            path = str(BLUR_DIR / ("%d_%d.png" % (id(self), self._b_shot)))
            if self.player.video_take_snapshot(0, path, 96, 0) == 0:
                QTimer.singleShot(250, lambda: self._b_load(path))
        except RuntimeError:
            pass

    def _b_load(self, path):
        img = QImage(path)
        try:
            os.remove(path)
        except OSError:
            pass
        if img.isNull() or self.width() < 10:
            return
        small = img.scaled(28, max(1, int(28 * img.height() / max(1, img.width()))),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = small.scaled(10, max(1, int(10 * img.height() / max(1, img.width()))),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        big = QPixmap.fromImage(small).scaled(self.width(), self.height(),
                                              Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm = QPixmap(self.width(), self.height())
        pm.fill(QColor("black"))
        p = QPainter(pm)
        p.drawPixmap((self.width() - big.width()) // 2, (self.height() - big.height()) // 2, big)
        p.fillRect(pm.rect(), QColor(0, 0, 0, 95))          # leger assombrissement
        p.end()
        self._b_prev = self._b_pm
        self._b_pm = pm
        self._b_alpha = 0.0
        self._b_anim.stop()
        self._b_anim.start()

    def _b_fade(self, v):
        self._b_alpha = float(v)
        self.update()

    # --- dessin ---------------------------------------------------------------------------
    def paintEvent(self, event):
        if not (self._b_on and self._b_pm is not None and self.blur_active()):
            return super().paintEvent(event)
        p = QPainter(self)
        if self._b_prev is not None and self._b_alpha < 1:
            p.drawPixmap(0, 0, self._b_prev)
        p.setOpacity(self._b_alpha if self._b_prev is not None else 1.0)
        p.drawPixmap(0, 0, self._b_pm)
        p.setOpacity(1.0)
        border = self.palette().color(QPalette.Window)
        if border.lightness() > 40:                         # cadre bleu du lecteur actif / case choisie
            pen = QPen(border, self._b_margin * 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.rect())
        p.end()
