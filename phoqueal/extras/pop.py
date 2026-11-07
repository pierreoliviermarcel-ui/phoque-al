"""POP mode : un repère visuel et sonore à intervalle régulier, sur tous les écrans en même temps.

- Désactivé par défaut ; un avertissement s'affiche quand on l'active.
- Une seule « session » pour toutes les vidéos en cours (plusieurs écrans = un seul compte à rebours) ;
  le temps ne compte que pendant la lecture, il se remet à zéro après 10 min sans lecture.
- Intervalle réglable (3 min par défaut), ajusté selon la phase de la séance en cours :
  calme ×1,5 · montée ×1 · rapide ×0,85 · intense / final ×0,75 · ralentis ×1,25 · stop et redescente : aucun.
- Logo : mot « POP » néon par défaut, ou une image choisie ; jingle au choix (6 intégrés) ou fichier perso.
- Masqué en mode discret et par la touche panique. Activation aussi depuis le téléphone (onglet Musique).
- Réglages : ⋯ → Musique et PMV → POP mode… (onglet « Phrases » : phrases d'encouragement de la séance).
"""
import math
import os
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from .. import db
from ..config import DATA_DIR, find_tool
from . import neonstyle as ns
from .common import log, safe

PUBLIC = __name__.startswith("phoqueal")
PRIV = DATA_DIR / "pop"
DUR = 6.0                    # durée d'affichage du logo (s)
SIZE = 270                   # taille de la fenêtre du logo (px)
MULT = {"CALME": 1.5, "MONTEE": 1.0, "RAPIDE": 0.85, "INTENSE": 0.75, "FINAL": 0.75, "RALENTIS": 1.25,
        "STOP": None, "REDESCENTE": None}
FLOOR = 120.0                # version publique : jamais moins de 2 min
_FLAGS = 0x08000000 if sys.platform == "win32" else 0

WARN_DEFAULT = (
    "<b>Avant d'activer le POP mode</b><br><br>"
    "Si tu associes ces moments à un produit à inhaler :<ul>"
    "<li>jamais avec des médicaments contre les troubles de l'érection, ni avec d'autres produits ;</li>"
    "<li>ne jamais l'avaler, éviter la peau et les yeux, tenir loin d'une flamme ;</li>"
    "<li>espacer les prises, rester assis ou allongé, aérer la pièce ;</li>"
    "<li>malaise, vertiges, troubles de la vue, lèvres bleutées : arrêter tout de suite.</li></ul>"
    "Urgence : 112 (15 en France).")

ENGINE = None


def S(main, k, d):
    return db.get_setting(main.conn, "pop_" + k, d)


def setS(main, k, v):
    db.set_setting(main.conn, "pop_" + k, str(v))


def is_on(main):
    return S(main, "on", "0") == "1"


def interval(main):
    try:
        v = float(S(main, "interval", "180") or 180)
    except ValueError:
        v = 180.0
    return max(FLOOR, v) if PUBLIC else max(10.0, v)


# ----------------------------------------------------------------------------------------
# Avertissement
# ----------------------------------------------------------------------------------------
def warn_text():
    f = PRIV / "avertissement.txt"
    try:
        if f.is_file():
            return f.read_text(encoding="utf-8")
    except OSError:
        pass
    return ns.tr(WARN_DEFAULT)


def needs_warning(main):
    if PUBLIC:
        return True
    try:
        return float(S(main, "warn_until", "0") or 0) < time.time()
    except ValueError:
        return True


def plain(html):
    import re
    t = re.sub(r"<li>", "• ", html)
    t = re.sub(r"<br\s*/?>|</li>|</ul>|<ul>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def ask_warning(main, parent=None):
    """Fenêtre d'avertissement sur le PC ; True si accepté."""
    from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
    d = QDialog(parent or main)
    d.setWindowTitle("POP mode")
    d.setMinimumWidth(520)
    lay = QVBoxLayout(d)
    lab = QLabel(warn_text())
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    lay.addWidget(lab)
    later = None
    if not PUBLIC:
        later = QCheckBox("Ne plus afficher pendant 30 jours")
        lay.addWidget(later)
    row = QHBoxLayout()
    row.addStretch(1)
    no = QPushButton("Annuler")
    ok = QPushButton("Activer le POP mode")
    ok.setDefault(True)
    row.addWidget(no); row.addWidget(ok)
    lay.addLayout(row)
    no.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    if d.exec() != QDialog.Accepted:
        return False
    if later is not None and later.isChecked():
        setS(main, "warn_until", time.time() + 30 * 86400)
    return True


def set_on(main, on, ask=True, parent=None):
    """Active / désactive ; à l'activation, avertissement (sur le PC si ask)."""
    if on and ask and needs_warning(main) and not ask_warning(main, parent):
        return False
    setS(main, "on", "1" if on else "0")
    if ENGINE:
        ENGINE.reset()
    main.status.setText("POP mode activé" if on else "POP mode désactivé")
    return True


# ----------------------------------------------------------------------------------------
# Jingle
# ----------------------------------------------------------------------------------------
def jingle_path(main):
    j = S(main, "jingle", "1")
    if j == "none":
        return None
    if j == "file":
        p = PRIV / "perso.wav"
        return str(p) if p.is_file() else None
    try:
        n = max(1, min(6, int(j)))
    except ValueError:
        n = 1
    p = PRIV / ("jingle_%d.wav" % n)
    if not p.is_file():
        PRIV.mkdir(parents=True, exist_ok=True)
        from .popjingles import make
        make(n, p)
    return str(p)


def import_sound(main, src):
    """Fichier son de l'utilisateur -> data/pop/perso.wav (converti par FFmpeg si besoin)."""
    PRIV.mkdir(parents=True, exist_ok=True)
    dst = PRIV / "perso.wav"
    if src.lower().endswith(".wav"):
        shutil.copyfile(src, dst)
        return True
    ff = find_tool("ffmpeg")
    if not ff:
        return False
    r = subprocess.run([ff, "-v", "error", "-y", "-i", src, "-t", "8", "-ac", "2", "-ar", "44100", str(dst)],
                       capture_output=True, timeout=60, creationflags=_FLAGS)
    return r.returncode == 0 and dst.is_file()


class Player:
    def __init__(self):
        self.fx = None
        try:
            from PySide6.QtMultimedia import QSoundEffect
            self.fx = QSoundEffect()
        except Exception:
            self.fx = None
        self.src = None

    def play(self, path, volume):
        if not path:
            return
        if self.fx is not None:
            from PySide6.QtCore import QUrl
            if self.src != path:
                self.fx.setSource(QUrl.fromLocalFile(path))
                self.src = path
            self.fx.setVolume(max(0.0, min(1.0, volume / 100.0)))
            if self.fx.status() == type(self.fx).Status.Loading:
                QTimer.singleShot(150, lambda: self.fx.play())
            else:
                self.fx.play()
        elif sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


# ----------------------------------------------------------------------------------------
# Logo à l'écran
# ----------------------------------------------------------------------------------------
def image_path(main):
    p = S(main, "image", "") or ""
    if p and os.path.isfile(p):
        return p
    if not PUBLIC:
        for name in ("logo.png",):
            f = PRIV / name
            if f.is_file():
                return str(f)
    return None


class PopWindow(QWidget):
    def __init__(self, engine, screen, pixmap):
        from .rythme import overlay_flags
        super().__init__(None, overlay_flags())
        self.engine = engine
        self.pix = pixmap
        self.t0 = time.monotonic()
        for a in (Qt.WA_TranslucentBackground, Qt.WA_ShowWithoutActivating, Qt.WA_TransparentForMouseEvents,
                  Qt.WA_NoSystemBackground):
            self.setAttribute(a)
        g = screen.geometry()
        self.setGeometry(g.x() + g.width() - SIZE - 18, g.y() + g.height() - SIZE - 14, SIZE, SIZE)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        self.timer.start(33)

    def step(self):
        if time.monotonic() - self.t0 > DUR or self.engine.must_hide():
            self.timer.stop()
            self.close()
            try:
                self.engine.windows.remove(self)
            except (ValueError, AttributeError):
                pass
            self.deleteLater()
            return
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        t = time.monotonic() - self.t0
        a = min(1.0, t / 0.35) * min(1.0, max(0.0, (DUR - t) / 0.8))
        bounce = 1.0 + 0.18 * math.exp(-t * 6) * math.sin(t * 16) if t > 0.05 else 0.7
        W = self.width()
        c = QPointF(W / 2, W * 0.56)
        col = QColor("#ffae3c") if self.pix else QColor("#ff3ec8")
        # halo
        gr = QRadialGradient(c, W * 0.42)
        h = QColor(col); h.setAlphaF(0.28 * a)
        gr.setColorAt(0, h)
        h2 = QColor(col); h2.setAlphaF(0.0)
        gr.setColorAt(1, h2)
        p.setPen(Qt.NoPen); p.setBrush(gr)
        p.drawEllipse(c, W * 0.42, W * 0.42)
        # anneaux qui s'élargissent
        p.setBrush(Qt.NoBrush)
        for k in range(3):
            ph = ((t * 0.8) + k / 3.0) % 1.0
            r = W * (0.16 + 0.30 * ph)
            rc = QColor(col); rc.setAlphaF(a * 0.55 * (1 - ph))
            p.setPen(QPen(rc, 2.2))
            p.drawEllipse(c, r, r)
        p.save()
        p.translate(c)
        p.scale(bounce, bounce)
        if self.pix:
            ph = self.pix.height()
            pw = self.pix.width()
            p.setOpacity(a)
            p.drawPixmap(QRectF(-pw / 2, -ph / 2 - 6, pw, ph), self.pix, QRectF(self.pix.rect()))
        else:
            p.rotate(-8)
            f = ns.font("pop", 74)
            ns.glow_text(p, QRectF(-W / 2, -60, W, 110), "POP", f, "#ff3ec8", glow=4, alpha=a)
            # bulles qui éclatent
            for k, (bx, by, br) in enumerate(((92, -52, 11), (112, -78, 6), (-98, -40, 5))):
                ph = ((t * 0.9) + k * 0.33) % 1.0
                bc = QColor("#ff8be0"); bc.setAlphaF(a * (1 - ph))
                p.setPen(QPen(bc, 2)); p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(bx, by - 18 * ph), br * (1 + ph), br * (1 + ph))
        p.restore()


# ----------------------------------------------------------------------------------------
# Moteur
# ----------------------------------------------------------------------------------------
class Engine:
    def __init__(self, main):
        self.main = main
        self.acc = 0.0           # temps de lecture depuis le dernier POP
        self.session = 0.0       # temps de lecture de la session
        self.idle = 0.0
        self.last = time.monotonic()
        self.windows = []
        self.player = Player()
        self.timer = QTimer(main)
        self.timer.timeout.connect(lambda: safe(self.tick))
        self.timer.start(1000)

    def reset(self):
        self.acc = 0.0
        self.session = 0.0
        self.idle = 0.0

    def must_hide(self):
        m = self.main
        return bool(getattr(m, "discreet", False) or getattr(m.ctrl, "hidden", False))

    def seance(self):
        try:
            from . import seance
            return seance.SESSION
        except Exception:
            return None

    def phase_key(self):
        s = self.seance()
        if s is None or s.idx < 0:
            return None
        return ns.KEY.get(s.phases[s.idx][0])

    def active(self):
        s = self.seance()
        if s is not None:
            return s.paused_until is None and s.ending is None
        for w in list(self.main.ctrl.players.values()):
            try:
                if w.is_playing():
                    return True
            except Exception:
                pass
        return False

    def target(self):
        """Intervalle courant (s) ; None = pas de POP pendant cette phase."""
        key = self.phase_key()
        mult = MULT.get(key, 1.0) if key else 1.0
        if mult is None:
            return None
        t = interval(self.main) * mult
        if PUBLIC:
            t *= min(1.5, 1.0 + 0.1 * int(self.session // 1800))
            t = max(FLOOR, t)
        return t

    def next_in(self):
        t = self.target()
        return None if t is None else max(0.0, t - self.acc)

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(5.0, now - self.last), now
        if not is_on(self.main) or self.must_hide():
            return
        if not self.active():
            self.idle += dt
            if self.idle > 600:
                self.reset()
            return
        self.idle = 0.0
        self.session += dt
        t = self.target()
        if t is None:
            return
        self.acc += dt
        if self.acc >= t:
            self.acc = 0.0
            self.fire()

    def fire(self):
        if self.must_hide():
            return
        m = self.main
        pix = None
        path = image_path(m)
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                pix = pm.scaled(int(SIZE * 0.62), int(SIZE * 0.62), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        try:
            screens = m.ctrl.screens()
        except Exception:
            from PySide6.QtWidgets import QApplication
            screens = QApplication.screens()
        alive = []
        for w in self.windows:
            try:
                if w.isVisible():
                    alive.append(w)
            except RuntimeError:                      # fenêtre déjà détruite
                pass
        self.windows = alive
        for scr in screens:
            w = PopWindow(self, scr, pix)
            w.show()
            w.raise_()
            self.windows.append(w)
        try:
            self.player.play(jingle_path(m), int(S(m, "volume", "70") or 70))
        except Exception as e:
            log("pop jingle : %r" % e)


# ----------------------------------------------------------------------------------------
# Réglages
# ----------------------------------------------------------------------------------------
def open_dialog(main):
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                                   QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
                                   QTabWidget, QVBoxLayout, QWidget)
    from .popjingles import NAMES
    d = QDialog(main)
    d.setWindowTitle("POP mode")
    d.setMinimumSize(620, 560)
    lay = QVBoxLayout(d)
    tabs = QTabWidget()
    lay.addWidget(tabs)

    # --- réglages
    g = QWidget()
    f = QFormLayout(g)
    on = QCheckBox("Activer le POP mode")
    on.setChecked(is_on(main))

    def toggled(v):
        if not set_on(main, v, ask=True, parent=d):
            on.blockSignals(True); on.setChecked(False); on.blockSignals(False)
    on.toggled.connect(toggled)
    f.addRow(on)
    iv = QDoubleSpinBox()
    iv.setDecimals(1); iv.setSingleStep(0.5); iv.setSuffix(" min")
    iv.setRange(FLOOR / 60 if PUBLIC else 0.5, 60)
    iv.setValue(interval(main) / 60)
    iv.valueChanged.connect(lambda v: setS(main, "interval", int(round(v * 60))))
    f.addRow("Intervalle", iv)
    note = ("En séance, l'intervalle suit la phase : calme ×1,5 · montée ×1 · rapide ×0,85 · intense ×0,75 · "
            "ralentis ×1,25 · rien pendant stop et redescente. Hors séance : intervalle fixe, compté pendant la "
            "lecture (toutes les vidéos en cours forment une seule session).")
    if PUBLIC:
        note += " Minimum 2 min ; l'intervalle s'allonge de 10 % toutes les 30 min de session (jusqu'à +50 %)."
    nl = QLabel(note); nl.setWordWrap(True); nl.setStyleSheet("color:#8b8c99")
    f.addRow(nl)
    # image
    img = QComboBox()
    img.addItem("Logo par défaut" + ("" if PUBLIC else " (ton image dans data\\pop si présente, sinon « POP »)"), "")
    img.addItem("Mon image…", "pick")
    cur = S(main, "image", "")
    if cur:
        img.addItem(os.path.basename(cur), cur)
        img.setCurrentIndex(2)

    def img_changed(i):
        v = img.itemData(i)
        if v == "pick":
            path, _ = QFileDialog.getOpenFileName(d, "Image du POP mode", "", "Images (*.png *.jpg *.jpeg *.webp *.gif)")
            if path:
                setS(main, "image", path)
                img.blockSignals(True)
                if img.count() > 2:
                    img.removeItem(2)
                img.addItem(os.path.basename(path), path)
                img.setCurrentIndex(2)
                img.blockSignals(False)
            else:
                img.setCurrentIndex(2 if img.count() > 2 else 0)
        else:
            setS(main, "image", v or "")
    img.currentIndexChanged.connect(img_changed)
    f.addRow("Logo", img)
    # jingle
    jr = QHBoxLayout()
    jg = QComboBox()
    for n, name in NAMES.items():
        jg.addItem("%d · %s" % (n, name), str(n))
    jg.addItem("Mon fichier son…", "file")
    jg.addItem("Aucun son", "none")
    i = jg.findData(S(main, "jingle", "1"))
    jg.setCurrentIndex(max(0, i))

    def jg_changed(i):
        v = jg.itemData(i)
        if v == "file":
            path, _ = QFileDialog.getOpenFileName(d, "Son du POP mode", "", "Sons (*.wav *.mp3 *.ogg *.m4a *.flac)")
            if path and import_sound(main, path):
                setS(main, "jingle", "file")
            elif not (PRIV / "perso.wav").is_file():
                jg.setCurrentIndex(0)
                return
        setS(main, "jingle", jg.itemData(jg.currentIndex()))
    jg.currentIndexChanged.connect(jg_changed)
    listen = QPushButton("▶ Écouter")
    listen.clicked.connect(lambda: safe(lambda: ENGINE.player.play(jingle_path(main), vol.value())))
    jr.addWidget(jg, 1); jr.addWidget(listen)
    f.addRow("Jingle", jr)
    vol = QSlider(Qt.Horizontal)
    vol.setRange(0, 100)
    vol.setValue(int(S(main, "volume", "70") or 70))
    vol.valueChanged.connect(lambda v: setS(main, "volume", v))
    f.addRow("Volume du jingle", vol)
    test = QPushButton("Tester maintenant (logo + jingle sur tous les écrans)")
    test.clicked.connect(lambda: safe(ENGINE.fire))
    f.addRow(test)
    tabs.addTab(g, "Réglages")

    # --- phrases
    ph = QWidget()
    pv = QVBoxLayout(ph)
    info = QLabel("Phrases d'encouragement affichées par la séance à chaque phase : une par ligne, tirées au hasard. "
                  "Laisse vide pour les phrases par défaut.")
    info.setWordWrap(True); info.setStyleSheet("color:#8b8c99")
    pv.addWidget(info)
    area = QScrollArea(); area.setWidgetResizable(True)
    inner = QWidget(); il = QFormLayout(inner)
    edits = {}
    for key in ns.ORDER:
        word, color, _ic = ns.PHASES[key]
        lab = QLabel(word); lab.setStyleSheet("color:%s;font-weight:700;letter-spacing:1px" % color)
        e = QPlainTextEdit()
        e.setFixedHeight(74)
        e.setPlaceholderText("\n".join(ns.DEFAULT_PHRASES[key]))
        e.setPlainText(db.get_setting(main.conn, "pop_phr_" + key, "") or "")
        il.addRow(lab, e)
        edits[key] = e
    area.setWidget(inner)
    pv.addWidget(area, 1)
    reset = QPushButton("Rétablir les phrases par défaut")
    reset.clicked.connect(lambda: [e.setPlainText("") for e in edits.values()])
    pv.addWidget(reset)
    tabs.addTab(ph, "Phrases")

    row = QHBoxLayout(); row.addStretch(1)
    ok = QPushButton("Fermer"); ok.setDefault(True)
    row.addWidget(ok)
    lay.addLayout(row)

    def save():
        for key, e in edits.items():
            db.set_setting(main.conn, "pop_phr_" + key, e.toPlainText().strip())
        d.accept()
    ok.clicked.connect(save)
    d.rejected.connect(lambda: [db.set_setting(main.conn, "pop_phr_" + k, e.toPlainText().strip())
                                for k, e in edits.items()])
    d.exec()


def menu(main, m):
    a = m.addAction("POP mode…" + ("  (activé)" if is_on(main) else ""))
    a.triggered.connect(lambda: safe(open_dialog, main))


# ----------------------------------------------------------------------------------------
# Téléphone
# ----------------------------------------------------------------------------------------
PAGE_ROW = ('<div class="o-ry o-se o-pop"><span>POP mode</span>'
            '<button id="oPop" data-pop="1">Désactivé</button></div>')
PAGE_JS = r"""
// ---------- POP mode (onglet Musique)
function popRender(){
  const b = document.getElementById("oPop"); if (!b || !state) return;
  const pp = state.pop || {};
  b.classList.toggle("on", !!pp.on);
  b.textContent = pp.on ? (pp.next != null ? "Activé · prochain dans " + fmt(pp.next) : "Activé") : "Désactivé";
}
setInterval(popRender, 1000);
document.addEventListener("click", e => {
  const b = e.target.closest("[data-pop]"); if (!b || !state) return;
  const pp = state.pop || {};
  if (pp.on){ act("pop", 0, 0); toast("POP mode désactivé"); return; }
  if (pp.warn && !confirm(pp.warn)) return;
  act("pop", 0, 1, {ack: 1}); toast("POP mode activé");
});
"""


def patch_remote(main):
    try:
        from .. import webremote
        from ..webremote import WebRemote
    except Exception:
        return
    page = getattr(webremote, "PAGE", None)
    if isinstance(page, str) and 'id="oPop"' not in page:
        mark = '<button data-se="stop">Arrêter</button></div>'
        if mark in page:
            page = page.replace(mark, mark + "\n    " + PAGE_ROW, 1)
        j = page.rfind("</script>")
        if j >= 0:
            page = page[:j] + PAGE_JS + page[j:]
        i = page.rfind("</style>")
        if i >= 0:
            page = page[:i] + "#oPop.on{background:#ff3ec8;color:#fff;box-shadow:0 0 14px rgba(255,62,200,.6)}\n" + page[i:]
        webremote.PAGE = page
    if getattr(WebRemote, "_extras_pop", False):
        return
    orig_snap, orig_run = WebRemote.snapshot, WebRemote.run

    def snapshot(self):
        st = orig_snap(self)
        try:
            m = self.main
            on = is_on(m)
            nxt = ENGINE.next_in() if (ENGINE and on) else None
            st["pop"] = {"on": on, "next": None if nxt is None else int(nxt),
                         "warn": plain(warn_text()) if (not on and needs_warning(m)) else ""}
        except Exception as e:
            log("pop téléphone : %r" % e)
        return st

    def run(self, a):
        if a.get("cmd") == "pop":
            m = self.main
            want = bool(int(a.get("value") or 0))
            if want and needs_warning(m) and not a.get("ack"):
                return
            set_on(m, want, ask=False)
            return
        return orig_run(self, a)

    WebRemote.snapshot = snapshot
    WebRemote.run = run
    WebRemote._extras_pop = True


def install(main):
    global ENGINE
    ns.fonts()
    ENGINE = Engine(main)
    patch_remote(main)
