"""Moments favoris : marquer l'instant en cours d'une video (touche K), les retrouver, les enchainer
(« bobine »), et les servir au montage au rythme."""
import random
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QPushButton

from .. import db
from ..config import DATA_DIR
from .common import current_time, log, owner, play_at

MOMENT_DIR = DATA_DIR / "moments"
ACTIONS = [
    ("p_moment", "player", "Moment favori : marquer l'instant en cours", "K"),
    ("p_moment_next", "player", "Aller au moment favori suivant de cette vidéo", "J"),
]


def fmt(t):
    t = int(max(0, t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


# ----------------------------------------------------------------------------------------
# Donnees
# ----------------------------------------------------------------------------------------
def add(conn, vid, t):
    near = conn.execute("SELECT id FROM moments WHERE video_id=? AND ABS(t-?)<3", (vid, t)).fetchone()
    if near:
        return near["id"], False
    cur = conn.execute("INSERT INTO moments(video_id, t, created_at) VALUES (?,?,?)", (vid, t, time.time()))
    conn.commit()
    return cur.lastrowid, True


def delete(conn, mid):
    r = conn.execute("SELECT thumb FROM moments WHERE id=?", (mid,)).fetchone()
    conn.execute("DELETE FROM moments WHERE id=?", (mid,))
    conn.commit()
    if r and r["thumb"]:
        try:
            import os
            os.remove(r["thumb"])
        except OSError:
            pass


def all_moments(conn, limit=None):
    sql = ("SELECT m.id AS mid, m.t, m.thumb AS mthumb, v.* FROM moments m JOIN videos v ON v.id = m.video_id "
           "WHERE v.missing=0 ORDER BY m.created_at DESC")
    return conn.execute(sql + (" LIMIT %d" % limit if limit else "")).fetchall()


def video_moments(conn, vid):
    return [r["t"] for r in conn.execute("SELECT t FROM moments WHERE video_id=? ORDER BY t", (vid,))]


# ----------------------------------------------------------------------------------------
# Touches dans le lecteur
# ----------------------------------------------------------------------------------------
def mark(win):
    main = win.ctrl.main
    vid, t = current_time(win)
    o = owner(win)
    if not vid or vid < 0:
        o.osd("Moment : pas disponible pour cette vidéo", 1500)
        return
    mid, new = add(main.conn, vid, t)
    if not new:
        o.osd("⚑ Moment déjà marqué (%s)" % fmt(t), 1500)
        return
    MOMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = str(MOMENT_DIR / ("%d.jpg" % mid))
    try:
        if o.player.video_take_snapshot(0, path, 480, 0) == 0:
            main.conn.execute("UPDATE moments SET thumb=? WHERE id=?", (path, mid))
            main.conn.commit()
    except Exception as e:
        log("capture moment : %r" % e)
    n = main.conn.execute("SELECT COUNT(*) AS n FROM moments WHERE video_id=?", (vid,)).fetchone()["n"]
    o.osd("⚑ Moment marqué à %s  (%d dans cette vidéo)" % (fmt(t), n), 1800)


def next_moment(win):
    main = win.ctrl.main
    vid, t = current_time(win)
    o = owner(win)
    times = video_moments(main.conn, vid) if vid else []
    if not times:
        o.osd("Aucun moment marqué dans cette vidéo (touche K)", 1800)
        return
    nxt = [x for x in times if x > t + 2]
    target = nxt[0] if nxt else times[0]
    o.player.set_time(int(max(0, target - 1) * 1000))
    o.osd("⚑ Moment %d/%d  (%s)" % (times.index(target) + 1, len(times), fmt(target)), 1500)


PLAYER_KEYS = {"p_moment": mark, "p_moment_next": next_moment}
MOSAIC_KEYS = dict(PLAYER_KEYS)


# ----------------------------------------------------------------------------------------
# Bobine : les moments enchaines sur un ecran
# ----------------------------------------------------------------------------------------
class Reel(QObject):
    def __init__(self, main, items, screen, seconds):
        super().__init__(main)
        self.main = main
        self.items = items              # [(video_id, t)]
        self.screen = screen
        self.seconds = seconds
        self.i = -1
        self.current = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)

    def start(self):
        self.step()

    def stop(self):
        self.timer.stop()
        if getattr(self.main, "_reel", None) is self:
            self.main._reel = None

    def still_mine(self):
        p = self.main.ctrl.players.get(self.screen)
        return p is not None and self.current is not None and getattr(p, "video_id", None) == self.current

    def step(self):
        if self.i >= 0 and not self.still_mine():
            return self.stop()                                   # l'utilisateur a change de video
        for _ in range(len(self.items)):
            self.i = (self.i + 1) % len(self.items)
            vid, t = self.items[self.i]
            if play_at(self.main, vid, max(0, t - 2), choice=self.screen):
                self.current = vid
                p = self.main.ctrl.players.get(self.screen)
                if p is not None:
                    self.screen = p.number
                    QTimer.singleShot(1200, lambda p=p, i=self.i: p.osd(
                        "⚑ Bobine %d/%d" % (i + 1, len(self.items)), 1500))
                self.timer.start(int(self.seconds * 1000))
                return
        self.stop()


def play_reel(main, shuffle=True):
    items = [(r["id"], r["t"]) for r in all_moments(main.conn)]
    if not items:
        main.status.setText("Aucun moment favori : touche K pendant une vidéo pour en marquer.")
        return
    if shuffle:
        random.shuffle(items)
    screen = main.ask_screen()
    if screen == -1:
        return
    if not screen:
        screen = main.ctrl.pick_screen(0)
    old = getattr(main, "_reel", None)
    if old is not None:
        old.stop()
    secs = float(db.get_setting(main.conn, "reel_seconds", "20"))
    main._reel = Reel(main, items, screen, secs)
    main._reel.start()


# ----------------------------------------------------------------------------------------
# Accueil
# ----------------------------------------------------------------------------------------
def home_rows(main):
    rows = all_moments(main.conn, 40)
    if not rows:
        return []
    items = []
    for k, r in enumerate(rows, 1):
        d = dict(r)
        d["title"] = (" " if main.discreet else "%s  ·  %s" % (fmt(r["t"]), r["title"]))
        d["thumb"] = r["mthumb"] or r["thumb"]
        d["kind"] = "photo"                                         # pas d'apercu anime au survol
        items.append(d)
    times = [(r["id"], r["t"]) for r in rows]
    total = main.conn.execute("SELECT COUNT(*) AS n FROM moments").fetchone()["n"]
    return [{"title": "⚑ Mes moments  (%d)   —   cliquer ici pour la bobine" % total, "rows": items,
             "activate": lambda i: play_at(main, times[i][0], max(0, times[i][1] - 2)),
             "title_click": lambda: play_reel(main),
             "tooltip": "Enchaîne tous tes moments favoris (durée réglable dans Plus → Mes moments)"}]


# ----------------------------------------------------------------------------------------
# Montage au rythme : piocher dans les moments
# ----------------------------------------------------------------------------------------
def install(main):
    from ..montage import Montage
    if getattr(Montage, "_extras_moments", False):
        return
    orig = Montage.pick_next

    def pick_next(self, pw):
        try:
            if db.get_setting(self.conn, "montage_moments", "0") == "1":
                rows = self.conn.execute("SELECT m.video_id, m.t FROM moments m JOIN videos v ON v.id=m.video_id "
                                         "WHERE v.missing=0 ORDER BY RANDOM() LIMIT 8").fetchall()
                showing = {getattr(p, "video_id", None) for p in self.players()}
                for r in rows:
                    if r["video_id"] in showing and len(rows) > 1:
                        continue
                    row, path = self.ctrl.resolve(r["video_id"])
                    if path:
                        return row, path, max(0.0, r["t"] - 1)
        except Exception as e:
            log("montage moments : %r" % e)
        return orig(self, pw)

    Montage.pick_next = pick_next
    Montage._extras_moments = True


# ----------------------------------------------------------------------------------------
# Fenetre « Mes moments »
# ----------------------------------------------------------------------------------------
def MomentsDialog(main):
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                   QVBoxLayout, QCheckBox)
    dlg = QDialog(main)
    dlg.setWindowTitle("Mes moments favoris")
    dlg.resize(820, 600)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("Pendant une vidéo : <b>K</b> marque l'instant, <b>J</b> saute au moment suivant de la vidéo. "
                         "Double-clic pour revoir un moment."))
    lst = QListWidget()
    lst.setIconSize(QSize(160, 90))
    lay.addWidget(lst, 1)

    def fill():
        lst.clear()
        for r in all_moments(main.conn):
            it = QListWidgetItem("%s   ·   %s" % (fmt(r["t"]), "Vidéo" if main.discreet else r["title"]))
            th = r["mthumb"] or r["thumb"]
            if th and not main.discreet:
                it.setIcon(QIcon(th))
            it.setData(Qt.UserRole, (r["mid"], r["id"], r["t"]))
            lst.addItem(it)

    lst.itemActivated.connect(lambda it: play_at(main, it.data(Qt.UserRole)[1], max(0, it.data(Qt.UserRole)[2] - 2)))
    row = QHBoxLayout()
    reel = QPushButton("▶  Bobine (tous les moments enchaînés)")
    reel.setObjectName("heroPrimary")
    reel.clicked.connect(lambda: (dlg.accept(), play_reel(main)))
    secs = QDoubleSpinBox()
    secs.setRange(5, 300)
    secs.setDecimals(0)
    secs.setSuffix(" s par moment")
    secs.setValue(float(db.get_setting(main.conn, "reel_seconds", "20")))
    secs.valueChanged.connect(lambda v: db.set_setting(main.conn, "reel_seconds", str(int(v))))
    dele = QPushButton("Supprimer")

    def do_delete():
        for it in lst.selectedItems():
            delete(main.conn, it.data(Qt.UserRole)[0])
        fill()

    dele.clicked.connect(lambda: do_delete())
    mont = QCheckBox("Le montage au rythme pioche dans mes moments")
    mont.setChecked(db.get_setting(main.conn, "montage_moments", "0") == "1")
    mont.toggled.connect(lambda on: db.set_setting(main.conn, "montage_moments", "1" if on else "0"))
    close = QPushButton("Fermer")
    close.clicked.connect(dlg.accept)
    for w in (reel, secs, dele):
        row.addWidget(w)
    row.addStretch(1)
    row.addWidget(close)
    lay.addWidget(mont)
    lay.addLayout(row)
    fill()
    return dlg
