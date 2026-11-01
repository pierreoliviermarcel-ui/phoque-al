"""Jusqu'à 9 écrans : interface qui s'adapte au nombre d'écrans.

- La télécommande téléphone reçoit la position de chaque écran (mini-carte « 3 en haut, 2 en bas »…).
- « Sur quel écran ? » : à partir de 4 écrans, une mini-carte des écrans placés comme en vrai,
  avec ce qui passe sur chacun ; touches 1 à 9 pour choisir, 0 / Échap pour annuler.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import db
from .common import log, safe

MAX = 9


# ----------------------------------------------------------------------------------------
# Télécommande : position des écrans
# ----------------------------------------------------------------------------------------
def install_remote(main):
    try:
        from ..webremote import WebRemote
    except Exception:
        return
    if getattr(WebRemote, "_extras_geo", False):
        return
    orig = WebRemote.snapshot

    def snapshot(self):
        st = orig(self)
        try:
            scr = self.main.ctrl.screens()
            for e in st.get("screens", []):
                n = e.get("n", 0)
                if 1 <= n <= len(scr):
                    g = scr[n - 1].geometry()
                    e["geo"] = [g.x(), g.y(), g.width(), g.height()]
        except Exception as ex:
            log("geo ecrans : %r" % ex)
        return st

    WebRemote.snapshot = snapshot
    WebRemote._extras_geo = True


# ----------------------------------------------------------------------------------------
# « Sur quel écran ? » en mini-carte
# ----------------------------------------------------------------------------------------
def numbers(main):
    ctrl = main.ctrl
    try:
        nums = list(ctrl.all_numbers())
    except Exception:
        nums = list(range(1, len(ctrl.screens()) + 1))
    return [n for n in nums if 1 <= n <= MAX] or nums[:MAX]


def boxes(main, nums):
    """{numéro: (x, y, w, h)} : position Windows ; écrans du portable satellite en rangée dessous."""
    scr = main.ctrl.screens()
    out = {}
    for n in nums:
        if 1 <= n <= len(scr):
            g = scr[n - 1].geometry()
            out[n] = (g.x(), g.y(), g.width(), g.height())
    rest = [n for n in nums if n not in out]
    if rest:
        if out:
            bw = sum(b[2] for b in out.values()) / len(out) * 0.7
            bh = sum(b[3] for b in out.values()) / len(out) * 0.7
            x0 = min(b[0] for b in out.values())
            y0 = max(b[1] + b[3] for b in out.values()) + bh * 0.15
        else:
            bw, bh, x0, y0 = 1344, 756, 0, 0
        for i, n in enumerate(rest):
            out[n] = (x0 + i * bw * 1.05, y0, bw, bh)
    return out


class MapPicker(QDialog):
    def __init__(self, main, nums):
        super().__init__(main, Qt.Popup | Qt.FramelessWindowHint)
        self.main, self.nums, self.choice = main, nums, -1
        self.box = boxes(main, nums)
        self.rects = {}
        self.hover = None
        self.setMouseTracking(True)
        self.setStyleSheet("background:#17181f")
        n = len(nums)
        self.resize(560 if n <= 6 else 640, 380 if n <= 6 else 430)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        t = QLabel("Sur quel écran ?   <span style='color:#8b8c99'>touche 1 à %d · Échap annule</span>" % max(nums))
        t.setStyleSheet("color:#e9e9f0;font-size:15px;font-weight:600")
        lay.addWidget(t)
        self.area = QWidget(self)
        self.area.setMouseTracking(True)
        lay.addWidget(self.area, 1)
        self.area.paintEvent = self.paint_area
        self.area.mouseMoveEvent = self.move_area
        self.area.mousePressEvent = self.press_area
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel, 0, Qt.AlignRight)
        for k in range(1, 10):
            QShortcut(QKeySequence(str(k)), self).activated.connect(lambda k=k: self.pick(k))
        QShortcut(QKeySequence("0"), self).activated.connect(self.reject)

    def info(self, n):
        p = self.main.ctrl.players.get(n)
        if p is None:
            return "libre", False
        if self.main.discreet:
            return "en lecture", True
        try:
            if getattr(p, "is_mosaic", False):
                return "mosaïque", True
            t = p.display_title() if hasattr(p, "display_title") else getattr(p, "title", "")
            return (t or "en lecture"), True
        except Exception:
            return "en lecture", True

    def paint_area(self, _e):
        a = self.area
        p = QPainter(a)
        p.setRenderHint(QPainter.Antialiasing)
        bx = self.box.values()
        x0 = min(b[0] for b in bx); y0 = min(b[1] for b in bx)
        x1 = max(b[0] + b[2] for b in bx); y1 = max(b[1] + b[3] for b in bx)
        k = min(a.width() / max(1, x1 - x0), a.height() / max(1, y1 - y0))
        ox = (a.width() - (x1 - x0) * k) / 2
        oy = (a.height() - (y1 - y0) * k) / 2
        self.rects = {}
        for n, (x, y, w, h) in self.box.items():
            r = QRectF(ox + (x - x0) * k + 4, oy + (y - y0) * k + 4, w * k - 8, h * k - 8)
            self.rects[n] = r
            text, busy = self.info(n)
            hov = n == self.hover
            p.setPen(QPen(QColor("#5b8def") if hov else QColor("#2c2d38"), 2))
            p.setBrush(QColor("#2a3a66") if hov else (QColor("#1d2540") if busy else QColor("#20212b")))
            p.drawRoundedRect(r, 8, 8)
            f = QFont(); f.setPointSize(max(14, int(min(r.height() * 0.30, 34)))); f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#ffffff") if busy or hov else QColor("#9a9ba8"))
            p.drawText(r.adjusted(0, 0, 0, -r.height() * 0.25), Qt.AlignCenter, str(n))
            f2 = QFont(); f2.setPointSize(9)
            p.setFont(f2)
            p.setPen(QColor("#c9d3f0") if busy else QColor("#7a7b88"))
            fm = p.fontMetrics()
            p.drawText(r.adjusted(6, r.height() * 0.55, -6, 0), Qt.AlignHCenter | Qt.AlignTop,
                       fm.elidedText(text, Qt.ElideRight, int(r.width() - 12)))

    def at(self, e):
        pos = e.position()
        for n, r in self.rects.items():
            if r.contains(pos):
                return n
        return None

    def move_area(self, e):
        h = self.at(e)
        if h != self.hover:
            self.hover = h
            self.area.update()

    def press_area(self, e):
        n = self.at(e)
        if n is not None:
            self.pick(n)

    def pick(self, n):
        if n in self.nums:
            self.choice = n
            self.accept()


def install_picker(main):
    from ..ui import MainWindow
    if getattr(MainWindow, "_extras_picker", False):
        return
    orig = MainWindow.ask_screen

    def ask_screen(self, *a, **kw):
        try:
            nums = numbers(self)
            if len(nums) < 4 or db.get_setting(self.conn, "picker_map", "1") != "1":
                return orig(self, *a, **kw)
            # un écran imposé (menu « Écran : … »), un écran libre ou la question désactivée : comportement habituel
            sc = getattr(self, "screen_choice", None)
            forced = sc.currentData() if hasattr(sc, "currentData") else 0
            if forced or any(n not in self.ctrl.players for n in range(1, len(self.ctrl.screens()) + 1)) \
                    or db.get_setting(self.conn, "ask_screen", "1") != "1":
                return orig(self, *a, **kw)
            d = MapPicker(self, nums)
            pos = QCursor.pos()
            d.move(pos.x() - d.width() // 2, pos.y() - d.height() // 2)
            # les touches 1 à 9 globales (aller au lecteur) sont suspendues pendant le choix
            shorts = [x for x in getattr(self.ctrl, "_shortcuts", []) if x.isEnabled()]
            for x in shorts:
                x.setEnabled(False)
            safe(self.ctrl.show_numbers, True)
            try:
                d.exec()
            finally:
                safe(self.ctrl.show_numbers, False)
                for x in shorts:
                    x.setEnabled(True)
            return d.choice if d.choice > 0 else -1
        except Exception as e:
            log("choix d'écran : %r" % e)
            return orig(self, *a, **kw)

    MainWindow.ask_screen = ask_screen
    MainWindow._extras_picker = True


# ----------------------------------------------------------------------------------------
# Clavier : Ctrl + 1 à 9
# ----------------------------------------------------------------------------------------
def install_keys(main):
    if db.get_setting(main.conn, "ctrl_digits", "1") != "1":
        return
    main._digit_keys = []
    for k in range(0, 10):
        sc = QShortcut(QKeySequence("Ctrl+%d" % k), main)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(lambda k=k: safe(lambda: main.ctrl.focus(k)))
        main._digit_keys.append(sc)


def install(main):
    install_remote(main)
    install_picker(main)
