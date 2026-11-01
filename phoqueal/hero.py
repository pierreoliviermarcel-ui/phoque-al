"""Grande banniere animee de l'accueil.

- 6 diapositives : a reprendre, theme a decouvrir, theme favori, jamais vue, a revoir, nouveaute
- video en fond (muette) tiree de la vraie video, sinon image HD avec zoom lent (Ken Burns)
- fondu enchaine, progression du defilement, pause au survol
- bande de vignettes du theme, bouton "sur tous les ecrans"
"""
import os
import subprocess
import time

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from . import db, volumes
from .config import DATA_DIR, find_tool
from .thumbs import _FLAGS

try:
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    HAS_MM = True
except ImportError:
    HAS_MM = False

HERO_DIR = DATA_DIR / "hero"
HERO_HEIGHT = 470
SLIDE_MS = 12000
FADE_MS = 700
VIDEO_FADE_MS = 900
CARD_W, CARD_H = 150, 84
ACCENT = QColor("#5b8def")


# ----------------------------------------------------------------------------
# Contenu des diapositives
# ----------------------------------------------------------------------------
def fmt_duration(sec):
    if not sec:
        return ""
    m = int(sec // 60)
    return "%d h %02d min" % divmod(m, 60) if m >= 60 else "%d min" % max(1, m)


def video_info(v):
    parts = []
    if v["duration"]:
        parts.append(fmt_duration(v["duration"]))
    parts.append("Jamais vue" if not v["play_count"] else "Vue %d fois" % v["play_count"])
    if v["added_at"]:
        parts.append("ajoutée le " + time.strftime("%d/%m/%Y", time.localtime(v["added_at"])))
    return "   ·   ".join(parts)


def random_slide(conn, exclude=()):
    """Diapositive « Au hasard » : une video tiree au sort dans toute la bibliotheque."""
    ex = [int(x) for x in exclude if x is not None]
    v = conn.execute(
        "SELECT * FROM videos WHERE missing=0 AND kind='video'%s ORDER BY (thumb IS NULL), RANDOM() LIMIT 1"
        % ((" AND id NOT IN (%s)" % ",".join(map(str, ex))) if ex else "")).fetchone()
    if v is None:
        return None
    s = {"kind": "random", "tag": "\U0001F3B2  Au hasard", "video": v, "category": v["category"],
         "heading": v["title"], "progress": None}
    s["count"] = db.category_count(conn, v["category"])
    s["strip"] = conn.execute(
        "SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? AND id<>? "
        "ORDER BY (thumb IS NULL), RANDOM() LIMIT 8", (v["category"], v["id"])).fetchall()
    return s


def build_slides(conn):
    mode = db.get_setting(conn, "hero_mode", "mix")
    if mode == "random":
        r = random_slide(conn)
        return [r] if r else []
    used = set()
    slides = []
    r = random_slide(conn)
    if r:
        slides.append(r)
        used.add(r["video"]["id"])

    def pick(sql, args=()):
        for v in conn.execute(sql, args).fetchall():
            if v["id"] not in used:
                used.add(v["id"])
                return v
        return None

    def add(kind, tag, v, heading=None, progress=None):
        if not v:
            return
        slides.append({"kind": kind, "tag": tag, "video": v, "category": v["category"],
                       "heading": heading or v["title"], "progress": progress})

    v = pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND position > 30 "
             "AND (duration IS NULL OR position < duration * 0.95) "
             "ORDER BY last_played DESC LIMIT 3")
    if v:
        add("resume", "Reprendre", v, progress=(v["position"] / v["duration"]) if v["duration"] else None)

    liked = [v for v in db.liked_videos(conn, 40) if v["kind"] == "video"]
    if liked:
        import random as _r
        star = _r.choice(liked)
        used.add(star["id"])
        slides.append({"kind": "likes", "tag": "\u2665  Vos coups de cœur", "video": star,
                       "category": star["category"], "heading": star["title"], "progress": None,
                       "playlist": db.likes_playlist(conn), "count": len(db.liked_videos(conn)),
                       "strip": [v for v in liked if v["id"] != star["id"]][:8]})

    fav, chosen = db.favorite_category(conn)
    rnd = db.random_category(conn, exclude=fav)
    if rnd:
        add("theme", "Thème à découvrir",
            pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? ORDER BY (thumb IS NULL), RANDOM() LIMIT 5", (rnd,)),
            heading=rnd)
    if fav:
        add("theme", "Thème favori" if chosen else "Thème le plus regardé",
            pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? ORDER BY (thumb IS NULL), RANDOM() LIMIT 5", (fav,)),
            heading=fav)
    add("video", "Jamais vue",
        pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND play_count=0 ORDER BY (thumb IS NULL), RANDOM() LIMIT 5"))
    add("video", "À revoir",
        pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND play_count>0 AND last_played < ? "
             "ORDER BY RANDOM() LIMIT 5", (time.time() - 30 * 86400,)))
    span = conn.execute("SELECT MIN(added_at) AS a, MAX(added_at) AS b FROM videos WHERE missing=0 AND kind='video'").fetchone()
    if span["a"] and span["b"] and span["b"] - span["a"] > 3600:
        add("video", "Nouveauté",
            pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND added_at >= ? ORDER BY RANDOM() LIMIT 5",
                 (span["b"] - 7 * 86400,)))
    if len(slides) < 3:
        other = db.random_category(conn, exclude=rnd)
        if other:
            add("theme", "À découvrir aussi",
                pick("SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? ORDER BY RANDOM() LIMIT 5", (other,)),
                heading=other)

    for s in slides[:7]:
        if "strip" in s:
            continue
        s["count"] = db.category_count(conn, s["category"])
        s["strip"] = conn.execute(
            "SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? AND id<>? "
            "ORDER BY (thumb IS NULL), RANDOM() LIMIT 8", (s["category"], s["video"]["id"])).fetchall()
    return slides[:7]


def video_path(conn, vid, drives=None):
    row = conn.execute(
        "SELECT v.rel_path, s.serial, s.rel_root FROM videos v JOIN sources s ON s.id = v.source_id "
        "WHERE v.id=?", (vid,)).fetchone()
    if not row:
        return None
    root = volumes.resolve(row["serial"], row["rel_root"], drives)
    if not root:
        return None
    path = os.path.join(root, row["rel_path"])
    return path if os.path.isfile(path) else None


# ----------------------------------------------------------------------------
# Images HD des videos en vedette
# ----------------------------------------------------------------------------
class HeroWorker(QThread):
    ready = Signal(int, str)

    def __init__(self, video_ids):
        super().__init__()
        self.video_ids = video_ids

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return
        HERO_DIR.mkdir(parents=True, exist_ok=True)
        conn = db.connect()
        drives = volumes.connected_drives()
        for vid in self.video_ids:
            if self.isInterruptionRequested():
                break
            out = HERO_DIR / ("%d.jpg" % vid)
            if not out.is_file():
                row = conn.execute("SELECT duration FROM videos WHERE id=?", (vid,)).fetchone()
                path = video_path(conn, vid, drives)
                if not row or not path:
                    continue
                at = (row["duration"] or 60) * 0.3
                try:
                    subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % at, "-i", path,
                         "-frames:v", "1", "-vf", "scale=1920:-2", "-q:v", "3", "-y", str(out)],
                        capture_output=True, timeout=120, creationflags=_FLAGS)
                except Exception:
                    continue
            if out.is_file() and out.stat().st_size > 0:
                self.ready.emit(vid, str(out))
        conn.close()


# ----------------------------------------------------------------------------
# La banniere
# ----------------------------------------------------------------------------
class HeroBanner(QWidget):
    def __init__(self, main, slides):
        super().__init__()
        self.main = main
        self.slides = slides
        self.index = 0
        self.elapsed = 0
        self.hovered = False
        self.fade_from = None
        self.fade_t0 = 0
        self.frame = None
        self.video_t0 = None
        self._seek_pending = False
        self.cards = []            # [(QRectF, video_row)]
        self.hover_card = None
        self._pix = {}             # cache des QPixmap
        self.clock = QElapsedTimer()
        self.clock.start()
        self.last_tick = self.clock.elapsed()
        self.apply_height()
        self.setMouseTracking(True)

        try:
            self.slide_ms = max(3, int(float(db.get_setting(self.main.conn, "hero_seconds", "10")))) * 1000
        except ValueError:
            self.slide_ms = 10000
        for s in slides:
            self.init_image(s)

        self.play_btn = QPushButton(self)
        self.play_btn.setObjectName("heroPrimary")
        self.play_btn.clicked.connect(lambda: self.play_current())
        n = len(self.main.ctrl.screens())
        self.multi_btn = QPushButton("\u25B6\u25B6  Sur les %d écrans" % n, self)
        self.multi_btn.setObjectName("heroSecondary")
        self.multi_btn.clicked.connect(lambda: self.play_multi())
        self.multi_btn.setVisible(n > 1)
        self.see_btn = QPushButton("Voir le thème", self)
        self.see_btn.setObjectName("heroSecondary")
        self.see_btn.clicked.connect(lambda: self.see_current())
        self.prev_btn = QPushButton("\u2039", self)
        self.next_btn = QPushButton("\u203A", self)
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("heroArrow")
            b.setFixedSize(44, 64)
        self.prev_btn.clicked.connect(lambda: self.go(-1))
        self.next_btn.clicked.connect(lambda: self.go(1))
        for b in (self.play_btn, self.multi_btn, self.see_btn, self.prev_btn, self.next_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        many = len(slides) > 1 or (slides and slides[0]["kind"] == "random")
        self.prev_btn.setVisible(many)
        self.next_btn.setVisible(many)

        self.player = None
        if HAS_MM:
            self.player = QMediaPlayer(self)
            self.sink = QVideoSink(self)
            self.player.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self.on_frame)
            self.player.durationChanged.connect(self.on_duration)

        self.tick = QTimer(self)
        self.tick.setInterval(33)
        self.tick.timeout.connect(self.on_tick)
        self.enter_slide(0, fade=False)

    # --- taille : proportion de la hauteur visible de l'accueil -------------------------
    SIZES = [("fixed", "Compacte (470 px)"), ("0.55", "Normale (55 % de la fenêtre)"),
             ("0.72", "Grande (72 % de la fenêtre)"), ("0.88", "Très grande (88 % de la fenêtre)"),
             ("1.0", "Toute la fenêtre")]

    def apply_height(self):
        choice = db.get_setting(self.main.conn, "hero_size", "0.72")
        h = HERO_HEIGHT
        if choice != "fixed":
            try:
                vp = self.main.home_scroll.viewport().height()
                h = max(HERO_HEIGHT, int(vp * float(choice)) - 8)
            except (AttributeError, ValueError):
                pass
        if h != self.height() or self.minimumHeight() != h:
            self.setFixedHeight(h)

    # --- navigation ---------------------------------------------------------
    def current(self):
        return self.slides[self.index]

    def init_image(self, s):
        v = s["video"]
        s["image"] = v["thumb"] if v["thumb"] and os.path.isfile(v["thumb"]) else None
        hd = HERO_DIR / ("%d.jpg" % v["id"])
        if hd.is_file():
            s["image"] = str(hd)

    def advances(self):
        return len(self.slides) > 1 or (self.slides and self.slides[0]["kind"] == "random")

    def reroll(self, i):
        """Nouvelle video au hasard pour la diapositive i (a chaque passage)."""
        old = self.slides[i]
        new = random_slide(self.main.conn, exclude=[old["video"]["id"]])
        if not new:
            return
        self.init_image(new)
        self.slides[i] = new
        if not new["image"] or "hero" not in str(new["image"]):
            w = HeroWorker([new["video"]["id"]])
            w.ready.connect(self.set_image)
            self.main.hero_workers.append(w)
            w.start()

    def go(self, step):
        self.enter_slide((self.index + step) % len(self.slides))

    def enter_slide(self, i, fade=True):
        if fade and self.isVisible() and self.width() > 0:
            self.fade_from = self.grab()
            self.fade_t0 = self.clock.elapsed()
        if fade and self.slides[i]["kind"] == "random":
            self.reroll(i)
        self.index = i
        self.elapsed = 0
        self.frame = None
        self.video_t0 = None
        self.play_btn.setText("\u25B6  Reprendre" if self.current()["kind"] == "resume" else "\u25B6  Lire")
        self.see_btn.setText("Voir la playlist" if self.current().get("playlist") else "Voir le thème")
        self.see_btn.adjustSize()
        self.play_btn.adjustSize()
        self.layout_buttons()
        self.start_video()
        self.update()

    # --- video de fond -------------------------------------------------------
    def video_allowed(self):
        # pas de video de fond quand des lecteurs tournent : on menage le disque externe
        return self.player is not None and self.isVisible() and not self.main.ctrl.players

    def start_video(self):
        if not self.player:
            return
        self.player.stop()
        if not self.video_allowed():
            return
        path = video_path(self.main.conn, self.current()["video"]["id"])
        if not path:
            return
        self._seek_pending = True
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def on_duration(self, d):
        if self._seek_pending and d > 0:
            self._seek_pending = False
            v = self.current()["video"]
            from .players import random_start
            start = (v["position"] if self.current()["kind"] == "resume" and v["position"]
                     else random_start(d / 1000) or d / 1000 * 0.3)
            self.player.setPosition(int(start * 1000))

    def on_frame(self, frame):
        if self._seek_pending:
            return
        img = frame.toImage()
        if img.isNull():
            return
        self.frame = img
        if self.video_t0 is None:
            self.video_t0 = self.clock.elapsed()

    # --- horloge -------------------------------------------------------------
    def on_tick(self):
        now = self.clock.elapsed()
        dt = now - self.last_tick
        self.last_tick = now
        if not self.hovered and self.advances():
            self.elapsed += dt
            if self.elapsed >= self.slide_ms:
                self.go(1)
                return
        if self.player and self.frame is not None and not self.video_allowed():
            self.player.stop()
            self.frame = None
        self.update()

    def showEvent(self, e):
        self.last_tick = self.clock.elapsed()
        self.tick.start()
        if self.frame is None:
            QTimer.singleShot(0, self.start_video)
        super().showEvent(e)

    def hideEvent(self, e):
        self.tick.stop()
        if self.player:
            self.player.stop()
        self.frame = None
        super().hideEvent(e)

    def stop(self):
        self.tick.stop()
        if self.player:
            self.player.stop()

    # --- actions --------------------------------------------------------------
    def target(self):
        return self.main.ask_screen()

    def play_current(self):
        self.stop()
        self.main.ctrl.play(self.current()["video"]["id"], self.target(),
                            force_resume=self.current()["kind"] == "resume")

    def play_multi(self):
        self.stop()
        s = self.current()
        ids = [s["video"]["id"]] + [v["id"] for v in s["strip"]]
        for n, vid in enumerate(ids[:len(self.main.ctrl.screens())], 1):
            self.main.ctrl.play(vid, n)

    def see_current(self):
        s = self.current()
        if s.get("playlist"):
            self.main.show_playlist(s["playlist"])
        else:
            self.main.show_category(s["category"])

    def set_image(self, video_id, path):
        for s in self.slides:
            if s["video"]["id"] == video_id:
                s["image"] = path

    # --- souris ----------------------------------------------------------------
    def enterEvent(self, e):
        self.hovered = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered = False
        self.hover_card = None
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position()
        self.hover_card = None
        for i, (r, _) in enumerate(self.cards):
            if r.contains(pos):
                self.hover_card = i
        self.setCursor(Qt.PointingHandCursor if self.hover_card is not None else Qt.ArrowCursor)
        if self.hover_card is not None:
            self.setToolTip(self.cards[self.hover_card][1]["title"])
        else:
            self.setToolTip("")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.hover_card is not None:
            self.stop()
            self.main.ctrl.play(self.cards[self.hover_card][1]["id"], self.target())

    # --- mise en page ----------------------------------------------------------
    def resizeEvent(self, e):
        self.layout_buttons()
        super().resizeEvent(e)

    def layout_buttons(self):
        h = self.height()
        y = h - 128
        x = 52
        for b in (self.play_btn, self.multi_btn, self.see_btn):
            if b.isVisibleTo(self):
                b.adjustSize()
                b.move(x, y)
                x += b.width() + 12
        self.prev_btn.move(8, (h - 64) // 2 - 30)
        self.next_btn.move(self.width() - 52, (h - 64) // 2 - 30)

    def pixmap(self, path):
        if not path:
            return None
        pm = self._pix.get(path)
        if pm is None:
            pm = QPixmap(path)
            if len(self._pix) > 60:
                self._pix.clear()
            self._pix[path] = pm
        return pm if not pm.isNull() else None

    # --- dessin -------------------------------------------------------------------
    @staticmethod
    def cover_rect(sw, sh, w, h, zoom=1.0, pan=0.0):
        scale = max(w / sw, h / sh) * zoom
        dw, dh = sw * scale, sh * scale
        x = (w - dw) / 2 + pan * (dw - w) / 2
        y = (h - dh) / 3
        return QRectF(x, y, dw, dh)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        now = self.clock.elapsed()
        s = self.current()
        p.fillRect(self.rect(), QColor("#0f0f12"))

        # 1) image fixe avec zoom lent (Ken Burns)
        pm = self.pixmap(s.get("image"))
        progress = min(1.0, self.elapsed / self.slide_ms)
        if pm:
            direction = 1 if self.index % 2 == 0 else -1
            r = self.cover_rect(pm.width(), pm.height(), w, h, 1.04 + 0.08 * progress,
                                direction * (progress - 0.5) * 0.8)
            p.drawPixmap(r, pm, QRectF(pm.rect()))
        else:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, QColor("#1b1d2a"))
            g.setColorAt(1, QColor("#0f0f12"))
            p.fillRect(self.rect(), g)

        # 2) video de fond par-dessus, en fondu
        if self.frame is not None and self.video_t0 is not None:
            a = min(1.0, (now - self.video_t0) / VIDEO_FADE_MS)
            p.setOpacity(a)
            r = self.cover_rect(self.frame.width(), self.frame.height(), w, h)
            p.drawImage(r, self.frame, QRectF(self.frame.rect()))
            p.setOpacity(1.0)

        # 3) degrades de lisibilite
        g = QLinearGradient(0, 0, w * 0.75, 0)
        g.setColorAt(0, QColor(10, 10, 14, 240))
        g.setColorAt(0.5, QColor(10, 10, 14, 130))
        g.setColorAt(1, QColor(10, 10, 14, 0))
        p.fillRect(self.rect(), g)
        g = QLinearGradient(0, h * 0.45, 0, h)
        g.setColorAt(0, QColor(15, 15, 18, 0))
        g.setColorAt(1, QColor(15, 15, 18, 255))
        p.fillRect(self.rect(), g)

        # 4) textes
        left = 52
        textw = int(w * 0.5)
        f = QFont("Segoe UI", 10, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(ACCENT)
        p.drawText(left, h - 316, s["tag"].upper())
        hf = QFont("Segoe UI", 38 if len(s["heading"]) < 28 else 28, QFont.Bold)
        p.setFont(hf)
        p.setPen(QColor("white"))
        p.drawText(QRectF(left - 2, h - 306, textw, 76), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(s["heading"], Qt.ElideRight, textw))
        p.setFont(QFont("Segoe UI", 12))
        p.setPen(QColor("#d4d4dc"))
        if s["kind"] == "likes":
            line1 = "%d vidéo(s) aimée(s)   ·   thème : %s" % (s["count"], s["category"])
        elif s["kind"] == "theme":
            line1 = "%d vidéos   ·   en vedette : %s" % (s["count"], s["video"]["title"])
        else:
            line1 = "%s   ·   %d vidéos dans ce thème" % (s["category"], s["count"])
        p.drawText(QRectF(left, h - 226, textw, 26), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(line1, Qt.ElideRight, textw))
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor("#9a9aa6"))
        p.drawText(QRectF(left, h - 200, textw, 22), Qt.AlignLeft | Qt.AlignVCenter, video_info(s["video"]))
        if s["kind"] == "resume" and s["progress"] is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRoundedRect(QRectF(left, h - 170, 260, 4), 2, 2)
            p.setBrush(QColor("#e5484d"))
            p.drawRoundedRect(QRectF(left, h - 170, 260 * s["progress"], 4), 2, 2)
            rest = (s["video"]["duration"] or 0) - (s["video"]["position"] or 0)
            p.setPen(QColor("#c9c9d3"))
            p.drawText(QRectF(left + 272, h - 180, 200, 24), Qt.AlignLeft | Qt.AlignVCenter,
                       "reste " + fmt_duration(rest))

        # 5) bande de vignettes du theme
        self.cards = []
        n = min(len(s["strip"]), int((w * 0.46) // (CARD_W + 10)))
        if n >= 3:
            x0 = w - 48 - n * (CARD_W + 10) + 10
            y0 = h - 160
            p.setFont(QFont("Segoe UI", 9, QFont.Bold))
            p.setPen(QColor("#b9b9c4"))
            p.drawText(int(x0), int(y0 - 10), "AUSSI AIMÉES" if s["kind"] == "likes" else "DANS CE THÈME")
            for i, v in enumerate(s["strip"][:n]):
                r = QRectF(x0 + i * (CARD_W + 10), y0, CARD_W, CARD_H)
                hovered = i == self.hover_card
                if hovered:
                    r = r.adjusted(-6, -6, 6, 6)
                path = QPainterPath()
                path.addRoundedRect(r, 6, 6)
                p.save()
                p.setClipPath(path)
                tp = self.pixmap(v["thumb"]) if v["thumb"] else None
                if tp:
                    p.drawPixmap(self.cover_rect(tp.width(), tp.height(), r.width(), r.height())
                                 .translated(r.x(), r.y()), tp, QRectF(tp.rect()))
                else:
                    p.fillRect(r, QColor("#24242c"))
                p.restore()
                if hovered:
                    p.setPen(QPen(QColor("white"), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, 6, 6)
                self.cards.append((r, v))

        # 6) progression des diapositives (segments facon "stories")
        if len(self.slides) > 1:
            seg = SEG
            for i in range(len(self.slides)):
                r = QRectF(left + i * (seg + 6), h - 34, seg, 4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 70))
                p.drawRoundedRect(r, 2, 2)
                fill = 1.0 if i < self.index else (progress if i == self.index else 0.0)
                if fill > 0:
                    p.setBrush(QColor("white"))
                    p.drawRoundedRect(QRectF(r.x(), r.y(), seg * fill, 4), 2, 2)
            if self.hovered:
                p.setPen(QColor("#9a9aa6"))
                p.setFont(QFont("Segoe UI", 8))
                p.drawText(int(left + len(self.slides) * (seg + 6) + 6), h - 28, "en pause")

        # 7) fondu enchaine depuis la diapositive precedente
        if self.fade_from is not None:
            a = (now - self.fade_t0) / FADE_MS
            if a < 1:
                p.setOpacity(1 - a)
                p.drawPixmap(0, 0, self.fade_from)
                p.setOpacity(1.0)
            else:
                self.fade_from = None
        p.end()


HERO_STYLE = """
QPushButton#heroPrimary { background: white; color: #111; border: none; border-radius: 6px;
                          padding: 10px 24px; font-size: 12pt; font-weight: 600; }
QPushButton#heroPrimary:hover { background: #dcdce2; }
QPushButton#heroSecondary { background: rgba(80,80,90,170); color: white; border: none; border-radius: 6px;
                            padding: 10px 20px; font-size: 12pt; }
QPushButton#heroSecondary:hover { background: rgba(110,110,120,200); }
QPushButton#heroArrow { background: rgba(0,0,0,90); color: white; border: none; border-radius: 6px;
                        font-size: 26pt; padding: 0; }
QPushButton#heroArrow:hover { background: rgba(0,0,0,160); }
"""


SEG = 34          # longueur des repères du bandeau (réglée par l'accueil)
