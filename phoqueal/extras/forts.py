"""Passages forts : les moments les plus intenses d'une vidéo (mouvement à l'image + énergie du son).

- Analyse rapide (images réduites à 2 par seconde, son à 8 kHz), faite quand une vidéo est lue,
  et en option pour toute la bibliothèque (coups de cœur et mieux notées d'abord).
- Touche H dans le lecteur : passage fort suivant (Maj+H : précédent).
- Le montage au rythme, les séances et les PMV partent de préférence des passages forts.
"""
import bisect
import os
import random
import subprocess
import sys
import threading
import time

import numpy as np

from .. import db, volumes
from ..config import find_tool
from .common import current_time, log, owner, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
SCHEMA = """
CREATE TABLE IF NOT EXISTS hot(video_id INTEGER NOT NULL, t0 REAL NOT NULL, t1 REAL NOT NULL, score REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_hot_video ON hot(video_id, t0);
CREATE TABLE IF NOT EXISTS hot_done(video_id INTEGER PRIMARY KEY, n INTEGER, done_at REAL);
"""
ACTIONS = [
    ("p_hot_next", "player", "Passage fort suivant", "H"),
    ("p_hot_prev", "player", "Passage fort précédent", "Shift+H"),
]
FPS = 2
W, H = 64, 36
WORKER = None


# ----------------------------------------------------------------------------------------
# Analyse (pure + FFmpeg)
# ----------------------------------------------------------------------------------------
def motion_curve(ffmpeg, path):
    """Mouvement par seconde (différence moyenne entre images successives, 2 images/s)."""
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", path, "-an",
                          "-vf", "fps=%d,scale=%d:%d,format=gray" % (FPS, W, H), "-f", "rawvideo", "-"],
                         capture_output=True, timeout=1800, creationflags=_FLAGS).stdout
    n = len(out) // (W * H)
    if n < 4:
        return np.zeros(0)
    fr = np.frombuffer(out[:n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float32)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    d = np.concatenate([[d[0]], d])
    # coupes franches (changement de plan) : on les ecrete pour ne pas les confondre avec de l'action
    cap = np.percentile(d, 97) if len(d) > 20 else d.max()
    d = np.minimum(d, cap)
    secs = n // FPS
    return d[:secs * FPS].reshape(secs, FPS).mean(axis=1)


def audio_curve(ffmpeg, path, secs):
    sr = 8000
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True, timeout=1800, creationflags=_FLAGS).stdout
    x = np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0
    if len(x) < sr:
        return np.zeros(secs)
    m = min(secs, len(x) // sr)
    rms = np.sqrt((x[:m * sr].reshape(m, sr) ** 2).mean(axis=1))
    return np.pad(rms, (0, secs - m))


def zs(v):
    if len(v) == 0:
        return v
    s = v.std()
    return (v - np.median(v)) / (s + 1e-9)


def find_passages(motion, audio, win=12, top=6, min_gap=20):
    """-> [(t0, t1, score)] : fenêtres de `win` secondes les plus intenses, sans chevauchement."""
    n = len(motion)
    if n < win * 2:
        return []
    a = audio[:n] if len(audio) >= n else np.pad(audio, (0, n - len(audio)))
    sc = 0.65 * zs(motion) + 0.35 * zs(a)
    k = np.ones(win) / win
    w = np.convolve(sc, k, mode="valid")                       # score moyen de la fenetre qui commence en i
    # on ignore les 3 premiers pourcents (generiques, logos)
    skip = int(n * 0.03)
    w[:skip] = -1e9
    out = []
    order = np.argsort(-w)
    taken = []
    for i in order:
        if len(out) >= top or w[i] < 0.3:
            break
        if any(abs(i - j) < min_gap for j in taken):
            continue
        taken.append(int(i))
        out.append((float(i), float(i + win), round(float(w[i]), 3)))
    return sorted(out)


def analyse(ffmpeg, path):
    m = motion_curve(ffmpeg, path)
    if len(m) == 0:
        return []
    a = audio_curve(ffmpeg, path, len(m))
    return find_passages(m, a)


# ----------------------------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------------------------
def passages(conn, vid):
    try:
        return [(r[0], r[1], r[2]) for r in conn.execute("SELECT t0, t1, score FROM hot WHERE video_id=? ORDER BY t0", (vid,))]
    except Exception:
        return []


def is_done(conn, vid):
    try:
        return conn.execute("SELECT 1 FROM hot_done WHERE video_id=?", (vid,)).fetchone() is not None
    except Exception:
        return False


def hot_start(conn, vid):
    p = passages(conn, vid)
    if not p:
        return None
    w = np.array([max(0.05, s) for _a, _b, s in p])
    t0 = p[int(np.random.choice(len(p), p=w / w.sum()))][0]
    return max(0.0, t0 + random.uniform(-1.0, 2.0))


class Worker:
    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.background = False
        self.stopping = False
        self.thread = threading.Thread(target=lambda: safe(self.run), daemon=True)
        self.done_n = 0
        self.current = None          # vidéo en cours d'analyse (panneau des traitements)

    def start(self):
        self.thread.start()

    def request(self, vid, front=True):
        with self.lock:
            if vid in self.queue:
                self.queue.remove(vid)
            self.queue.insert(0, vid) if front else self.queue.append(vid)
        self.wake.set()

    def next_id(self, conn):
        with self.lock:
            while self.queue:
                v = self.queue.pop(0)
                if not is_done(conn, v):
                    return v
        if self.background:
            r = conn.execute("SELECT v.id FROM videos v LEFT JOIN hot_done h ON h.video_id=v.id "
                             "WHERE h.video_id IS NULL AND v.missing=0 AND v.kind='video' "
                             "ORDER BY v.liked DESC, COALESCE(v.rating,0) DESC, v.play_count DESC LIMIT 1").fetchone()
            if r:
                return r[0]
        return None

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout=8000")
        conn.executescript(SCHEMA)
        while not self.stopping:
            vid = self.next_id(conn)
            if vid is None:
                self.wake.wait(5)
                self.wake.clear()
                continue
            r = conn.execute("SELECT v.rel_path, s.serial, s.rel_root FROM videos v JOIN sources s "
                             "ON s.id=v.source_id WHERE v.id=?", (vid,)).fetchone()
            root = volumes.resolve(r["serial"], r["rel_root"]) if r else None
            path = os.path.join(root, r["rel_path"]) if root else None
            if not path or not os.path.isfile(path):
                # disque debranche : on reessaiera plus tard (rien n'est enregistre)
                time.sleep(10)
                continue
            self.current = vid
            try:
                found = analyse(ffmpeg, path)
            except Exception as e:
                log("passages forts %s : %r" % (vid, e))
                found = []
            finally:
                self.current = None
            conn.execute("DELETE FROM hot WHERE video_id=?", (vid,))
            conn.executemany("INSERT INTO hot(video_id, t0, t1, score) VALUES (?,?,?,?)",
                             [(vid, a, b, s) for a, b, s in found])
            conn.execute("INSERT OR REPLACE INTO hot_done(video_id, n, done_at) VALUES (?,?,?)",
                         (vid, len(found), time.time()))
            conn.commit()
            self.done_n += 1


# ----------------------------------------------------------------------------------------
# Lecteur
# ----------------------------------------------------------------------------------------
def jump(win, step):
    main = win.ctrl.main
    vid, t = current_time(win)
    o = owner(win)
    if not vid or vid < 0:
        return
    if not is_done(main.conn, vid):
        WORKER.request(vid)
        o.osd("Recherche des passages forts… réessaie dans une minute", 2000)
        return
    p = passages(main.conn, vid)
    if not p:
        o.osd("Pas de passage fort net dans cette vidéo", 1500)
        return
    starts = [a for a, _b, _s in p]
    if step > 0:
        i = bisect.bisect_right(starts, t + 2)
        if i >= len(starts):
            i = 0
    else:
        i = bisect.bisect_left(starts, t - 4) - 1
        if i < 0:
            i = len(starts) - 1
    o.player.set_time(int(starts[i] * 1000))
    o.osd("Passage fort %d/%d" % (i + 1, len(starts)), 1300)


PLAYER_KEYS = {"p_hot_next": lambda w: jump(w, 1), "p_hot_prev": lambda w: jump(w, -1)}
MOSAIC_KEYS = dict(PLAYER_KEYS)


def menu(main, m):
    sub = m.addMenu("Passages forts")
    try:
        done = main.conn.execute("SELECT COUNT(*) FROM hot_done").fetchone()[0]
        tot = main.conn.execute("SELECT COUNT(*) FROM videos WHERE missing=0 AND kind='video'").fetchone()[0]
    except Exception:
        done, tot = 0, 0
    a = sub.addAction("Analysées : %d / %d vidéos (H / Maj+H dans le lecteur)" % (done, tot))
    a.setEnabled(False)
    bg = sub.addAction("Analyser toute la bibliothèque en tâche de fond (coups de cœur d'abord)")
    bg.setCheckable(True)
    bg.setChecked(db.get_setting(main.conn, "hot_background", "0") == "1")

    def set_bg(on):
        db.set_setting(main.conn, "hot_background", "1" if on else "0")
        if WORKER:
            WORKER.background = bool(on)
            WORKER.wake.set()
    bg.triggered.connect(set_bg)
    ms = sub.addAction("Montage, séances et PMV partent des passages forts")
    ms.setCheckable(True)
    ms.setChecked(db.get_setting(main.conn, "hot_montage", "1") == "1")
    ms.triggered.connect(lambda on: db.set_setting(main.conn, "hot_montage", "1" if on else "0"))


def install(main):
    global WORKER
    main.conn.executescript(SCHEMA)
    main.conn.commit()
    WORKER = Worker()
    WORKER.background = db.get_setting(main.conn, "hot_background", "0") == "1"
    WORKER.start()

    from PySide6.QtCore import QTimer
    from ..players import PlayerWindow
    if not getattr(PlayerWindow, "_extras_hot", False):
        orig_load = PlayerWindow.load

        def load(self, video_id, path, title, start_s):
            r = orig_load(self, video_id, path, title, start_s)
            if video_id and video_id > 0 and WORKER is not None:
                QTimer.singleShot(6000, lambda v=video_id: WORKER.request(v, front=False))
            return r
        PlayerWindow.load = load
        PlayerWindow._extras_hot = True

    from ..montage import Montage
    if not getattr(Montage, "_extras_hot", False):
        orig_pick = Montage.pick_next

        def pick_next(self, pw):
            res = orig_pick(self, pw)
            try:
                if res and db.get_setting(self.conn, "hot_montage", "1") == "1" and random.random() < 0.7:
                    row, path, start = res
                    t = hot_start(self.conn, row["id"])
                    if t is not None:
                        return row, path, t
            except Exception as e:
                log("montage passages forts : %r" % e)
            return res
        Montage.pick_next = pick_next
        Montage._extras_hot = True

    old_close = main.closeEvent

    def close_event(event):
        try:
            WORKER.stopping = True
            WORKER.wake.set()
        except Exception:
            pass
        old_close(event)
    main.closeEvent = close_event
