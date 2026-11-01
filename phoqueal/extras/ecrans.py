"""« Mes écrans » : choisir quel écran porte quel numéro.

L'ordre est mémorisé par écran physique (numéro de série, sinon modèle + nom Windows) dans le
réglage « screen_order ». Les écrans inconnus (nouvel écran branché) viennent après, de gauche à droite.
"""
import json

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from .. import db
from .common import log, safe

ORDER = []                       # cles des ecrans, dans l'ordre choisi
_AUTO = None                     # Controller.screens d'origine (ordre gauche -> droite)


def key(screen):
    """Identifiant stable d'un écran physique."""
    try:
        serial = (screen.serialNumber() or "").strip()
    except Exception:
        serial = ""
    model = ""
    try:
        model = ("%s %s" % (screen.manufacturer() or "", screen.model() or "")).strip()
    except Exception:
        pass
    name = screen.name() or ""
    if serial:
        return "sn:%s:%s" % (model, serial)
    return "nm:%s:%s" % (model, name)


def label(screen):
    g = screen.geometry()
    try:
        model = ("%s %s" % (screen.manufacturer() or "", screen.model() or "")).strip()
    except Exception:
        model = ""
    return "%s  —  %d × %d" % (model or screen.name() or "Écran", g.width(), g.height())


def auto_screens():
    return sorted(QApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))


def ordered(order=None):
    order = ORDER if order is None else order
    scr = auto_screens()
    rank = {k: i for i, k in enumerate(order)}
    known = [s for s in scr if key(s) in rank]
    known.sort(key=lambda s: rank[key(s)])
    return known + [s for s in scr if key(s) not in rank]


def load(conn):
    global ORDER
    try:
        ORDER = list(json.loads(db.get_setting(conn, "screen_order", "[]") or "[]"))
    except Exception:
        ORDER = []


def save(conn, order):
    global ORDER
    ORDER = list(order)
    db.set_setting(conn, "screen_order", json.dumps(ORDER))


def apply(main):
    """Reprend les numéros partout (lecteurs déjà ouverts, menu Écran, télécommande)."""
    for fn in (lambda: main.ctrl.screens_changed(), lambda: main.refresh_screen_choice()):
        try:
            fn()
        except Exception as e:
            log("ecrans apply : %r" % e)


def install(main):
    global _AUTO
    from ..players import Controller
    load(main.conn)
    if not getattr(Controller, "_extras_order", False):
        _AUTO = Controller.screens
        Controller.screens = staticmethod(lambda: ordered())
        Controller._extras_order = True
    if ORDER:
        QTimer.singleShot(0, lambda: apply(main))


# ----------------------------------------------------------------------------------------
# Fenêtre « Mes écrans »
# ----------------------------------------------------------------------------------------
class Plan(QWidget):
    """Schéma des écrans tels que Windows les place, avec leur numéro."""

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setMinimumSize(460, 240)
        self.rects = []

    def paintEvent(self, _e):
        scr = self.dlg.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#15151a"))
        if not scr:
            return
        geo = [s.geometry() for s in scr]
        x0 = min(g.x() for g in geo); y0 = min(g.y() for g in geo)
        x1 = max(g.x() + g.width() for g in geo); y1 = max(g.y() + g.height() for g in geo)
        m = 20
        k = min((self.width() - 2 * m) / max(1, x1 - x0), (self.height() - 2 * m) / max(1, y1 - y0))
        ox = (self.width() - (x1 - x0) * k) / 2
        oy = (self.height() - (y1 - y0) * k) / 2
        self.rects = []
        sel = self.dlg.lst.currentRow()
        for i, (s, g) in enumerate(zip(scr, geo)):
            r = QRectF(ox + (g.x() - x0) * k + 3, oy + (g.y() - y0) * k + 3, g.width() * k - 6, g.height() * k - 6)
            self.rects.append(r)
            p.setPen(QPen(QColor("#5b8def") if i == sel else QColor("#2c2d38"), 3 if i == sel else 2))
            p.setBrush(QColor("#2a3a66") if i == sel else QColor("#1d2540"))
            p.drawRoundedRect(r, 6, 6)
            f = QFont(); f.setPointSize(max(12, int(min(r.height() * 0.32, 40)))); f.setBold(True)
            p.setFont(f); p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignCenter, str(i + 1))

    def mousePressEvent(self, e):
        pos = e.position() if hasattr(e, "position") else e.pos()
        for i, r in enumerate(self.rects):
            if r.contains(pos):
                self.dlg.lst.setCurrentRow(i)
                self.update()
                return


class ScreensDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Mes écrans — numéros")
        self.setMinimumWidth(760)
        self.initial = list(ORDER)
        self.order = [key(s) for s in ordered()]
        lay = QVBoxLayout(self)
        info = QLabel("Choisis quel écran porte quel numéro : clique un écran sur le schéma ou dans la liste, "
                      "puis monte-le ou descends-le. Les écrans affichent leur numéro pendant le réglage.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.plan = Plan(self)
        row.addWidget(self.plan, 3)
        side = QVBoxLayout()
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(lambda _r: self.plan.update())
        side.addWidget(self.lst, 1)
        b = QHBoxLayout()
        self.up = QPushButton("↑  Monter"); self.up.clicked.connect(lambda: self.move(-1))
        self.down = QPushButton("↓  Descendre"); self.down.clicked.connect(lambda: self.move(1))
        b.addWidget(self.up); b.addWidget(self.down)
        side.addLayout(b)
        row.addLayout(side, 2)
        lay.addLayout(row, 1)
        bottom = QHBoxLayout()
        ident = QPushButton("Identifier les écrans"); ident.clicked.connect(self.identify)
        auto = QPushButton("Ordre automatique (gauche → droite)"); auto.clicked.connect(self.auto)
        bottom.addWidget(ident); bottom.addWidget(auto); bottom.addStretch(1)
        cancel = QPushButton("Annuler"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Enregistrer"); ok.setDefault(True); ok.clicked.connect(self.accept)
        bottom.addWidget(cancel); bottom.addWidget(ok)
        lay.addLayout(bottom)
        self.fill(0)
        QTimer.singleShot(300, self.identify)

    def current(self):
        return ordered(self.order)

    def fill(self, sel):
        self.lst.clear()
        for i, s in enumerate(self.current()):
            QListWidgetItem("%d    %s" % (i + 1, label(s)), self.lst)
        self.lst.setCurrentRow(max(0, min(sel, self.lst.count() - 1)))
        self.plan.update()

    def preview(self):
        global ORDER
        ORDER = list(self.order)
        apply(self.main)
        self.identify()

    def identify(self):
        try:
            self.main.ctrl.identify(2500)
        except Exception as e:
            log("identify : %r" % e)

    def move(self, step):
        i = self.lst.currentRow()
        j = i + step
        cur = [key(s) for s in self.current()]
        if i < 0 or not (0 <= j < len(cur)):
            return
        cur[i], cur[j] = cur[j], cur[i]
        self.order = cur
        self.fill(j)
        self.preview()

    def auto(self):
        self.order = [key(s) for s in auto_screens()]
        self.fill(0)
        self.preview()

    def accept(self):
        save(self.main.conn, [key(s) for s in self.current()])
        apply(self.main)
        super().accept()

    def reject(self):
        global ORDER
        ORDER = self.initial
        apply(self.main)
        super().reject()


def open_dialog(main):
    safe(lambda: ScreensDialog(main).exec())
