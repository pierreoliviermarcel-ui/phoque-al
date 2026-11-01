"""Statistiques de visionnage (temps reellement regarde, compte toutes les 10 s) et de la bibliotheque."""
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .common import log

TICK = 10


def record(main):
    try:
        day = time.strftime("%Y-%m-%d")
        rows = []
        for w in list(main.ctrl.players.values()):
            if getattr(w, "is_remote", False):
                continue
            tiles = w.tiles if getattr(w, "is_mosaic", False) else [w]
            for t in tiles:
                vid = getattr(t, "video_id", None)
                if vid and vid > 0 and t.is_playing():
                    rows.append((vid, day))
        if rows:
            main.conn.executemany("INSERT OR IGNORE INTO watch(video_id, day, seconds) VALUES (?,?,0)", rows)
            main.conn.executemany("UPDATE watch SET seconds = seconds + %d WHERE video_id=? AND day=?" % TICK, rows)
            main.conn.commit()
    except Exception as e:
        log("stats : %r" % e)


def install(main):
    main._stats_timer = QTimer(main)
    main._stats_timer.setInterval(TICK * 1000)
    main._stats_timer.timeout.connect(lambda: record(main))
    main._stats_timer.start()


# ----------------------------------------------------------------------------------------
# Calculs
# ----------------------------------------------------------------------------------------
def hours(sec):
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m = r // 60
    return "%d h %02d" % (h, m) if h else "%d min" % m


def compute(conn, discreet=False):
    q = lambda sql, *a: conn.execute(sql, a).fetchone()
    today = time.strftime("%Y-%m-%d")
    d7 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
    d30 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 29 * 86400))
    s = {
        "today": q("SELECT SUM(seconds) AS s FROM watch WHERE day=?", today)["s"] or 0,
        "week": q("SELECT SUM(seconds) AS s FROM watch WHERE day>=?", d7)["s"] or 0,
        "month": q("SELECT SUM(seconds) AS s FROM watch WHERE day>=?", d30)["s"] or 0,
        "all": q("SELECT SUM(seconds) AS s FROM watch")["s"] or 0,
        "since": q("SELECT MIN(day) AS d FROM watch")["d"],
    }
    days = []
    for k in range(29, -1, -1):
        d = time.strftime("%Y-%m-%d", time.localtime(time.time() - k * 86400))
        days.append((d, q("SELECT SUM(seconds) AS s FROM watch WHERE day=?", d)["s"] or 0))
    s["days"] = days
    lib = q("SELECT COUNT(*) AS n, SUM(duration) AS d, SUM(size) AS sz, SUM(play_count=0) AS never, "
            "SUM(liked) AS liked FROM videos WHERE missing=0 AND kind='video'")
    s["lib"] = dict(lib)
    s["photos"] = q("SELECT COUNT(*) AS n FROM videos WHERE missing=0 AND kind='photo'")["n"]
    s["moments"] = q("SELECT COUNT(*) AS n FROM moments")["n"]
    s["ratings"] = [q("SELECT COUNT(*) AS n FROM videos WHERE missing=0 AND rating=?", k)["n"] for k in range(1, 6)]
    name = lambda i, v: ("n°%d" % i) if discreet else v
    s["themes"] = [(name(i, r["category"]), r["s"]) for i, r in enumerate(conn.execute(
        "SELECT v.category, SUM(w.seconds) AS s FROM watch w JOIN videos v ON v.id=w.video_id "
        "GROUP BY v.category ORDER BY s DESC LIMIT 8"), 1)]
    s["videos"] = [(name(i, r["title"]), r["s"]) for i, r in enumerate(conn.execute(
        "SELECT v.title, SUM(w.seconds) AS s FROM watch w JOIN videos v ON v.id=w.video_id "
        "GROUP BY v.id ORDER BY s DESC LIMIT 8"), 1)]
    s["launched"] = [(name(i, r["title"]), r["play_count"]) for i, r in enumerate(conn.execute(
        "SELECT title, play_count FROM videos WHERE missing=0 AND play_count>0 ORDER BY play_count DESC LIMIT 8"), 1)]
    try:
        s["people"] = [(name(i, r["name"]), r["s"]) for i, r in enumerate(conn.execute(
            "SELECT p.name, SUM(w.seconds) AS s FROM watch w JOIN video_people vp ON vp.video_id=w.video_id "
            "AND vp.manual>=0 JOIN people p ON p.id=vp.person_id GROUP BY p.id ORDER BY s DESC LIMIT 8"), 1)]
    except Exception:
        s["people"] = []
    s["unwatched_themes"] = [(name(i, r["category"]), r["n"]) for i, r in enumerate(conn.execute(
        "SELECT category, COUNT(*) AS n FROM videos WHERE missing=0 AND kind='video' AND play_count=0 "
        "GROUP BY category ORDER BY n DESC LIMIT 8"), 1)]
    return s


# ----------------------------------------------------------------------------------------
# Affichage
# ----------------------------------------------------------------------------------------
class Bars(QWidget):
    """Histogramme des 30 derniers jours."""

    def __init__(self, days):
        super().__init__()
        self.days = days
        self.setMinimumHeight(170)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top = max([v for _, v in self.days] + [60])
        n = len(self.days)
        bw = (w - 20) / n
        p.setFont(QFont("Segoe UI", 8))
        for i, (d, v) in enumerate(self.days):
            bh = (h - 34) * v / top
            x = 10 + i * bw
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#5b8def") if i == n - 1 else QColor("#3b5bb5"))
            p.drawRoundedRect(QRectF(x + 1, h - 20 - bh, bw - 2, bh), 2, 2)
            if i % 5 == 4 or i == n - 1:
                p.setPen(QColor("#8a8a96"))
                p.drawText(QRectF(x - 14, h - 18, bw + 28, 16), Qt.AlignCenter, d[8:10] + "/" + d[5:7])
        p.setPen(QColor("#8a8a96"))
        p.drawText(QRectF(10, 0, w, 14), Qt.AlignLeft, "max : " + hours(top))
        p.end()


def ranked(title, items, fmt=hours):
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    head = QLabel("<b>%s</b>" % title)
    lay.addWidget(head)
    if not items:
        lab = QLabel("—")
        lab.setObjectName("status")
        lay.addWidget(lab)
    top = max([v for _, v in items] + [1])
    for name, v in items:
        row = QLabel()
        width = int(160 * v / top)
        row.setText("<table width='100%%'><tr><td>%s</td><td align='right'>%s</td></tr></table>"
                    "<div style='background:#3b5bb5;height:4px;width:%dpx'></div>"
                    % (str(name)[:48].replace("<", "&lt;"), fmt(v), max(2, width)))
        lay.addWidget(row)
    lay.addStretch(1)
    return box


def card(value, label):
    w = QLabel("<div style='font-size:20pt;font-weight:700'>%s</div><div style='color:#8a8a96'>%s</div>" % (value, label))
    w.setStyleSheet("background:#17181f;border:1px solid #2a2b36;border-radius:10px;padding:10px 14px;")
    return w


def StatsDialog(main):
    from ..dupes import human
    s = compute(main.conn, main.discreet)
    dlg = QDialog(main)
    dlg.setWindowTitle("Statistiques")
    dlg.resize(980, 720)
    outer = QVBoxLayout(dlg)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    lay = QVBoxLayout(body)
    scroll.setWidget(body)
    outer.addWidget(scroll, 1)

    since = s["since"]
    lay.addWidget(QLabel("<b>Temps de visionnage</b>  <span style='color:#8a8a96'>(compté depuis le %s)</span>"
                         % ("%s/%s/%s" % (since[8:], since[5:7], since[:4]) if since else "lancement de cette fonction")))
    row = QHBoxLayout()
    for v, lab in ((s["today"], "aujourd'hui"), (s["week"], "7 derniers jours"),
                   (s["month"], "30 derniers jours"), (s["all"], "au total")):
        row.addWidget(card(hours(v), lab))
    lay.addLayout(row)
    lay.addWidget(Bars(s["days"]))

    lib = s["lib"]
    n = lib["n"] or 0
    never = lib["never"] or 0
    lay.addWidget(QLabel("<b>Bibliothèque</b>"))
    row = QHBoxLayout()
    row.addWidget(card(str(n), "vidéos"))
    row.addWidget(card(hours(lib["d"] or 0), "de vidéos au total"))
    row.addWidget(card(human(lib["sz"] or 0), "sur le disque"))
    row.addWidget(card("%d %%" % (100 * never / n if n else 0), "jamais regardées (%d)" % never))
    lay.addLayout(row)
    row = QHBoxLayout()
    row.addWidget(card("♥ %d" % (lib["liked"] or 0), "vidéos aimées"))
    row.addWidget(card("⚑ %d" % s["moments"], "moments favoris"))
    row.addWidget(card(" ".join("%d★:%d" % (k, c) for k, c in zip(range(5, 0, -1), reversed(s["ratings"]))) or "—",
                       "répartition des notes"))
    row.addWidget(card(str(s["photos"]), "photos"))
    lay.addLayout(row)

    grid = QGridLayout()
    grid.addWidget(ranked("Thèmes les plus regardés", s["themes"]), 0, 0)
    grid.addWidget(ranked("Vidéos les plus regardées", s["videos"]), 0, 1)
    grid.addWidget(ranked("Personnes les plus regardées", s["people"]), 1, 0)
    grid.addWidget(ranked("Les plus lancées", s["launched"], fmt=lambda v: "%d fois" % v), 1, 1)
    grid.addWidget(ranked("Thèmes à découvrir (vidéos jamais vues)", s["unwatched_themes"],
                          fmt=lambda v: "%d vidéos" % v), 2, 0)
    lay.addLayout(grid)
    lay.addStretch(1)
    close = QPushButton("Fermer")
    close.clicked.connect(dlg.accept)
    outer.addWidget(close)
    return dlg
