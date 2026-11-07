"""Detection des changements de scene (FFmpeg, images cles uniquement : rapide).

- Page suivante / Page precedente dans le lecteur : scene suivante / precedente.
- Le montage au rythme fait demarrer ses extraits sur de vrais debuts de scene.
- Analyse a la demande (video lue) et, en option, de toute la bibliotheque en tache de fond.
"""
import bisect
import os
import random
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QThread, QTimer, Signal

from .. import db, volumes
from ..config import find_tool
from .common import current_time, log, owner

ACTIONS = [
    ("p_scene_next", "player", "Scène suivante", "PgDown"),
    ("p_scene_prev", "player", "Scène précédente", "PgUp"),
]
_FLAGS = 0x08000000 if sys.platform == "win32" else 0
THRESHOLD = 0.30
PTS = re.compile(r"pts_time:\s*([0-9.]+)")


def detect(ffmpeg, path, threshold=THRESHOLD, timeout=900):
    """Liste des instants (s) de changement de scene, en ne decodant que les images cles."""
    vf = "scale=128:-2,select='gt(scene\\,%.2f)',showinfo" % threshold
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-skip_frame", "nokey", "-i", path,
           "-an", "-sn", "-dn", "-vf", vf, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_FLAGS)
    err = p.stderr.decode("utf-8", "ignore")
    times = sorted({round(float(m.group(1)), 2) for m in PTS.finditer(err)})
    out = []
    for t in times:                                   # pas deux scenes a moins de 2 s
        if not out or t - out[-1] >= 2:
            out.append(t)
    return out


def scenes_of(conn, vid):
    return [r["t"] for r in conn.execute("SELECT t FROM scenes WHERE video_id=? ORDER BY t", (vid,))]


def is_done(conn, vid):
    return conn.execute("SELECT 1 FROM scenes_done WHERE video_id=?", (vid,)).fetchone() is not None


class SceneWorker(QThread):
    done = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.urgent = []
        self.background = False
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.current = None          # vidéo en cours d'analyse (panneau des traitements)

    def request(self, vid):
        if not vid or vid < 0:
            return
        with self.lock:
            if vid in self.urgent:
                self.urgent.remove(vid)
            self.urgent.insert(0, vid)
        self.wake.set()

    def stop(self):
        self.requestInterruption()
        self.wake.set()
        self.wait(3000)

    def next_id(self, conn):
        with self.lock:
            while self.urgent:
                vid = self.urgent.pop(0)
                if not is_done(conn, vid):
                    return vid
        if self.background:
            r = conn.execute("SELECT v.id FROM videos v LEFT JOIN scenes_done d ON d.video_id=v.id "
                             "WHERE d.video_id IS NULL AND v.missing=0 AND v.kind='video' AND v.archive IS NULL "
                             "ORDER BY v.play_count DESC, v.id LIMIT 1").fetchone()
            if r:
                return r["id"]
        return None

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return
        conn = db.connect()
        while not self.isInterruptionRequested():
            vid = self.next_id(conn)
            if vid is None:
                self.wake.wait(5)
                self.wake.clear()
                continue
            r = conn.execute("SELECT v.rel_path, s.serial, s.rel_root FROM videos v JOIN sources s "
                             "ON s.id=v.source_id WHERE v.id=?", (vid,)).fetchone()
            root = volumes.resolve(r["serial"], r["rel_root"]) if r else None
            if not root:
                conn.execute("INSERT OR REPLACE INTO scenes_done(video_id, n, done_at) VALUES (?,?,?)", (vid, -1, 0))
                conn.commit()
                continue
            self.current = vid
            try:
                times = detect(ffmpeg, os.path.join(root, r["rel_path"]))
            except Exception as e:
                log("scenes %s : %r" % (vid, e))
                times = []
            finally:
                self.current = None
            conn.execute("DELETE FROM scenes WHERE video_id=?", (vid,))
            conn.executemany("INSERT INTO scenes(video_id, t) VALUES (?,?)", [(vid, t) for t in times])
            conn.execute("INSERT OR REPLACE INTO scenes_done(video_id, n, done_at) VALUES (?,?,?)",
                         (vid, len(times), time.time()))
            conn.commit()
            self.done.emit(vid, len(times))
            if self.background and not self.urgent:
                self.wake.wait(2)                         # menage le disque externe
                self.wake.clear()
        conn.close()


WORKER = None


def jump(win, step):
    main = win.ctrl.main
    vid, t = current_time(win)
    o = owner(win)
    if not vid or vid < 0:
        return
    if not is_done(main.conn, vid):
        WORKER.request(vid)
        o.osd("Analyse des scènes en cours… réessaie dans quelques secondes", 2000)
        return
    times = scenes_of(main.conn, vid)
    if not times:
        o.osd("Aucun changement de scène détecté", 1500)
        return
    if step > 0:
        i = bisect.bisect_right(times, t + 1)
        if i >= len(times):
            o.osd("Dernière scène", 1200)
            return
    else:
        i = bisect.bisect_left(times, t - 3) - 1
        if i < 0:
            o.player.set_time(0)
            o.osd("Début de la vidéo", 1200)
            return
    o.player.set_time(int(times[i] * 1000))
    o.osd("Scène %d/%d" % (i + 1, len(times)), 1200)


PLAYER_KEYS = {"p_scene_next": lambda w: jump(w, 1), "p_scene_prev": lambda w: jump(w, -1)}
MOSAIC_KEYS = dict(PLAYER_KEYS)


def scene_start(conn, vid, duration):
    """Un debut de scene au hasard (20 % - 95 % de la video), ou None."""
    times = scenes_of(conn, vid)
    if duration:
        times = [t for t in times if 0.2 * duration <= t <= duration - 15]
    return random.choice(times) if times else None


def install(main):
    global WORKER
    WORKER = SceneWorker()
    WORKER.background = db.get_setting(main.conn, "scenes_background", "0") == "1"
    WORKER.start()
    main._scene_worker = WORKER

    from ..players import PlayerWindow
    if not getattr(PlayerWindow, "_extras_scenes", False):
        orig_load = PlayerWindow.load

        def load(self, video_id, path, title, start_s):
            r = orig_load(self, video_id, path, title, start_s)
            if video_id and video_id > 0 and WORKER is not None:
                QTimer.singleShot(3000, lambda v=video_id: WORKER.request(v))   # apres le demarrage
            return r

        PlayerWindow.load = load
        PlayerWindow._extras_scenes = True

    from ..montage import Montage
    if not getattr(Montage, "_extras_scenes", False):
        orig_pick = Montage.pick_next

        def pick_next(self, pw):
            res = orig_pick(self, pw)
            try:
                if res and db.get_setting(self.conn, "montage_scenes", "1") == "1":
                    row, path, start = res
                    vid = row["id"]
                    if vid and vid > 0:
                        t = scene_start(self.conn, vid, row["duration"])
                        if t is not None:
                            return row, path, t
                        WORKER.request(vid)
            except Exception as e:
                log("montage scenes : %r" % e)
            return res

        Montage.pick_next = pick_next
        Montage._extras_scenes = True

    old_close = main.closeEvent

    def close_event(event):
        try:
            WORKER.stop()
        except Exception:
            pass
        old_close(event)

    main.closeEvent = close_event


def set_background(main, on):
    db.set_setting(main.conn, "scenes_background", "1" if on else "0")
    if WORKER is not None:
        WORKER.background = bool(on)
        WORKER.wake.set()


def progress(conn):
    done = conn.execute("SELECT COUNT(*) AS n FROM scenes_done").fetchone()["n"]
    total = conn.execute("SELECT COUNT(*) AS n FROM videos WHERE missing=0 AND kind='video'").fetchone()["n"]
    return done, total
