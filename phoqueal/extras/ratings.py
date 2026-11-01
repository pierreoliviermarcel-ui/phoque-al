"""Etoiles : une note de 1 a 5 par video."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from .common import owner

ACTIONS = [("p_rate%d" % n, "player", "Noter %d étoile%s" % (n, "s" if n > 1 else ""), "Alt+%d" % n) for n in range(1, 6)]
ACTIONS.append(("p_rate0", "player", "Retirer la note", "Alt+0"))
STAR = "★"


def stars(n):
    return STAR * n + "☆" * (5 - n)


def get(conn, vid):
    if not vid or vid < 0:
        return 0
    r = conn.execute("SELECT rating FROM videos WHERE id=?", (vid,)).fetchone()
    return int(r["rating"] or 0) if r else 0


def set_rating(main, vid, n):
    if not vid or vid < 0:
        return False
    main.conn.execute("UPDATE videos SET rating=? WHERE id=?", (int(n), vid))
    main.conn.commit()
    refresh_cards(main, vid)
    return True


def rate_player(win, n):
    o = owner(win)
    main = win.ctrl.main
    if set_rating(main, o.video_id, n):
        o.osd(stars(n) if n else "Note retirée", 1500)
    else:
        o.osd("Note : pas disponible pour cette vidéo", 1500)


PLAYER_KEYS = {"p_rate%d" % n: (lambda w, n=n: rate_player(w, n)) for n in range(0, 6)}
MOSAIC_KEYS = dict(PLAYER_KEYS)


def refresh_cards(main, vid):
    row = main.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not row:
        return
    icon = main.icon_for(row)
    for item in main.items.get(vid, []):
        try:
            if item is getattr(main, "hover_item", None):
                main.hover_icon = icon
            else:
                item.setIcon(icon)
        except RuntimeError:
            pass


def install(main):
    from ..ui import MainWindow, CARD
    if getattr(MainWindow, "_extras_rating", False):
        return
    orig = MainWindow.icon_for

    def icon_for(self, row):
        icon = orig(self, row)
        try:
            n = int(row["rating"] or 0) if "rating" in row.keys() else 0
        except (IndexError, KeyError, TypeError, ValueError):
            n = 0
        if n <= 0 or self.discreet:
            return icon
        pm = icon.pixmap(CARD)
        if pm.isNull():
            return icon
        pm = QPixmap(pm)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        w = 16 * n + 12
        p.drawRoundedRect(QRectF(6, pm.height() - 28, w, 22), 6, 6)
        p.setPen(QColor("#ffcc33"))
        p.setFont(QFont("Segoe UI Symbol", 11))
        p.drawText(QRectF(12, pm.height() - 28, w, 22), Qt.AlignLeft | Qt.AlignVCenter, STAR * n)
        p.end()
        return QIcon(pm)

    MainWindow.icon_for = icon_for
    MainWindow._extras_rating = True


def home_rows(main):
    rows = main.conn.execute(
        "SELECT * FROM videos WHERE missing=0 AND kind='video' AND rating>=4 "
        "ORDER BY rating DESC, RANDOM() LIMIT 40").fetchall()
    return [{"title": "★ Les mieux notées  (%d)" % len(rows), "rows": rows}] if rows else []


def card_menu(main, menu, vids):
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if not vids:
        return {}
    cur = get(main.conn, vids[0]) if len(vids) == 1 else -1
    sub = menu.addMenu("★  Noter")
    acts = {}
    for n in range(5, 0, -1):
        a = sub.addAction(stars(n))
        a.setCheckable(True)
        a.setChecked(cur == n)
        acts[a] = (lambda n=n: [set_rating(main, v, n) for v in vids])
    sub.addSeparator()
    acts[sub.addAction("Retirer la note")] = (lambda: [set_rating(main, v, 0) for v in vids])
    return acts
