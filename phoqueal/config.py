"""Chemins et reglages generaux de Phoque Al."""
import glob
import os
import shutil
from pathlib import Path

import sys as _sys

# Version .exe (PyInstaller) : le dossier de l'application est celui de Phoque Al.exe
if getattr(_sys, "frozen", False):
    APP_DIR = Path(_sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
THUMB_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "focal.db"

VIDEO_EXT = {
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob", ".divx",
}

THUMB_WIDTH = 480          # largeur des miniatures generees (px)
THUMB_POSITION = 0.15      # position de capture (15 % de la video)
ROW_LIMIT = 40             # nombre de vignettes par rangee sur l'accueil


def ensure_dirs():
    THUMB_DIR.mkdir(parents=True, exist_ok=True)


def find_tool(name):
    """Trouve ffmpeg / ffprobe : PATH, dossier tools de Phoque Al, ou installation winget."""
    found = shutil.which(name)
    if found:
        return found
    exe = name + ".exe"
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        APP_DIR / "tools" / exe,
        Path(local) / "Microsoft" / "WinGet" / "Links" / exe,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    if local:
        pattern = os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", "bin", exe)
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None
