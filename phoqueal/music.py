"""Musique de fond : lit les fichiers audio d'un dossier pendant les videos."""
import os
import random
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut

from . import db, volumes
from .players import vlc

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}


def mrl_to_path(mrl):
    if not mrl:
        return None
    path = unquote(urlparse(mrl).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return os.path.normpath(path)


def mrl_to_name(mrl):
    if not mrl:
        return ""
    path = unquote(urlparse(mrl).path)
    return os.path.splitext(os.path.basename(path))[0]


class MusicPlayer(QObject):
    track_changed = Signal(str)
    state_changed = Signal(bool)

    def __init__(self, main, instance):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.instance = instance
        self.files = []
        self.mp = instance.media_player_new()
        self.mlp = instance.media_list_player_new()
        self.mlp.set_media_player(self.mp)
        self.mlp.set_playback_mode(vlc.PlaybackMode.loop)
        self.volume = int(db.get_setting(self.conn, "music_volume", "60"))
        self.shuffle = db.get_setting(self.conn, "music_shuffle", "1") == "1"
        self.mp.event_manager().event_attach(vlc.EventType.MediaPlayerMediaChanged, self._vlc_media_changed)
        self.track_changed.connect(lambda _: self.mp.audio_set_volume(self.volume))

        self._shortcuts = []
        self.bind_keys(main.ctrl.keymap)

    def bind_keys(self, mapping):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        actions = {
            "m_toggle": self.toggle, "m_next": self.next, "m_prev": self.previous,
            "m_vol_up": lambda: self.change_volume(5), "m_vol_down": lambda: self.change_volume(-5),
            "m_montage": lambda: self.main.montage_btn.toggle(),
        }
        for key, fn in actions.items():
            seq = mapping.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), self.main)
                sc.setContext(Qt.ApplicationShortcut)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    # --- dossier -----------------------------------------------------------
    def folder(self):
        serial = db.get_setting(self.conn, "music_serial")
        rel = db.get_setting(self.conn, "music_rel")
        if serial is None or rel is None:
            return None
        return volumes.resolve(serial, rel)

    def set_folder(self, path):
        root, rel = volumes.split_path(path)
        serial, _ = volumes.volume_info(root)
        if not serial:
            return 0
        db.set_setting(self.conn, "music_serial", serial)
        db.set_setting(self.conn, "music_rel", rel)
        return self.load()

    def load(self):
        """(Re)charge la liste des morceaux. Retourne le nombre de fichiers."""
        path = self.folder()
        files = []
        if path:
            for dirpath, _, names in os.walk(path, onerror=lambda e: None):
                for n in names:
                    if os.path.splitext(n)[1].lower() in AUDIO_EXT:
                        files.append(os.path.join(dirpath, n))
        files.sort(key=str.lower)
        if self.shuffle:
            random.shuffle(files)
        was_playing = self.is_playing()
        self.mlp.stop()
        self.files = files
        self.mlp.set_media_list(self.instance.media_list_new(files))
        if was_playing and files:
            self.mlp.play()
        self.state_changed.emit(self.is_playing())
        return len(files)

    # --- commandes ---------------------------------------------------------
    def is_playing(self):
        return bool(self.mp.is_playing())

    def toggle(self):
        if not self.files:
            if not self.load():
                self.main.status.setText("Musique : choisis d'abord un dossier.")
                return
        state = self.mp.get_state()
        if self.is_playing():
            self.mp.set_pause(1)
        elif state == vlc.State.Paused:
            self.mp.set_pause(0)
        else:
            self.mlp.play()
        self.mp.audio_set_volume(self.volume)
        playing = state != vlc.State.Playing
        self.state_changed.emit(playing)
        self.osd("Musique en pause" if not playing else "\u266A " + self.current_name())

    def pause(self):
        if self.is_playing():
            self.mp.set_pause(1)
            self.state_changed.emit(False)

    def next(self):
        if self.files:
            self.mlp.next()
            self.state_changed.emit(True)

    def previous(self):
        if self.files:
            self.mlp.previous()
            self.state_changed.emit(True)

    def set_volume(self, value):
        self.volume = max(0, min(100, int(value)))
        self.mp.audio_set_volume(self.volume)
        db.set_setting(self.conn, "music_volume", str(self.volume))

    def change_volume(self, delta):
        self.set_volume(self.volume + delta)
        self.osd("Musique %d %%" % self.volume)
        self.main.music_volume.blockSignals(True)
        self.main.music_volume.setValue(self.volume)
        self.main.music_volume.blockSignals(False)

    def set_shuffle(self, on):
        self.shuffle = bool(on)
        db.set_setting(self.conn, "music_shuffle", "1" if on else "0")
        if self.files:
            self.load()

    def current_path(self):
        media = self.mp.get_media()
        return mrl_to_path(media.get_mrl()) if media else None

    def time_s(self):
        t = self.mp.get_time()
        return t / 1000.0 if t is not None and t >= 0 else None

    def current_name(self):
        media = self.mp.get_media()
        return mrl_to_name(media.get_mrl()) if media else ""

    def osd(self, text):
        self.main.ctrl.each(lambda p: p.osd(text))

    def stop(self):
        self.mlp.stop()

    # appele depuis le fil de VLC : on ne fait qu'emettre un signal Qt
    def _vlc_media_changed(self, event):
        media = self.mp.get_media()
        self.track_changed.emit(mrl_to_name(media.get_mrl()) if media else "")
