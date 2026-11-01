"""Apercus animes au survol : 8 images reparties dans la video, collees en une bande.

Generation a la demande (survol, prioritaire) puis en tache de fond pour tout le reste.
Les bandes sont gardees dans data/previews : la deuxieme fois, l'apercu est instantane.
"""
import os
import subprocess
import threading

from PySide6.QtCore import QThread, Signal

from . import db, volumes
from .config import DATA_DIR, ROW_LIMIT, find_tool
from .thumbs import _FLAGS, probe_duration

PREVIEW_DIR = DATA_DIR / "previews"
CLIP_DIR = DATA_DIR / "clips"
FRAMES = 8
FW, FH = 320, 180
CLIP_PARTS = 5          # extraits video : 5 passages...
CLIP_SECONDS = 2.0      # ...de 2 secondes chacun


def preview_path(video_id):
    return PREVIEW_DIR / ("%d.jpg" % video_id)


def clip_path(video_id):
    return CLIP_DIR / ("%d.mp4" % video_id)


def target_path(video_id, mode):
    return clip_path(video_id) if mode == "video" else preview_path(video_id)


def make_clip(ffmpeg, ffprobe, video_id, path, duration):
    """Petit extrait muet de 10 s (5 x 2 s repartis dans la video), 320x180."""
    if not duration and ffprobe:
        duration = probe_duration(ffprobe, path)
    if not duration or duration < CLIP_PARTS * CLIP_SECONDS * 1.5:
        return None
    out = clip_path(video_id)
    tmp = out.with_suffix(".part.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for i in range(CLIP_PARTS):
        t = duration * (0.08 + 0.80 * i / (CLIP_PARTS - 1))
        cmd += ["-ss", "%.2f" % t, "-t", "%.2f" % CLIP_SECONDS, "-i", path]
    chains = [
        "[%d:v:0]scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25[v%d]" % (i, FW, FH, FW, FH, i)
        for i in range(CLIP_PARTS)]
    graph = ";".join(chains) + ";" + "".join("[v%d]" % i for i in range(CLIP_PARTS)) \
        + "concat=n=%d:v=1:a=0[out]" % CLIP_PARTS
    cmd += ["-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(tmp)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300, creationflags=_FLAGS)
    except Exception:
        return None
    if tmp.is_file() and tmp.stat().st_size > 0:
        os.replace(tmp, out)
        return str(out)
    return None


def make_preview(ffmpeg, ffprobe, video_id, path, duration):
    if not duration and ffprobe:
        duration = probe_duration(ffprobe, path)
    if not duration or duration < 4:
        return None
    out = preview_path(video_id)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for i in range(FRAMES):
        t = duration * (0.06 + 0.86 * i / (FRAMES - 1))
        cmd += ["-ss", "%.2f" % t, "-i", path]
    chains = [
        "[%d:v:0]scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1[v%d]" % (i, FW, FH, FW, FH, i)
        for i in range(FRAMES)]
    graph = ";".join(chains) + ";" + "".join("[v%d]" % i for i in range(FRAMES)) \
        + "hstack=inputs=%d[out]" % FRAMES
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "5", "-y", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180, creationflags=_FLAGS)
    except Exception:
        return None
    return str(out) if out.is_file() and out.stat().st_size > 0 else None


class PreviewService(QThread):
    """Un seul fil de travail : la demande de survol passe toujours avant le fond."""
    ready = Signal(int, str)

    def __init__(self, mode="images"):
        super().__init__()
        self.mode = mode
        self._urgent = None
        self._background = False
        self._wake = threading.Event()
        self._failed = set()

    def request(self, video_id):
        self._urgent = video_id
        self._wake.set()

    def start_background(self):
        self._background = True
        self._wake.set()

    def stop(self):
        self.requestInterruption()
        self._wake.set()
        self.wait(5000)

    def _background_ids(self, conn):
        rows = conn.execute(
            "SELECT id FROM ("
            "  SELECT id, ROW_NUMBER() OVER (PARTITION BY category ORDER BY title COLLATE NOCASE) AS rn, category "
            "  FROM videos WHERE missing=0 AND kind='video'"
            ") ORDER BY rn > ?, rn, category COLLATE NOCASE", (ROW_LIMIT,)).fetchall()
        return [r["id"] for r in rows if not target_path(r["id"], self.mode).is_file()]

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self._failed.clear()
            self._reset_queue = True
            self._wake.set()

    def run(self):
        ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        if not ffmpeg:
            return
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        CLIP_DIR.mkdir(parents=True, exist_ok=True)
        conn = db.connect()
        drives = volumes.connected_drives()
        queue = None
        self._reset_queue = False
        while not self.isInterruptionRequested():
            if self._reset_queue:
                queue, self._reset_queue = None, False
            mode = self.mode
            vid, urgent = self._urgent, True
            self._urgent = None
            if vid is None and self._background:
                if queue is None:
                    queue = self._background_ids(conn)
                while queue and vid is None:
                    cand = queue.pop(0)
                    if not target_path(cand, mode).is_file() and cand not in self._failed:
                        vid, urgent = cand, False
            if vid is None:
                self._wake.wait(2)
                self._wake.clear()
                continue
            out = target_path(vid, mode)
            if out.is_file():
                self.ready.emit(vid, str(out))
                continue
            if vid in self._failed:
                continue
            row = conn.execute(
                "SELECT v.rel_path, v.duration, s.serial, s.rel_root FROM videos v "
                "JOIN sources s ON s.id = v.source_id WHERE v.id=?", (vid,)).fetchone()
            if not row:
                continue
            root = volumes.resolve(row["serial"], row["rel_root"], drives)
            if not root:
                drives = volumes.connected_drives()
                continue
            maker = make_clip if mode == "video" else make_preview
            made = maker(ffmpeg, ffprobe, vid, os.path.join(root, row["rel_path"]), row["duration"])
            if made:
                self.ready.emit(vid, made)
            else:
                self._failed.add(vid)
        conn.close()
