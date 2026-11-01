"""Montage au rythme de la musique : les lecteurs changent de video sur les temps du morceau.

- chaque morceau est analyse une fois (tempo + temps), resultat garde dans data/beats
- chaque lecteur prepare l'extrait suivant en coulisse ; au bon temps, bascule instantanee
- un extrait dure au moins `min_dur` secondes ; quand plusieurs ecrans sont prets,
  c'est celui qui montre son extrait depuis le plus longtemps qui change (effet de montage)
"""
import bisect
import os
import random
import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from . import db
from .players import set_muted

try:
    from . import beats as beatlib
    HAS_NUMPY = True
except ImportError:
    beatlib = None
    HAS_NUMPY = False

from .config import find_tool


class BeatAnalyzer(QThread):
    """Analyse les morceaux en tache de fond : le morceau en cours d'abord, puis le reste du dossier."""
    done = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.urgent = None
        self.queue = []
        self.wake = threading.Event()

    def request(self, path, priority=False):
        if priority:
            self.urgent = path
        elif path not in self.queue:
            self.queue.append(path)
        self.wake.set()

    def stop(self):
        self.requestInterruption()
        self.wake.set()
        self.wait(5000)

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        while not self.isInterruptionRequested():
            path = self.urgent
            self.urgent = None
            if path is None and self.queue:
                path = self.queue.pop(0)
            if path is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            result = beatlib.load_cached(path)
            if result is None and ffmpeg and os.path.isfile(path):
                try:
                    result = beatlib.analyze(ffmpeg, path)
                except Exception:
                    result = None
            self.done.emit(path, result)


class Montage(QObject):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.music = main.music
        self.ctrl = main.ctrl
        g = lambda k, d: db.get_setting(self.conn, k, d)
        self.enabled = g("montage_on", "0") == "1"
        self.min_dur = float(g("montage_min", "4"))
        self.every = int(g("montage_every", "1"))            # 1, 2 ou 4 temps
        self.source = g("montage_source", "context")          # context | all
        self.mute_videos = g("montage_mute2", "0") == "1"      # par defaut : on garde le son des videos
        self.include_mosaics = g("montage_mosaics", "1") == "1"
        self.mosaic_mode = g("montage_mosaic_mode", "one")    # one | all
        self.beats = []
        self.downbeat = 0
        self.bpm = None
        self.track = None
        self.next_i = 0
        self._last_raw = None
        self._last_wall = 0.0
        self.analyzer = None
        if HAS_NUMPY:
            self.analyzer = BeatAnalyzer()
            self.analyzer.done.connect(self.on_analyzed)
            self.analyzer.start()
        self.music.track_changed.connect(lambda _: QTimer.singleShot(150, self.on_track))
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.was_active = False

    # --- reglages ------------------------------------------------------------------
    def save(self):
        s = lambda k, v: db.set_setting(self.conn, k, v)
        s("montage_on", "1" if self.enabled else "0")
        s("montage_min", str(self.min_dur))
        s("montage_every", str(self.every))
        s("montage_source", self.source)
        s("montage_mute2", "1" if self.mute_videos else "0")
        s("montage_mosaics", "1" if self.include_mosaics else "0")
        s("montage_mosaic_mode", self.mosaic_mode)

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.save()
        if on:
            self.queue_folder()
            self.on_track()
        self.apply_mute()

    def active(self):
        return self.enabled and self.music.is_playing()

    def apply_mute(self):
        on = self.active() and self.mute_videos
        for pw in self.players(all_tiles=True):
            want = on and (not getattr(pw, "is_tile", False) or self.include_mosaics)
            if pw.montage_mute != want:
                pw.montage_mute = want
                set_muted(pw.player, pw.muted or want)

    # --- morceaux ----------------------------------------------------------------------
    def queue_folder(self):
        if self.analyzer:
            for f in self.music.files[:400]:
                self.analyzer.request(f)

    def on_track(self):
        path = self.music.current_path()
        self.track = path
        self.beats, self.bpm, self.next_i = [], None, 0
        if path and self.analyzer and self.enabled:
            cached = beatlib.load_cached(path)
            if cached:
                self.on_analyzed(path, cached)
            else:
                self.analyzer.request(path, priority=True)
                self.main.status.setText("Montage : analyse du rythme du morceau…")

    def on_analyzed(self, path, result):
        if path != self.track or not result:
            return
        self.beats = result["beats"]
        self.downbeat = result.get("downbeat", 0)
        self.bpm = result.get("bpm")
        self.next_i = 0
        self.main.status.setText("Montage : %s BPM détectés" % self.bpm)

    # --- temps de la musique (lisse) ---------------------------------------------------------
    def music_time(self):
        raw = self.music.time_s()
        now = time.monotonic()
        if raw is None:
            return None
        if raw != self._last_raw:
            self._last_raw, self._last_wall = raw, now
            return raw
        return raw + min(1.0, now - self._last_wall)

    # --- choix des extraits ---------------------------------------------------------------
    def players(self, all_tiles=False):
        """Lecteurs simples + cases de mosaique (si le montage s'applique aux mosaiques)."""
        units = []
        for w in self.ctrl.players.values():
            if getattr(w, "is_mosaic", False):
                if self.include_mosaics or all_tiles:
                    units.extend(t for t in w.tiles if t.video_id)
            elif w.video_id:
                units.append(w)
        return units

    def pick_next(self, pw):
        ids = []
        if self.source == "context" and getattr(pw, "is_tile", False):
            ids = list(pw.mosaic.pool)
        elif self.source == "context":
            if pw.queue:
                ids = list(pw.queue)
            else:
                row = self.conn.execute("SELECT category FROM videos WHERE id=?", (pw.video_id,)).fetchone()
                if row:
                    ids = db.category_video_ids(self.conn, row["category"])
        if len(ids) < 2:
            ids = db.all_video_ids(self.conn)
        showing = {p.video_id for p in self.players()}
        choices = [i for i in ids if i not in showing] or ids
        for _ in range(10):
            vid = random.choice(choices)
            row, path = self.ctrl.resolve(vid)
            if path:
                d = row["duration"] or 0
                if d > self.min_dur * 2 + 10:
                    # moment au hasard dans les 80 derniers pourcents
                    start = random.uniform(0.2 * d, max(0.2 * d, d - self.min_dur * 2 - 5))
                else:
                    start = 0
                return row, path, start
        return None

    def prepare(self, pw):
        if pw.alt_ready or getattr(pw, "_preparing", False):
            return
        picked = self.pick_next(pw)
        if not picked:
            return
        row, path, start = picked
        pw._preparing = True
        pw.prepare_alt(row["id"], path, row["title"], start)
        QTimer.singleShot(1000, lambda p=pw: setattr(p, "_preparing", False))

    def cut(self, pw):
        if pw.swap_alt():
            self.prepare(pw)
            return True
        return False

    def force_cut(self, pw):
        """Fin d'extrait avant la coupe prevue : on bascule tout de suite."""
        if not self.cut(pw):
            picked = self.pick_next(pw)
            if picked:
                row, path, start = picked
                pw.load(row["id"], path, row["title"], start)

    # --- horloge -------------------------------------------------------------------------
    def tick(self):
        active = self.active()
        if active != self.was_active:
            self.was_active = active
            self.apply_mute()
        if not active:
            return
        players = self.players()
        if not players:
            return
        # preparation en coulisse : tous les lecteurs simples ; en mosaique (mode "une case"),
        # seulement les 2 cases les plus anciennes, pour ne pas surcharger le disque externe
        by_mosaic = {}
        for pw in players:
            if getattr(pw, "is_tile", False) and self.mosaic_mode == "one":
                by_mosaic.setdefault(id(pw.mosaic), []).append(pw)
            elif not pw.alt_ready:
                self.prepare(pw)
        for tiles in by_mosaic.values():
            for pw in sorted(tiles, key=lambda p: p.clip_start)[:2]:
                if not pw.alt_ready:
                    self.prepare(pw)
        now = time.monotonic()
        t = self.music_time()
        if not self.beats or t is None:
            # rythme pas encore connu : coupes a intervalle regulier
            for pw in players:
                if now - pw.clip_start >= self.min_dur * 1.5:
                    self.cut(pw)
                    break
            return
        # prochain temps retenu (1 temps sur `every`, cale sur le debut de mesure detecte)
        if self.next_i >= len(self.beats) or (self.next_i > 0 and self.beats[self.next_i - 1] > t + 0.5):
            self.next_i = bisect.bisect_left(self.beats, t)
        while self.next_i < len(self.beats) and (
                (self.next_i - self.downbeat) % self.every != 0 or self.beats[self.next_i] < t - 0.08):
            self.next_i += 1
        if self.next_i >= len(self.beats):
            return
        if t >= self.beats[self.next_i] - 0.015:
            self.next_i += 1
            ready = [p for p in players if p.alt_ready and now - p.clip_start >= self.min_dur]
            if not ready:
                return
            first = min(ready, key=lambda p: p.clip_start)
            if getattr(first, "is_tile", False) and self.mosaic_mode == "all":
                # toute la mosaique change d'un coup
                for p in ready:
                    if getattr(p, "is_tile", False) and p.mosaic is first.mosaic:
                        self.cut(p)
            else:
                self.cut(first)

    def stop(self):
        self.timer.stop()
        if self.analyzer:
            self.analyzer.stop()
