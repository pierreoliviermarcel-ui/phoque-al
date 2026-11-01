"""Generation des miniatures avec FFmpeg (une a la fois pour menager le disque)."""
import subprocess
import sys

from .config import THUMB_DIR, THUMB_POSITION, THUMB_WIDTH

_FLAGS = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW


def probe_duration(ffprobe, path):
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60, creationflags=_FLAGS)
        return float(out.stdout.strip())
    except Exception:
        return None


def make_thumb(ffmpeg, ffprobe, video_id, path):
    """Retourne (chemin_miniature ou None, duree ou None)."""
    duration = probe_duration(ffprobe, path) if ffprobe else None
    at = duration * THUMB_POSITION if duration else 5.0
    out = THUMB_DIR / ("%d.jpg" % video_id)
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % at, "-i", path,
             "-frames:v", "1", "-vf", "scale=%d:-2" % THUMB_WIDTH, "-q:v", "4", "-y", str(out)],
            capture_output=True, timeout=120, creationflags=_FLAGS)
    except Exception:
        return None, duration
    if out.is_file() and out.stat().st_size > 0:
        return str(out), duration
    return None, duration
