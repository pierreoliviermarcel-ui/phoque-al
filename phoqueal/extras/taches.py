"""Traitements en cours : petit panneau discret en bas à gauche de la fenêtre principale.

Rassemble l'avancement des travaux longs, sans rien changer à leur fonctionnement :
- création de PMV, amélioration d'image (IA), retrait de la musique ;
- tags automatiques, passages forts, scènes (analyses de fond, masquables) ;
- analyse de la bibliothèque, miniatures, tri automatique.

Le panneau est invisible quand rien ne tourne. Un clic dessus : détail, annulation, réglages.
Aucun nom de fichier n'est affiché (seulement dans la bulle d'aide, et jamais en mode discret).
"""
import os
import re
import time

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QVBoxLayout, QWidget

from .. import db
from .common import log, safe

ACCENT = "#5b8def"
MAX_ROWS = 3
STATE = {"thumb": None, "thumb_txt": "", "scan": None, "notice": None, "seen": {}, "counts": (0, {}), "pmv_seen": set()}


def tr(text):
    try:
        from . import i18n
        return i18n.tr(text)
    except Exception:
        return text


def S(main, key, default):
    return db.get_setting(main.conn, "jobs_" + key, default)


def setS(main, key, val):
    db.set_setting(main.conn, "jobs_" + key, val)


# ----------------------------------------------------------------------------------------
# Collecte : chaque source renvoie des dict(key, label, frac (0..1 ou None), extra, tip, bg, cancel)
# ----------------------------------------------------------------------------------------
def _counts(main):
    """Comptes de la bibliothèque, recalculés toutes les 10 s au plus (requêtes légères)."""
    t, c = STATE["counts"]
    if time.time() - t < 10:
        return c
    c = {}
    q = {"total": "SELECT COUNT(*) FROM videos WHERE missing=0 AND kind='video'",
         "hot": "SELECT COUNT(*) FROM hot_done",
         "scenes": "SELECT COUNT(*) FROM scenes_done",
         "enh_wait": "SELECT COUNT(*) FROM enhance WHERE status='attente'"}
    for k, sql in q.items():
        try:
            c[k] = main.conn.execute(sql).fetchone()[0]
        except Exception:
            c[k] = 0
    STATE["counts"] = (time.time(), c)
    return c


def _pmv(main):
    from . import pmvgen
    out = []
    for job in list(getattr(pmvgen, "RUNNING", [])):
        jid = id(job)
        if job.finished:
            pmvgen.RUNNING.remove(job)
            if jid in STATE["pmv_seen"]:
                STATE["pmv_seen"].discard(jid)
                if job.error:
                    STATE["notice"] = (time.time() + 10, tr("PMV : échec"), True)
                elif not job.cancel:
                    STATE["notice"] = (time.time() + 8, tr("PMV créé ✓"), False)
            continue
        STATE["pmv_seen"].add(jid)
        n = len(getattr(job, "jobs", [job]))
        extra = (tr("version %d / %d") % (job.cur + 1, n)) if n > 1 else ""
        out.append(dict(key="pmv%d" % jid, label=tr("Création du PMV"), frac=job.done / max(1, job.total),
                        extra=extra, tip="", bg=False, cancel=lambda j=job: setattr(j, "cancel", True)))
    return out


def _enhance(main):
    from . import ameliorer
    w = ameliorer.WORKER
    cur = w.current if w else None
    if not cur:
        return []
    wait = _counts(main).get("enh_wait", 0)
    extra = (tr("%d en attente") % wait) if wait else ""
    label = tr("Amélioration IA") + (" " + tr("(test 30 s)") if cur.get("test") else "")
    return [dict(key="enh%s" % cur.get("id"), label=label, frac=float(cur.get("progress") or 0), extra=extra,
                 tip=os.path.basename(cur.get("path") or ""), bg=False,
                 cancel=lambda i=cur.get("id"): setattr(w, "cancel_id", i))]


def _separation(main):
    from . import separation
    w = separation.WORKER
    cur = w.current if w else None
    if not cur:
        return []
    vid, f = cur
    with w.lock:
        n = len(w.queue)
    return [dict(key="sep%s" % vid, label=tr("Retrait de la musique"), frac=float(f or 0),
                 extra=(tr("%d en attente") % n) if n else "", tip=_title(main, vid),
                 bg=bool(w.background and not n), cancel=None)]


def _autotags(main):
    from . import autotags
    w = autotags.WORKER
    if w is None or not w.running() or not w.total:
        return []
    st = w.status or ""
    if st.startswith("en ") or st == "terminé":          # en pause / en attente : rien à montrer
        return []
    return [dict(key="tags", label=tr("Tags automatiques"), frac=w.done / max(1, w.total),
                 extra="%d / %d" % (w.done, w.total), tip="", bg=True, cancel=None)]


def _forts(main):
    from . import forts
    w = forts.WORKER
    if w is None or getattr(w, "current", None) is None:
        return []
    c = _counts(main)
    return [dict(key="hot", label=tr("Passages forts"), frac=c["hot"] / max(1, c["total"]),
                 extra="%d / %d" % (c["hot"], c["total"]), tip=_title(main, w.current), bg=not w.queue, cancel=None)]


def _scenes(main):
    from . import scenes
    w = scenes.WORKER
    if w is None or getattr(w, "current", None) is None:
        return []
    c = _counts(main)
    return [dict(key="scn", label=tr("Détection des scènes"), frac=c["scenes"] / max(1, c["total"]),
                 extra="%d / %d" % (c["scenes"], c["total"]), tip=_title(main, w.current), bg=not w.urgent,
                 cancel=None)]


def _core(main):
    out = []
    sw = getattr(main, "scan_worker", None)
    if sw is not None and sw.isRunning():
        out.append(dict(key="scan", label=tr("Analyse de la bibliothèque"), frac=None, extra="", tip="", bg=False,
                        cancel=None))
    tw = getattr(main, "thumb_worker", None)
    if tw is not None and tw is not STATE["thumb"]:
        STATE["thumb"], STATE["thumb_txt"] = tw, ""
        try:
            tw.progress.connect(lambda s: STATE.__setitem__("thumb_txt", s))
        except Exception:
            pass
    if tw is not None and tw.isRunning():
        m = re.search(r"(\d+)\s*/\s*(\d+)", STATE["thumb_txt"])
        a, b = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        out.append(dict(key="thumbs", label=tr("Miniatures"), frac=(a / b) if b else None,
                        extra=("%d / %d" % (a, b)) if b else "", tip="", bg=False, cancel=None))
    return out


def _tri(main):
    from . import tri
    if not tri.STATE.get("busy"):
        return []
    return [dict(key="tri", label=tr("Tri automatique"), frac=None, extra="", tip="", bg=False, cancel=None)]


def _definition(main):
    from . import definition
    w = definition.WORKER
    if w is None or w.current is None:
        return []
    t, c, total = STATE.get("def") or (0, None, 0)
    if c is None or time.time() - t > 10:
        c, total = definition.counts(main.conn)
        STATE["def"] = (time.time(), c, total)
    done = total - c["?"]
    return [dict(key="def", label=tr("Définition des vidéos"), frac=done / max(1, total),
                 extra="%d / %d" % (done, total), tip="", bg=True, cancel=None)]


SOURCES = [_pmv, _enhance, _separation, _core, _tri, _autotags, _forts, _scenes, _definition]


def _title(main, vid):
    try:
        r = main.conn.execute("SELECT title FROM videos WHERE id=?", (vid,)).fetchone()
        return r[0] if r else ""
    except Exception:
        return ""


def collect(main):
    items = []
    show_bg = S(main, "bg", "1") == "1"
    for src in SOURCES:
        try:
            for it in src(main):
                if show_bg or not it["bg"]:
                    items.append(it)
        except Exception:
            pass                                       # module absent ou pas encore démarré
    return items


def eta(it):
    """Temps restant estimé d'après la vitesse observée (None si pas encore fiable)."""
    f = it["frac"]
    if f is None:
        return None
    now = time.time()
    t0, f0 = STATE["seen"].setdefault(it["key"], (now, f))
    if f < f0:                                         # repartie de zéro : on recommence la mesure
        STATE["seen"][it["key"]] = (now, f)
        return None
    if now - t0 < 8 or f - f0 < 0.01 or f >= 1:
        return None
    return (now - t0) / (f - f0) * (1 - f)


def fmt_eta(s):
    if s is None or s > 48 * 3600:
        return ""
    if s < 60:
        return tr("≈ %d s") % max(5, round(s / 5) * 5)
    if s < 3600:
        return tr("≈ %d min") % round(s / 60)
    return tr("≈ %d h %02d") % (s // 3600, (s % 3600) // 60)


# ----------------------------------------------------------------------------------------
# Panneau
# ----------------------------------------------------------------------------------------
class Row(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setSpacing(8)
        self.name = QLabel()
        self.name.setObjectName("jobName")
        self.info = QLabel()
        self.info.setObjectName("jobInfo")
        h.addWidget(self.name, 1)
        h.addWidget(self.info)
        v.addLayout(h)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(3)
        self.bar.setRange(0, 1000)
        v.addWidget(self.bar)

    def show_item(self, it, discreet):
        self.name.setText(it["label"])
        bits = []
        if it["frac"] is not None:
            bits.append("%d %%" % int(it["frac"] * 100))
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(it["frac"] * 1000))
        else:
            self.bar.setRange(0, 0)                     # barre « en cours » sans pourcentage
        e = fmt_eta(eta(it))
        if e:
            bits.append(e)
        if it["extra"]:
            bits.append(it["extra"])
        self.info.setText("  ·  ".join(bits))
        self.setToolTip("" if discreet else it.get("tip") or "")


class Panel(QFrame):
    def __init__(self, main, host):
        super().__init__(host)
        self.main, self.host = main, host
        self.setObjectName("jobsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(300)
        self.setStyleSheet(
            "#jobsPanel { background: rgba(18,18,24,215); border: 1px solid rgba(255,255,255,18); border-radius: 8px; }"
            "#jobsPanel[hover=\"true\"] { background: rgba(26,26,34,240); border-color: rgba(91,141,239,90); }"
            "QLabel { background: transparent; }"
            "#jobName { color: #b4b5c2; font-size: 8.5pt; }"
            "#jobInfo, #jobMore { color: #7c7d8a; font-size: 8pt; }"
            "#jobNotice { color: #b4b5c2; font-size: 8.5pt; }"
            "QProgressBar { background: rgba(255,255,255,20); border: none; border-radius: 1px; }"
            "QProgressBar::chunk { background: %s; border-radius: 1px; }" % ACCENT)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(11, 8, 11, 8)
        self.lay.setSpacing(7)
        self.rows = [Row(self) for _ in range(MAX_ROWS)]
        for r in self.rows:
            self.lay.addWidget(r)
        self.more = QLabel()
        self.more.setObjectName("jobMore")
        self.notice = QLabel()
        self.notice.setObjectName("jobNotice")
        self.lay.addWidget(self.more)
        self.lay.addWidget(self.notice)
        self.items = []
        self.hide()
        host.installEventFilter(self)

    # placement : coin bas gauche de la zone des pages
    def place(self):
        self.adjustSize()
        self.move(10, max(0, self.host.height() - self.height() - 10))
        self.raise_()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            if self.isVisible():
                self.place()
        return False

    def enterEvent(self, e):
        self.setProperty("hover", True); self.style().unpolish(self); self.style().polish(self)

    def leaveEvent(self, e):
        self.setProperty("hover", False); self.style().unpolish(self); self.style().polish(self)

    def mousePressEvent(self, e):
        safe(self.open_menu)

    def refresh(self):
        main = self.main
        if S(main, "on", "1") != "1":
            self.hide()
            return
        items = collect(main)
        self.items = items
        live = {it["key"] for it in items}
        for k in [k for k in STATE["seen"] if k not in live]:
            STATE["seen"].pop(k, None)
        note = STATE["notice"]
        if note and note[0] < time.time():
            STATE["notice"] = note = None
        if not items and not note:
            self.hide()
            return
        discreet = bool(getattr(main, "discreet", False))
        for i, r in enumerate(self.rows):
            if i < len(items):
                r.show_item(items[i], discreet)
                r.show()
            else:
                r.hide()
        extra = len(items) - MAX_ROWS
        self.more.setText(tr("+ %d autre(s) traitement(s)") % extra if extra > 0 else "")
        self.more.setVisible(extra > 0)
        if note:
            self.notice.setText(note[1])
            self.notice.setStyleSheet("color: #ff6b78;" if note[2] else "")
        self.notice.setVisible(bool(note))
        if not self.isVisible():
            self.show()
        self.place()

    def open_menu(self):
        main = self.main
        m = QMenu(self)
        if not self.items:
            a = m.addAction(tr("Aucun traitement en cours"))
            a.setEnabled(False)
        for it in self.items:
            pct = ("  —  %d %%" % int(it["frac"] * 100)) if it["frac"] is not None else ""
            if it["cancel"]:
                m.addAction(tr("Annuler : %s") % it["label"] + pct).triggered.connect(lambda _=False, f=it["cancel"]: f())
            else:
                a = m.addAction(it["label"] + pct)
                a.setEnabled(False)
        m.addSeparator()
        a = m.addAction(tr("Afficher les analyses de fond (tags, scènes, passages forts…)"))
        a.setCheckable(True)
        a.setChecked(S(main, "bg", "1") == "1")
        a.triggered.connect(lambda on: (setS(main, "bg", "1" if on else "0"), self.refresh()))
        m.addAction(tr("Masquer ce panneau (⋯ → Préférences pour le remettre)")).triggered.connect(
            lambda: (setS(main, "on", "0"), self.refresh()))
        m.exec(self.mapToGlobal(QPoint(0, -m.sizeHint().height() - 4)))


PANEL = {"w": None}


def menu(main, m):
    """Entrée du sous-menu « Préférences »."""
    a = m.addAction("Panneau des traitements en cours (bas gauche)")
    a.setCheckable(True)
    a.setChecked(S(main, "on", "1") == "1")
    a.triggered.connect(lambda on: (setS(main, "on", "1" if on else "0"),
                                    PANEL["w"] and PANEL["w"].refresh()))


def install(main):
    host = getattr(main, "pages", None) or main.centralWidget()
    p = Panel(main, host)
    PANEL["w"] = p
    t = QTimer(p)
    t.setInterval(1000)
    t.timeout.connect(lambda: safe(p.refresh))
    t.start()
    main._jobs_panel = p
