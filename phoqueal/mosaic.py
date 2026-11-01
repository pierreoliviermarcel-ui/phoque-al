"""Mode mosaique : plusieurs videos sur un meme ecran.

Souris = controle d'UNE video (la tuile sous le curseur) :
  clic gauche   selectionner          double-clic   agrandir / reduire
  molette       volume                Ctrl+molette  avancer / reculer de 10 s
  clic milieu   couper / remettre le son
  clic droit    menu complet (pause, suivante, rejouer, choisir une video, fin de video, enregistrer...)
Clavier = controle de TOUTES les videos de la mosaique (memes touches que le lecteur).
"""
import random
import time

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QInputDialog, QMenu, QStackedLayout, QVBoxLayout, QWidget

from . import db, view
from .blurfill import BlurMixin
from .players import ACTIVE_BORDER, fmt_time, set_muted, vlc

GRIDS = {2: (1, 2), 3: (1, 3), 4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3), 12: (3, 4), 16: (4, 4)}

DEFAULT_CONFIG = {
    "name": "Mosaïque",
    "count": 4,
    "source": {"type": "all", "value": None},   # all | theme | playlist
    "order": "random",                           # random | ordered
    "start": "random",                           # begin | random
    "end": "next",                               # next | replay
    "sound": "first",                            # first | all | none
}


def marquee(player, text, ms=2500, size=22):
    try:
        player.video_set_marquee_int(vlc.VideoMarqueeOption.Enable, 1)
        player.video_set_marquee_int(vlc.VideoMarqueeOption.Size, size)
        player.video_set_marquee_int(vlc.VideoMarqueeOption.Position, 5)
        player.video_set_marquee_int(vlc.VideoMarqueeOption.Timeout, ms)
        player.video_set_marquee_string(vlc.VideoMarqueeOption.Text, text)
    except Exception:
        pass


class Tile(BlurMixin, QWidget):
    is_tile = True

    def __init__(self, mosaic, index):
        super().__init__(mosaic)
        self.mosaic = mosaic
        self.ctrl = mosaic.ctrl
        self.index = index
        self.video_id = None
        self.title = ""
        self.muted = True
        self.volume = 80
        self.end_mode = None          # None = comme la mosaique
        self.setAutoFillBackground(True)
        self.queue = None
        self.alt_ready = None
        self.alt_token = 0
        self.clip_start = time.monotonic()
        self.montage_mute = False
        self.view_mode = mosaic.view_mode
        self.stack = QStackedLayout()
        self.video, self.player = self._make_surface()
        self.alt_video, self.alt_player = self._make_surface()
        self.stack.addWidget(self.video)
        self.stack.addWidget(self.alt_video)
        self.stack.setCurrentWidget(self.video)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addLayout(self.stack)
        self.set_selected(False)
        self.blur_init(2, 3000)

    def _make_surface(self):
        frame = QFrame(self)
        frame.setAttribute(Qt.WA_NativeWindow)
        frame.setAutoFillBackground(True)
        pal = frame.palette()
        pal.setColor(QPalette.Window, QColor("black"))
        frame.setPalette(pal)
        player = self.ctrl.instance.media_player_new()
        player.video_set_key_input(False)
        player.video_set_mouse_input(False)
        player.set_hwnd(int(frame.winId()))
        return frame, player

    # --- montage : extrait suivant en coulisse (meme principe que le lecteur) ---------------
    def prepare_alt(self, video_id, path, title, start_s):
        self.alt_token += 1
        token = self.alt_token
        self.alt_ready = None
        p = self.alt_player
        p.set_media(self.ctrl.media_for(path, hold=True))
        set_muted(p, True)
        p.play()
        QTimer.singleShot(350, lambda: token == self.alt_token and p.set_time(int(start_s * 1000)))
        for ms in (120, 250, 450):
            QTimer.singleShot(ms, lambda: token == self.alt_token and set_muted(p, True))
        QTimer.singleShot(750, lambda: token == self.alt_token and p.set_pause(1))

        def ready():
            if token == self.alt_token:
                self.alt_ready = (video_id, title)
        QTimer.singleShot(950, ready)

    def swap_alt(self):
        if not self.alt_ready:
            return False
        vid, title = self.alt_ready
        self.alt_ready = None
        self.alt_token += 1
        new_p, new_v = self.alt_player, self.alt_video
        old_p, old_v = self.player, self.video
        self.stack.setCurrentWidget(new_v)
        new_p.set_pause(0)
        new_p.audio_set_volume(self.volume)
        set_muted(new_p, self.muted or self.montage_mute)
        self.player, self.video = new_p, new_v
        self.alt_player, self.alt_video = old_p, old_v
        old_p.set_pause(1)
        self.video_id, self.title = vid, title
        self.clip_start = time.monotonic()
        QTimer.singleShot(50, self.blur_layout)
        return True

    def refresh_video(self):
        from .players import refresh_frames
        refresh_frames(self, (self.video, self.alt_video), (self.player, self.alt_player))
        self.apply_view()

    def apply_view(self):
        w, h = max(self.video.width(), 10), max(self.video.height(), 10)
        for p in (self.player, self.alt_player):
            view.apply(p, self.view_mode, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.apply_view)
        QTimer.singleShot(0, self.blur_layout)

    def set_selected(self, on):
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(ACTIVE_BORDER if on else "#050507"))
        self.setPalette(pal)

    # --- lecture ------------------------------------------------------------------
    def load(self, video_id, position=None):
        row, path = self.ctrl.resolve(video_id)
        if not path:
            return False
        self.video_id = row["id"]
        self.title = row["title"]
        self.clip_start = time.monotonic()
        media = self.ctrl.media_for(path)
        self.player.set_media(media)
        self.player.play()
        if position is None and self.mosaic.config.get("start") == "random" and row["duration"]:
            from .players import random_start
            position = random_start(row["duration"])
        QTimer.singleShot(300, self.apply_audio)
        QTimer.singleShot(1000, self.apply_audio)
        for ms in (400, 1200):
            QTimer.singleShot(ms, self.blur_layout)
        if position:
            QTimer.singleShot(500, lambda p=position: self.player.set_time(int(p * 1000)))
        QTimer.singleShot(900, lambda: self.osd(self.title, 2000))
        self.ctrl._count_play(row["id"])
        return True

    def apply_audio(self):
        self.player.audio_set_volume(self.volume)
        set_muted(self.player, self.muted or self.montage_mute)

    def restart(self):
        self.player.stop()
        self.player.play()
        QTimer.singleShot(300, self.apply_audio)
        QTimer.singleShot(1000, self.apply_audio)
        for ms in (400, 1200):
            QTimer.singleShot(ms, self.blur_layout)

    def is_playing(self):
        return bool(self.player.is_playing())

    def set_pause(self, paused):
        self.player.set_pause(1 if paused else 0)

    def toggle_pause(self):
        playing = self.is_playing()
        self.set_pause(playing)
        self.osd("Pause" if playing else "Lecture")

    def seek(self, seconds):
        t = self.player.get_time()
        if t < 0:
            return
        length = self.player.get_length()
        t = max(0, t + seconds * 1000)
        if length > 0:
            t = min(t, length - 1000)
        self.player.set_time(int(t))
        self.osd("%s / %s" % (fmt_time(t), fmt_time(length)) if length > 0 else fmt_time(t), 1500)

    def change_volume(self, delta, show=True):
        self.volume = max(0, min(150, self.volume + delta))
        self.player.audio_set_volume(self.volume)
        if show:
            self.osd("Volume %d %%" % self.volume, 1500)

    def set_mute(self, muted):
        self.muted = muted
        set_muted(self.player, muted)

    def toggle_mute(self):
        self.set_mute(not self.muted)
        self.osd("Son coupé" if self.muted else "Son %d %%" % self.volume, 1500)

    def osd(self, text, ms=2500):
        marquee(self.player, "%d  %s" % (self.index + 1, text), ms)

    def state(self):
        t = self.player.get_time()
        return {"video_id": self.video_id, "position": (t / 1000.0) if t and t > 0 else 0,
                "muted": self.muted, "volume": self.volume, "end_mode": self.end_mode}

    def release(self):
        self.alt_token += 1
        for p in (self.player, self.alt_player):
            p.stop()
            p.release()

    # --- souris : controle individuel ---------------------------------------------------
    def mousePressEvent(self, e):
        self.mosaic.select(self)
        if e.button() == Qt.MiddleButton:
            self.toggle_mute()
        elif e.button() == Qt.RightButton:
            self.mosaic.tile_menu(self, e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.mosaic.toggle_zoom(self)

    def wheelEvent(self, e):
        self.mosaic.select(self)
        step = 1 if e.angleDelta().y() > 0 else -1
        if e.modifiers() & Qt.ControlModifier:
            self.seek(10 * step)
        else:
            self.change_volume(5 * step)


class MosaicWindow(QWidget):
    is_mosaic = True

    def __init__(self, ctrl, number, screen, config, state=None, mosaic_id=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.ctrl = ctrl
        self.conn = ctrl.conn
        self.number = number
        self.target_screen = screen
        self.config = dict(DEFAULT_CONFIG, **config)
        self.mosaic_id = mosaic_id
        self.view_mode = self.config.get("view") or db.get_setting(ctrl.conn, "view_mode", "original")
        self.was_fullscreen = True
        self.zoomed = None
        self.video_id = None
        self.setWindowTitle("Phoque Al %d" % number)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#050507"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        count = self.config["count"] if self.config["count"] in GRIDS else 4
        rows, cols = GRIDS[count]
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.tiles = []
        for i in range(count):
            t = Tile(self, i)
            grid.addWidget(t, i // cols, i % cols)
            self.tiles.append(t)
        self.build_pool()

        sound = self.config["sound"]
        for i, t in enumerate(self.tiles):
            t.muted = not (sound == "all" or (sound == "first" and i == 0))
        state = state or []
        for i, t in enumerate(self.tiles):
            st = state[i] if i < len(state) else None
            if st:
                t.muted = st.get("muted", t.muted)
                t.volume = st.get("volume", t.volume)
                t.end_mode = st.get("end_mode")
                if st.get("video_id") and t.load(st["video_id"], st.get("position") or 0.01):
                    continue
            self.load_next(t)
        self.selected = self.tiles[0]
        self.selected.set_selected(True)
        self._shortcuts = []
        self.bind_keys(ctrl.keymap)

    # --- reserve de videos -------------------------------------------------------------
    def build_pool(self):
        src = self.config["source"]
        if src["type"] == "theme":
            ids = db.category_video_ids(self.conn, src["value"])
        elif src["type"] == "playlist":
            ids = [r["id"] for r in db.playlist_videos(self.conn, src["value"])]
        elif src["type"] in ("person", "tag"):
            from . import people as P
            fn = P.person_video_ids if src["type"] == "person" else P.tag_video_ids
            ids = fn(self.conn, src["value"], db.kind_filter(self.conn, "v."))
        else:
            ids = db.all_video_ids(self.conn)
        if self.config["order"] == "random":
            random.shuffle(ids)
        self.pool = ids
        self.cursor = 0

    def next_video(self, exclude=()):
        if not self.pool:
            return None
        shown = {t.video_id for t in self.tiles} | set(exclude)
        for _ in range(len(self.pool)):
            if self.cursor >= len(self.pool):
                self.cursor = 0
                if self.config["order"] == "random":
                    random.shuffle(self.pool)
            vid = self.pool[self.cursor]
            self.cursor += 1
            if vid not in shown or len(self.pool) <= len(self.tiles):
                return vid
        return self.pool[0]

    def load_next(self, tile):
        tried = set()
        for _ in range(min(20, max(1, len(self.pool)))):
            vid = self.next_video(tried)
            if vid is None:
                return False
            tried.add(vid)
            if tile.load(vid):
                return True
        return False

    def tick_mosaic(self):
        montage = getattr(self.ctrl.main, "montage", None)
        for t in self.tiles:
            if t.player.get_state() == vlc.State.Ended:
                if montage and montage.active() and montage.include_mosaics:
                    montage.force_cut(t)
                    continue
                if (t.end_mode or self.config["end"]) == "replay":
                    t.restart()
                else:
                    self.load_next(t)

    # --- selection / zoom ------------------------------------------------------------------
    def select(self, tile):
        if self.selected is not tile:
            self.selected.set_selected(False)
            self.selected = tile
            tile.set_selected(True)
        self.activateWindow()

    def toggle_zoom(self, tile):
        if self.zoomed is None:
            for t in self.tiles:
                if t is not tile:
                    t.hide()
            self.zoomed = tile
        else:
            for t in self.tiles:
                t.show()
            self.zoomed = None

    # --- clavier : controle de groupe ---------------------------------------------------------
    def bind_keys(self, mapping):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        actions = {
            "p_pause": self.toggle_pause,
            "p_back": lambda: self.seek(-10), "p_fwd": lambda: self.seek(10),
            "p_back_long": lambda: self.seek(-60), "p_fwd_long": lambda: self.seek(60),
            "p_vol_up": lambda: self.change_volume(5), "p_vol_down": lambda: self.change_volume(-5),
            "p_mute": self.toggle_mute, "p_fullscreen": self.toggle_fullscreen,
            "p_info": self.show_info, "p_close": self.close,
            "p_next": self.next_all, "p_prev": lambda: self.osd("Précédent : non disponible en mosaïque"),
            "mo_save": self.save,
            "p_view": self.cycle_view,
            "p_blur": lambda: [t.set_blur(not self.tiles[0]._b_on, show=False) for t in self.tiles],
            "p_like": lambda: self.ctrl.toggle_like(self),
        }
        for key, fn in actions.items():
            seq = mapping.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    # interface commune avec PlayerWindow (utilisee par le controleur et Maj + touche)
    @property
    def muted(self):
        return all(t.muted for t in self.tiles)

    @property
    def volume(self):
        return self.tiles[0].volume

    def is_playing(self):
        return any(t.is_playing() for t in self.tiles)

    def set_pause(self, paused):
        for t in self.tiles:
            t.set_pause(paused)

    def toggle_pause(self):
        playing = self.is_playing()
        self.set_pause(playing)
        self.osd("Pause" if playing else "Lecture")

    def seek(self, seconds):
        for t in self.tiles:
            t.seek(seconds)

    def change_volume(self, delta):
        for t in self.tiles:
            t.change_volume(delta, show=False)
        self.osd("Volume %+d" % delta, 1200)

    def set_mute(self, muted):
        for t in self.tiles:
            t.set_mute(muted)

    def toggle_mute(self):
        if not self.muted:
            self.set_mute(True)
            self.osd("Son coupé", 1500)
        else:
            sound = self.config["sound"]
            for i, t in enumerate(self.tiles):
                t.set_mute(not (sound == "all" or (sound != "all" and i == 0)))
            self.osd("Son rétabli", 1500)

    def set_view(self, mode, show=True):
        self.view_mode = mode
        self.config["view"] = mode
        for t in self.tiles:
            t.view_mode = mode
            t.apply_view()
            t.blur_layout()
        if show:
            self.osd(view.LABELS.get(mode, mode), 1500)

    def refresh_video(self):
        for t in self.tiles:
            t.refresh_video()

    def cycle_view(self):
        self.set_view(view.next_mode(self.view_mode))

    def next_all(self):
        for t in self.tiles:
            self.load_next(t)

    def show_info(self):
        for t in self.tiles:
            tm, ln = t.player.get_time(), t.player.get_length()
            t.osd("%s   %s / %s%s" % (t.title, fmt_time(tm), fmt_time(ln), "  (muet)" if t.muted else ""), 4000)

    def osd(self, text, ms=2500):
        for t in self.tiles:
            t.osd(text, ms)

    # --- fenetre ----------------------------------------------------------------------------
    def place(self):
        from .players import place_on_screen
        place_on_screen(self, self.target_screen)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            geo = self.target_screen.availableGeometry()
            self.showNormal()
            self.resize(geo.width() * 2 // 3, geo.height() * 2 // 3)
            self.move(geo.center() - self.rect().center())
        else:
            self.place()

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.ctrl.last_active = self.number
        super().changeEvent(event)

    def closeEvent(self, event):
        for t in self.tiles:
            t.release()
        self.ctrl.forget(self)
        super().closeEvent(event)

    # --- menu d'une tuile ---------------------------------------------------------------------
    def tile_menu(self, tile, global_pos):
        menu = QMenu(self)
        a_pause = menu.addAction("Pause" if tile.is_playing() else "Lecture")
        a_mute = menu.addAction("Activer le son" if tile.muted else "Couper le son")
        a_solo = menu.addAction("Son uniquement sur cette vidéo")
        menu.addSeparator()
        a_like = menu.addAction("\u2665  Je n'aime plus" if db.is_liked(self.conn, tile.video_id) else "\u2665  J'aime")
        a_next = menu.addAction("Vidéo suivante")
        a_replay = menu.addAction("Rejouer depuis le début")
        a_pick = menu.addAction("Choisir une vidéo…")
        end_menu = menu.addMenu("À la fin de cette vidéo")
        end_opts = {}
        for key, label in ((None, "Comme la mosaïque (%s)" % ("rejouer" if self.config["end"] == "replay"
                                                               else "suivante")),
                           ("replay", "Rejouer la même"), ("next", "Passer à la suivante")):
            act = end_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(tile.end_mode == key)
            end_opts[act] = key
        a_zoom = menu.addAction("Réduire" if self.zoomed else "Agrandir cette vidéo")
        a_full = menu.addAction("Ouvrir seule en plein écran sur cet écran")
        menu.addSeparator()
        a_save = menu.addAction("Enregistrer la mosaïque")
        a_save_as = menu.addAction("Enregistrer sous un autre nom…")
        a_close = menu.addAction("Fermer la mosaïque")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == a_pause:
            tile.toggle_pause()
        elif chosen == a_mute:
            tile.toggle_mute()
        elif chosen == a_solo:
            for t in self.tiles:
                t.set_mute(t is not tile)
            tile.osd("Son ici uniquement", 1500)
        elif chosen == a_like:
            self.select(tile)
            self.ctrl.toggle_like(self)
        elif chosen == a_next:
            self.load_next(tile)
        elif chosen == a_replay:
            tile.player.set_time(0)
        elif chosen == a_pick:
            from .mosaic_ui import VideoPicker
            vid = VideoPicker.pick(self, self.conn)
            if vid:
                tile.load(vid, 0.01)
        elif chosen in end_opts:
            tile.end_mode = end_opts[chosen]
        elif chosen == a_zoom:
            self.toggle_zoom(tile)
        elif chosen == a_full:
            vid, number = tile.video_id, self.number
            self.close()
            self.ctrl.play(vid, number)
        elif chosen == a_save:
            self.save()
        elif chosen == a_save_as:
            self.save(ask=True)
        elif chosen == a_close:
            self.close()

    # --- enregistrement ------------------------------------------------------------------------
    def save(self, ask=False):
        name = self.config.get("name") or "Mosaïque"
        if ask or not self.mosaic_id:
            name, ok = QInputDialog.getText(self, "Enregistrer la mosaïque", "Nom de la mosaïque :", text=name)
            name = name.strip()
            if not ok or not name:
                return
            if ask:
                self.mosaic_id = None
        self.config["name"] = name
        self.mosaic_id = db.save_mosaic(self.conn, self.mosaic_id, name, self.number, self.config,
                                        [t.state() for t in self.tiles])
        self.osd("Mosaïque « %s » enregistrée" % name, 2500)
