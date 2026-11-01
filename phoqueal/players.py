"""Lecteurs VLC plein ecran (un par ecran) et controle clavier centralise."""
import os
import random
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QStackedLayout, QVBoxLayout, QWidget

from . import db, keymap, photos, view, volumes
from .blurfill import BlurMixin
from .config import APP_DIR


def _prepare_vlc():
    """Utilise un VLC portable place dans tools\\vlc s'il existe (version hors ligne)."""
    vlc_dir = APP_DIR / "tools" / "vlc"
    if (vlc_dir / "libvlc.dll").is_file():
        os.environ["PYTHON_VLC_LIB_PATH"] = str(vlc_dir / "libvlc.dll")
        os.environ["PYTHON_VLC_MODULE_PATH"] = str(vlc_dir)
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(vlc_dir))


_prepare_vlc()
import vlc  # noqa: E402

ACTIVE_BORDER = "#5b8def"
DEFAULT_VOLUME = 80


_saved_tracks = {}


def set_muted(player, muted):
    """Coupe / remet le son d'UN lecteur sans toucher aux autres (ni a la musique).

    Sous Windows, le « muet » de VLC peut agir sur tout le logiciel a la fois : on coupe donc
    plutot la piste audio du lecteur, et on la remet ensuite.
    """
    key = id(player)
    try:
        current = player.audio_get_track()
        if muted:
            if current not in (-1, None):
                _saved_tracks[key] = current
                player.audio_set_track(-1)
        elif current == -1:
            track = _saved_tracks.pop(key, None)
            if track in (None, -1):
                ids = [d[0] for d in (player.audio_get_track_description() or []) if d[0] != -1]
                track = ids[0] if ids else None
            if track is not None:
                player.audio_set_track(track)
    except Exception:
        pass


def refresh_frames(widget, frames, players):
    """Force VLC a reprendre la taille de sa zone (apres un changement d'ecran / de resolution)."""
    try:
        for fr in frames:
            fr.resize(max(1, fr.width() - 1), fr.height())
        lay = widget.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        for p in players:
            try:
                p.video_set_scale(0)          # 0 = adapter a la fenetre
            except Exception:
                pass
    except RuntimeError:
        pass


def place_on_screen(win, screen):
    """Plein ecran sur le BON ecran, des la premiere ouverture.

    Sous Windows, une fenetre neuve s'ouvre sur l'ecran principal et y reste en plein ecran :
    on l'attache d'abord a son ecran, puis on verifie peu apres (comme le fait la rotation)."""
    geo = screen.geometry()
    wh = win.windowHandle()
    if wh is not None and wh.screen() is not screen:
        wh.setScreen(screen)
    if win.isFullScreen():
        win.showNormal()
    win.setGeometry(geo)
    win.show()
    win.setGeometry(geo)
    win.showFullScreen()

    def check():
        target = getattr(win, "target_screen", screen)
        try:
            if win.isVisible() and win.isFullScreen() and win.screen() is not target:
                g = target.geometry()
                h = win.windowHandle()
                if h is not None:
                    h.setScreen(target)
                win.showNormal()
                win.setGeometry(g)
                win.showFullScreen()
        except RuntimeError:
            pass

    QTimer.singleShot(150, check)
    QTimer.singleShot(600, check)
    if hasattr(win, "refresh_video"):
        for ms in (350, 1000):
            QTimer.singleShot(ms, win.refresh_video)


def random_start(duration):
    """Moment au hasard dans les 80 derniers pourcents (en gardant une marge avant la fin)."""
    if not duration or duration < 40:
        return 0
    lo = 0.2 * duration
    hi = max(lo, duration - max(20.0, 0.05 * duration))
    return random.uniform(lo, hi)


def fmt_time(ms):
    s = max(0, int(ms // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


class PlayerWindow(BlurMixin, QWidget):
    def __init__(self, ctrl, number, screen):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.ctrl = ctrl
        self.number = number
        self.target_screen = screen
        self.video_id = None
        self.title = ""
        self.volume = DEFAULT_VOLUME
        self.muted = False
        self.was_fullscreen = True
        self.queue = None        # liste d'identifiants de videos (playlist)
        self.qi = 0
        self.qname = None
        self.loop = False
        self.random_mode = False
        self.view_mode = db.get_setting(ctrl.conn, "view_mode", "original")
        self.setWindowTitle("Phoque Al %d" % number)
        self.setAutoFillBackground(True)

        # deux surfaces video superposees : celle qu'on voit, et une en coulisse
        # (utilisee par le montage au rythme pour preparer l'extrait suivant)
        self.stack = QStackedLayout()
        self.video, self.player = self._make_surface(ctrl)
        self.alt_video, self.alt_player = self._make_surface(ctrl)
        self.stack.addWidget(self.video)
        self.stack.addWidget(self.alt_video)
        self.stack.setCurrentWidget(self.video)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.addLayout(self.stack)
        self._set_border(False)
        self.alt_ready = None        # (video_id, titre) quand l'extrait suivant est pret
        self.alt_token = 0
        self.clip_start = time.monotonic()
        self.montage_mute = False
        self.apply_view()
        self.blur_init(3)

        self._shortcuts = []
        self.bind_keys(ctrl.keymap)

    # --- format d'image --------------------------------------------------------------
    def refresh_video(self):
        refresh_frames(self, (self.video, self.alt_video), (self.player, self.alt_player))
        self.apply_view()

    def apply_view(self):
        w, h = self.video.width(), self.video.height()
        if w < 10 or h < 10:
            geo = self.target_screen.geometry()
            w, h = geo.width(), geo.height()
        for p in (self.player, self.alt_player):
            view.apply(p, self.view_mode, w, h)

    def set_view(self, mode, show=True):
        self.view_mode = mode
        self.apply_view()
        self.blur_layout()
        if show:
            self.osd(view.LABELS.get(mode, mode), 1500)

    def cycle_view(self):
        self.set_view(view.next_mode(self.view_mode))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.blur_layout)
        if self.view_mode in ("fill", "stretch"):
            QTimer.singleShot(0, self.apply_view)

    def _make_surface(self, ctrl):
        frame = QFrame(self)
        frame.setAttribute(Qt.WA_NativeWindow)
        frame.setAutoFillBackground(True)
        pal = frame.palette()
        pal.setColor(QPalette.Window, QColor("black"))
        frame.setPalette(pal)
        player = ctrl.instance.media_player_new()
        player.video_set_key_input(False)
        player.video_set_mouse_input(False)
        player.set_hwnd(int(frame.winId()))
        return frame, player

    # --- montage : extrait suivant prepare en coulisse ------------------------------
    def prepare_alt(self, video_id, path, title, start_s):
        self.alt_token += 1
        token = self.alt_token
        self.alt_ready = None
        p = self.alt_player
        p.set_media(self.ctrl.media_for(path, hold=True))
        set_muted(p, True)
        p.play()

        def seek():
            if token == self.alt_token:
                p.set_time(int(start_s * 1000))

        def hold():
            if token == self.alt_token:
                p.set_pause(1)

        def ready():
            if token == self.alt_token:
                self.alt_ready = (video_id, title)

        QTimer.singleShot(350, seek)
        for ms in (120, 250, 450):
            QTimer.singleShot(ms, lambda: token == self.alt_token and set_muted(p, True))
        QTimer.singleShot(750, hold)
        QTimer.singleShot(950, ready)

    def swap_alt(self):
        """Bascule instantanement sur l'extrait prepare. False s'il n'est pas pret."""
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
        QTimer.singleShot(400, self.blur_tick)
        return True

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
            "p_view": self.cycle_view,
            "p_blur": self.toggle_blur,
            "p_like": lambda: self.ctrl.toggle_like(self),
            "p_next": lambda: self.ctrl.queue_step(self, 1, manual=True),
            "p_prev": lambda: self.ctrl.queue_step(self, -1, manual=True),
        }
        for key, fn in actions.items():
            seq = mapping.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    # --- apparence ----------------------------------------------------------
    def _set_border(self, active):
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(ACTIVE_BORDER if active else "black"))
        self.setPalette(pal)

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange:
            self._set_border(self.isActiveWindow())
            if self.isActiveWindow():
                self.ctrl.last_active = self.number
        super().changeEvent(event)

    def place(self):
        place_on_screen(self, self.target_screen)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            geo = self.target_screen.availableGeometry()
            self.showNormal()
            self.resize(geo.width() // 2, geo.height() // 2)
            self.move(geo.center() - self.rect().center())
        else:
            self.place()

    def osd(self, text, ms=2500):
        try:
            self.player.video_set_marquee_int(vlc.VideoMarqueeOption.Enable, 1)
            self.player.video_set_marquee_int(vlc.VideoMarqueeOption.Size, 30)
            self.player.video_set_marquee_int(vlc.VideoMarqueeOption.Position, 5)
            self.player.video_set_marquee_int(vlc.VideoMarqueeOption.Timeout, ms)
            self.player.video_set_marquee_string(vlc.VideoMarqueeOption.Text, "%d  %s" % (self.number, text))
        except Exception:
            pass

    # --- lecture ------------------------------------------------------------
    def display_title(self):
        if self.queue:
            return "%s   (%s %d/%d)" % (self.title, self.qname or "playlist", self.qi + 1, len(self.queue))
        return self.title

    def load(self, video_id, path, title, start_s):
        self.clip_start = time.monotonic()
        self.video_id = video_id
        self.title = title
        media = self.ctrl.media_for(path)
        self.player.set_media(media)
        self.player.play()
        QTimer.singleShot(300, self._apply_audio)
        QTimer.singleShot(1000, self._apply_audio)
        for ms in (400, 1200):
            QTimer.singleShot(ms, self.blur_layout)
        if start_s:
            QTimer.singleShot(500, lambda: self.player.set_time(int(start_s * 1000)))
            QTimer.singleShot(900, lambda: self.osd("%s  (reprise à %s)" % (self.display_title(),
                                                                            fmt_time(start_s * 1000))))
        else:
            QTimer.singleShot(900, lambda: self.osd(self.display_title()))

    def _apply_audio(self):
        self.player.audio_set_volume(self.volume)
        set_muted(self.player, self.muted or self.montage_mute)

    def is_playing(self):
        return bool(self.player.is_playing())

    def toggle_pause(self):
        playing = self.player.is_playing()
        self.player.set_pause(1 if playing else 0)
        self.osd("Pause" if playing else "Lecture")

    def set_pause(self, paused):
        self.player.set_pause(1 if paused else 0)

    def seek(self, seconds):
        t = self.player.get_time()
        if t < 0:
            return
        length = self.player.get_length()
        t = max(0, t + seconds * 1000)
        if length > 0:
            t = min(t, length - 1000)
        self.player.set_time(int(t))
        self.osd("%s / %s" % (fmt_time(t), fmt_time(length)) if length > 0 else fmt_time(t))

    def change_volume(self, delta):
        self.volume = max(0, min(150, self.volume + delta))
        self.player.audio_set_volume(self.volume)
        if self.muted and delta > 0:
            self.toggle_mute()
        self.osd("Volume %d %%" % self.volume)

    def toggle_mute(self):
        self.set_mute(not self.muted)
        self.osd("Son coupé" if self.muted else "Son %d %%" % self.volume)

    def set_mute(self, muted):
        self.muted = muted
        set_muted(self.player, muted)

    def show_info(self):
        t, length = self.player.get_time(), self.player.get_length()
        self.osd("%s   %s / %s   vol %d %%%s" % (
            self.display_title(), fmt_time(t), fmt_time(length), self.volume, "  (muet)" if self.muted else ""), 4000)

    def closeEvent(self, event):
        self.ctrl.save_position(self)
        self.alt_token += 1
        for p in (self.player, self.alt_player):
            p.stop()
            p.release()
        self.ctrl.forget(self)
        super().closeEvent(event)


class Controller(QObject):
    """Gere les lecteurs, les ecrans et les raccourcis valables partout."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.instance = vlc.Instance(["--no-video-title-show", "--quiet", "--aout=directsound,any"])
        self.players = {}
        self.last_active = None
        self.hidden = False
        self._ticks = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        self.keymap = keymap.load(self.conn)
        self._shortcuts = []
        self.bind_keys()

    def bind_keys(self):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        actions = {
            "all_pause": self.pause_all,
            "all_back": lambda: self.each(lambda p: p.seek(-10)),
            "all_fwd": lambda: self.each(lambda p: p.seek(10)),
            "all_vol_up": lambda: self.each(lambda p: p.change_volume(5)),
            "all_vol_down": lambda: self.each(lambda p: p.change_volume(-5)),
            "all_mute": self.mute_all,
            "all_close": self.close_all,
            "panic": self.panic,
            "focus_home": lambda: self.focus(0),
            "discreet": lambda: self.main.set_discreet(not self.main.discreet),
            "all_view": self.cycle_view_all,
            "all_blur": self.toggle_blur_all,
            "rotate": lambda: self.rotate_screens(1),
            "rotate_back": lambda: self.rotate_screens(-1),
            "remote": lambda: self.main.toggle_remote(),
        }
        for n in range(1, 10):
            actions["focus_%d" % n] = lambda n=n: self.focus(n)
        for key, fn in actions.items():
            seq = self.keymap.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), self.main)
                sc.setContext(Qt.ApplicationShortcut)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    def apply_keymap(self, mapping):
        self.keymap = mapping
        self.bind_keys()
        for pw in self.players.values():
            pw.bind_keys(mapping)
        if getattr(self.main, "music", None):
            self.main.music.bind_keys(mapping)
        if hasattr(self.main, "bind_home_keys"):
            self.main.bind_home_keys(mapping)

    # --- ecrans -------------------------------------------------------------
    @staticmethod
    def screens():
        return sorted(QApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))

    def primary_number(self):
        prim = QApplication.primaryScreen()
        for i, s in enumerate(self.screens(), 1):
            if s is prim:
                return i
        return 1

    def pick_screen(self, choice):
        count = len(self.screens())
        if choice:
            return min(choice, count)
        prim = self.primary_number()
        free = [n for n in range(1, count + 1) if n not in self.players]
        others = [n for n in free if n != prim]
        if others:
            return others[0]
        if free:
            return free[0]
        return self.last_active if self.last_active in self.players else count

    def identify(self, ms=2500):
        """Affiche le numero de chaque ecran (et ce qu'il lit) pendant quelques secondes."""
        self.show_numbers(True)
        QTimer.singleShot(ms, lambda: self.show_numbers(False))

    def show_numbers(self, on):
        for lab in getattr(self, "_num_labels", []):
            try:
                lab.close()
                lab.deleteLater()
            except RuntimeError:
                pass
        self._num_labels = []
        if not on:
            return
        discreet = getattr(self.main, "discreet", False)
        for i, s in enumerate(self.screens(), 1):
            pw = self.players.get(i)
            sub = "libre" if pw is None else ("occupé" if discreet else
                                              (pw.display_title() if hasattr(pw, "display_title") else "mosaïque"))
            lab = QLabel("<div style='font-size:110pt;font-weight:800'>%d</div>"
                         "<div style='font-size:13pt'>%s</div>" % (i, sub.replace("<", "&lt;")[:60]))
            lab.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            lab.setAttribute(Qt.WA_ShowWithoutActivating)
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("background: rgba(40,70,150,235); color: white; border: 3px solid #8fb0ff;"
                              "border-radius: 18px; padding: 10px 24px;")
            lab.adjustSize()
            lab.resize(max(300, lab.width()), max(260, lab.height()))
            lab.move(s.geometry().center() - lab.rect().center())
            lab.show()
            lab.raise_()
            self._num_labels.append(lab)
        net = getattr(self.main, "network", None)          # ecrans d'un satellite : numero en incrustation
        if net is not None:
            for n in net.numbers():
                p = self.players.get(n)
                if p is not None:
                    p.osd("Écran %d" % n, 2500)

    # --- lecture ------------------------------------------------------------
    def resolve(self, video_id):
        if video_id is not None and int(video_id) < 0:                # video d'un satellite
            net = getattr(self.main, "network", None)
            return net.resolve(video_id) if net is not None else (None, None)
        row = self.conn.execute(
            "SELECT v.*, s.serial, s.rel_root FROM videos v JOIN sources s ON s.id = v.source_id "
            "WHERE v.id=?", (video_id,)).fetchone()
        if not row:
            return None, None
        root = volumes.resolve(row["serial"], row["rel_root"])
        if not root:
            self.main.status.setText("Disque absent : branche le disque externe.")
            return row, None
        if row["kind"] == "photo" and row["archive"]:
            try:
                path = photos.extract(row["id"], os.path.join(root, row["archive"]),
                                      photos.split_rel(row["rel_path"])[1])
            except Exception:
                self.main.status.setText("Photo illisible dans l'archive")
                return row, None
            return row, path
        path = os.path.join(root, row["rel_path"])
        if not os.path.isfile(path):
            self.main.status.setText("Fichier introuvable" if getattr(self.main, "discreet", False)
                                     else "Fichier introuvable : %s" % row["rel_path"])
            return row, None
        return row, path

    def media_for(self, path, hold=False):
        """Media VLC : une photo reste affichee `photo_seconds` (ou longtemps si elle est preparee
        en coulisse pour le montage, qui decide lui-meme du moment de la coupe)."""
        if os.path.splitext(path)[1].lower() in photos.IMAGE_EXT:
            secs = 3600 if hold else int(float(db.get_setting(self.conn, "photo_seconds", "6")))
            return self.instance.media_new(path, ":image-duration=%d" % secs)
        return self.instance.media_new(path, ":file-caching=1500")

    def _count_play(self, vid):
        self.conn.execute("UPDATE videos SET play_count = play_count + 1, last_played = ? WHERE id=?",
                          (time.time(), vid))
        self.conn.commit()

    def random_starts(self):
        return db.get_setting(self.conn, "start_mode", "random") == "random"

    def start_position(self, row, resume=False, force_resume=False):
        """Point de depart : reprise (si demandee), moment au hasard (reglage), ou debut."""
        duration = row["duration"] or 0
        pos = row["position"] or 0
        valid_resume = pos >= 30 and not (duration and pos > duration * 0.95)
        if force_resume and valid_resume:
            return pos
        if row["kind"] == "video" and self.random_starts():
            return random_start(duration)
        if resume and valid_resume:
            return pos
        return 0

    def play(self, video_id, choice=0, queue=None, qi=0, qname=None, loop=False, resume=False,
             force_resume=False):
        if choice == -1:                                              # choix d'ecran annule
            return False
        net = getattr(self.main, "network", None)
        if net is not None and choice and net.owns(choice):          # ecran d'un satellite
            return net.play_on(choice, video_id, queue=queue, qi=qi, qname=qname, loop=loop)
        row, path = self.resolve(video_id)
        if not path:
            return False
        number = self.pick_screen(choice)
        screen = self.screens()[number - 1]
        pw = self.players.get(number)
        if pw is not None and getattr(pw, "is_mosaic", False):
            pw.close()
            pw = None
        if pw:
            self.save_position(pw)
        else:
            pw = PlayerWindow(self, number, screen)
            self.players[number] = pw
            pw.place()
        pw.queue, pw.qi, pw.qname, pw.loop = queue, qi, qname, loop
        start = self.start_position(row, resume=(not queue or resume), force_resume=force_resume)
        pw.load(row["id"], path, row["title"], start)
        pw.raise_()
        pw.activateWindow()
        self.main.on_playback_started()
        self._count_play(row["id"])
        self.main.status.setText("Écran %d : lecture lancée" % number if getattr(self.main, "discreet", False)
                                 else "Écran %d : %s" % (number, pw.display_title()))
        return True

    def play_queue(self, ids, choice=0, name=None, start=0, loop=False, resume=False, random_mode=False):
        """Lit une liste de videos a la suite sur un ecran (saute les fichiers absents)."""
        if choice == -1:
            return False
        for i in range(start, len(ids)):
            if self.play(ids[i], choice, queue=list(ids), qi=i, qname=name, loop=loop,
                         resume=resume and i == start):
                for w in self.players.values():
                    if getattr(w, "queue", None) is not None and w.video_id == ids[i]:
                        w.random_mode = random_mode
                return True
        return False

    def play_queue_multi(self, ids, name=None, loop=False):
        """Repartit la liste sur tous les ecrans : 1 video sur n pour chacun."""
        n = len(self.screens())
        for k in range(n):
            part = list(ids[k::n])
            if part:
                self.play_queue(part, k + 1, name, 0, loop)

    def queue_step(self, pw, step, manual=False):
        """Passe a la video suivante/precedente de la playlist du lecteur. False si fin."""
        if getattr(pw, "is_remote", False):
            return pw.net.step(pw, step, manual)
        if getattr(pw, "is_mosaic", False):
            return False
        if not pw.queue:
            if not manual or not pw.video_id:
                return False
            # pas de playlist : on enchaine dans le theme de la video en cours
            row = self.conn.execute("SELECT category FROM videos WHERE id=?", (pw.video_id,)).fetchone()
            ids = db.category_video_ids(self.conn, row["category"]) if row else []
            if pw.video_id not in ids or len(ids) < 2:
                pw.osd("Rien d'autre dans ce thème")
                return False
            pw.queue, pw.qi, pw.qname = ids, ids.index(pw.video_id), row["category"]
        n = len(pw.queue)
        i = pw.qi
        if pw.random_mode and n > 1:
            for _ in range(10):
                j = random.randrange(n)
                if j != pw.qi:
                    row, path = self.resolve(pw.queue[j])
                    if path:
                        self.save_position(pw)
                        pw.qi = j
                        pw.load(row["id"], path, row["title"], self.start_position(row))
                        self._count_play(row["id"])
                        return True
            return False
        for _ in range(n):
            i += step
            if i >= n or i < 0:
                if not pw.loop:
                    if manual:
                        pw.osd("Début de la playlist" if i < 0 else "Fin de la playlist")
                    return False
                i %= n
            row, path = self.resolve(pw.queue[i])
            if path:
                self.save_position(pw)
                pw.qi = i
                pw.load(row["id"], path, row["title"], self.start_position(row))
                self._count_play(row["id"])
                return True
        return False

    def open_mosaic(self, config, state=None, screen=0, mosaic_id=None):
        from .mosaic import MosaicWindow
        number = min(screen, len(self.screens())) if screen else self.pick_screen(0)
        old = self.players.get(number)
        if old is not None:
            old.close()
        mw = MosaicWindow(self, number, self.screens()[number - 1], config, state, mosaic_id)
        self.players[number] = mw
        mw.place()
        mw.raise_()
        mw.activateWindow()
        self.main.on_playback_started()
        self.main.status.setText("Écran %d : mosaïque" % number if getattr(self.main, "discreet", False)
                                 else "Écran %d : mosaïque « %s »" % (number, mw.config["name"]))
        return mw

    def save_position(self, pw):
        if getattr(pw, "is_mosaic", False) or not pw.video_id:
            return
        if getattr(self.main, "montage", None) and self.main.montage.active():
            return
        t, length = pw.player.get_time(), pw.player.get_length()
        if t <= 0:
            return
        pos = t / 1000.0
        if length > 0 and t > length * 0.95:
            pos = 0
        self.conn.execute("UPDATE videos SET position=? WHERE id=?", (pos, pw.video_id))
        self.conn.commit()

    def forget(self, pw):
        if self.players.get(pw.number) is pw:
            del self.players[pw.number]

    def tick(self):
        self._ticks += 1
        for pw in list(self.players.values()):
            if getattr(pw, "is_remote", False):
                continue
            if getattr(pw, "is_mosaic", False):
                pw.tick_mosaic()
                continue
            if pw.player.get_state() == vlc.State.Ended:
                if getattr(self.main, "montage", None) and self.main.montage.active():
                    self.main.montage.force_cut(pw)
                    continue
                self.conn.execute("UPDATE videos SET position=0 WHERE id=?", (pw.video_id,))
                self.conn.commit()
                if not pw.queue and pw.loop and pw.video_id:
                    pw.player.stop()          # une seule video en boucle : on la rejoue
                    pw.player.play()
                    continue
                pw.video_id = None
                if not self.queue_step(pw, 1):
                    pw.close()
            elif self._ticks % 10 == 0:
                self.save_position(pw)

    # --- commandes globales -------------------------------------------------
    def each(self, fn):
        for pw in list(self.players.values()):
            fn(pw)

    def pause_all(self):
        playing = any(p.is_playing() for p in self.players.values())
        self.each(lambda p: p.set_pause(playing))
        self.each(lambda p: p.osd("Pause" if playing else "Lecture"))

    def mute_all(self):
        muted = not all(p.muted for p in self.players.values())
        self.each(lambda p: p.set_mute(muted))
        self.each(lambda p: p.osd("Son coupé" if muted else "Son %d %%" % p.volume))

    def screens_changed(self):
        """Un ecran a ete branche / debranche (ex. tablette) : les lecteurs gardent LEUR ecran,
        les numeros sont recalcules (ils suivent la position gauche -> droite) ;
        un lecteur dont l'ecran a disparu est ferme (sa position est memorisee)."""
        screens = self.screens()
        moved = {}
        for num, w in list(self.players.items()):
            if getattr(w, "is_remote", False):
                continue
            target = getattr(w, "target_screen", None)
            if target in screens:
                new = screens.index(target) + 1
                w.number = new
                w.setWindowTitle("Phoque Al %d" % new)
                moved[new] = w
            else:
                self.players.pop(num, None)
                try:
                    w.close()
                except RuntimeError:
                    pass
        moved.update({k: v for k, v in self.players.items() if getattr(v, "is_remote", False)})
        self.players = moved
        if self.last_active and self.last_active > len(screens):
            self.last_active = None
        net = getattr(self.main, "network", None)
        if net is not None:
            net.renumber()
        if hasattr(self.main, "on_screens_changed"):
            self.main.on_screens_changed()
        self.main.status.setText("Écrans détectés : %d" % len(screens))

    def all_numbers(self):
        """Ecrans locaux + ecrans des satellites connectes."""
        net = getattr(self.main, "network", None)
        return list(range(1, len(self.screens()) + 1)) + (net.numbers() if net is not None else [])

    def play_url(self, url, choice=0, title="", start=0.0):
        """Lit une video diffusee par le reseau (ordre du PC principal)."""
        number = self.pick_screen(choice)
        screen = self.screens()[number - 1]
        pw = self.players.get(number)
        if pw is not None and getattr(pw, "is_mosaic", False):
            pw.close()
            pw = None
        if pw:
            self.save_position(pw)
        else:
            pw = PlayerWindow(self, number, screen)
            self.players[number] = pw
            pw.place()
        pw.queue, pw.qi, pw.qname = None, 0, None
        pw.load(None, url, title or "Vidéo", start or 0)
        pw.raise_()
        pw.activateWindow()
        self.main.on_playback_started()
        return True

    def rotate_screens(self, step=1):
        """Decale chaque lecteur (ou mosaique) d'un ecran : 1 -> 2 -> 3 -> 1. La lecture continue."""
        screens = self.screens()
        n = len(screens)
        if n < 2 or not self.players:
            return
        moved = {}
        for num, w in [(k, v) for k, v in self.players.items() if not getattr(v, "is_remote", False)]:
            new = (num - 1 + step) % n + 1
            w.number = new
            w.target_screen = screens[new - 1]
            w.setWindowTitle("Phoque Al %d" % new)
            moved[new] = w
        moved.update({k: v for k, v in self.players.items() if getattr(v, "is_remote", False)})
        self.players = moved
        if self.last_active:
            self.last_active = (self.last_active - 1 + step) % n + 1
        if self.hidden:
            return            # mode panique : les fenetres seront replacees au retour
        for w in moved.values():
            if getattr(w, "is_remote", False):
                continue
            if w.isFullScreen():
                place_on_screen(w, w.target_screen)      # rattache au nouvel ecran + taille
            else:
                size = w.size()
                avail = w.target_screen.availableGeometry()
                w.setGeometry(avail.x() + (avail.width() - size.width()) // 2,
                              avail.y() + (avail.height() - size.height()) // 2, size.width(), size.height())
            QTimer.singleShot(400, lambda w=w: w.osd("Écran %d" % w.number, 1200))
            if hasattr(w, "refresh_video"):
                for ms in (300, 900):
                    QTimer.singleShot(ms, w.refresh_video)

    def toggle_like(self, owner):
        """J'aime / je n'aime plus la video d'un lecteur (ou de la case selectionnee d'une mosaique)."""
        if getattr(owner, "is_mosaic", False):
            owner = owner.selected
        if not owner or not owner.video_id:
            return
        if owner.video_id < 0:
            owner.osd("J'aime : pas encore disponible pour les vidéos d'un satellite", 2000)
            return
        liked = db.toggle_like(self.conn, owner.video_id)
        owner.osd("\u2665 J'aime" if liked else "Retiré des J'aime", 1500)
        if hasattr(self.main, "on_like_changed"):
            self.main.on_like_changed(owner.video_id, liked)

    def toggle_blur_all(self):
        players = [p for p in self.players.values() if hasattr(p, "set_blur") or hasattr(p, "tiles")]
        if not players:
            return
        first = players[0]
        on = not (first.tiles[0]._b_on if hasattr(first, "tiles") else first._b_on)
        db.set_setting(self.conn, "blur_fill", "1" if on else "0")
        for p in players:
            for t in (p.tiles if hasattr(p, "tiles") else [p]):
                t.set_blur(on, show=False)
            p.osd("Fond flou activé" if on else "Fond flou désactivé", 1500)

    def cycle_view_all(self):
        players = list(self.players.values())
        if players:
            mode = view.next_mode(players[0].view_mode)
            for p in players:
                p.set_view(mode)

    def close_all(self):
        self.each(lambda p: p.close())

    def focus(self, n):
        if n == 0:
            self.main.showNormal()
            self.main.raise_()
            self.main.activateWindow()
            return
        pw = self.players.get(n)
        if pw:
            pw.raise_()
            pw.activateWindow()
        else:
            self.main.status.setText("Aucune vidéo sur l'écran %d." % n)

    def panic(self):
        if not self.hidden:
            self.each(lambda p: p.set_pause(True))
            if getattr(self.main, "music", None):
                self.main.music.pause()
            remote = getattr(self.main, "remote", None)
            if remote is not None and remote.enabled:
                remote.watch.stop()
                remote.hide()
            for p in self.players.values():
                p.was_fullscreen = p.isFullScreen()
                p.hide()
            self.main.showMinimized()
            self.hidden = True
        else:
            for p in self.players.values():
                p.place() if p.was_fullscreen else p.show()
            self.main.showNormal()
            self.main.activateWindow()
            self.hidden = False
            remote = getattr(self.main, "remote", None)
            if remote is not None and remote.enabled:
                remote.watch.start()
