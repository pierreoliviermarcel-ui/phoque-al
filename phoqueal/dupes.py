"""Recherche de doublons et corbeille Phoque Al.

Doublons exacts : meme taille + meme empreinte (debut, milieu, fin du fichier : 3 x 1 Mo).
Verification complete optionnelle (fichier entier) juste avant suppression.
Doublons probables : videos de meme duree (au dixieme de seconde) mais de tailles differentes.
Suppression = deplacement dans <disque>\\_Corbeille_Focal\\... (meme disque, instantane, reversible).
"""
import hashlib
import os
import shutil

from PySide6.QtCore import QThread, Signal

from . import db, volumes

TRASH = "_Corbeille_Focal"
CHUNK = 1024 * 1024


def quick_hash(path, size):
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        for pos in (0, max(0, size // 2 - CHUNK // 2), max(0, size - CHUNK)):
            f.seek(pos)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def full_hash(path, stop=None):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            if stop and stop():
                return None
            block = f.read(8 * CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def entry_path(row, drives):
    root = volumes.resolve(row["serial"], row["rel_root"], drives)
    return os.path.join(root, row["rel_path"]) if root else None


class DupeScanner(QThread):
    progress = Signal(int, int, str)
    finished_groups = Signal(list, list)     # (exacts, probables)

    def run(self):
        conn = db.connect()
        drives = volumes.connected_drives()
        rows = conn.execute(
            "SELECT v.id, v.rel_path, v.size, v.mtime, v.duration, v.title, v.kind, v.play_count, "
            "s.serial, s.rel_root, s.label FROM videos v JOIN sources s ON s.id = v.source_id "
            "WHERE v.missing=0 AND v.archive IS NULL AND v.size > 0").fetchall()
        by_size = {}
        for r in rows:
            by_size.setdefault(r["size"], []).append(r)
        candidates = [r for g in by_size.values() if len(g) > 1 for r in g]
        exact = {}
        total = len(candidates)
        for i, r in enumerate(candidates, 1):
            if self.isInterruptionRequested():
                break
            path = entry_path(r, drives)
            if not path or not os.path.isfile(path):
                continue
            if i % 5 == 0 or i == total:
                self.progress.emit(i, total, r["title"])
            try:
                key = (r["size"], quick_hash(path, r["size"]))
            except OSError:
                continue
            exact.setdefault(key, []).append(dict(r, path=path))
        exact_groups = [g for g in exact.values() if len(g) > 1]
        exact_ids = {e["id"] for g in exact_groups for e in g}

        probable = {}
        for r in rows:
            if r["kind"] == "video" and r["duration"] and r["duration"] > 120 and r["id"] not in exact_ids:
                probable.setdefault(round(r["duration"], 1), []).append(r)
        probable_groups = []
        for g in probable.values():
            if len(g) > 1 and len({r["size"] for r in g}) > 1:
                items = []
                for r in g:
                    p = entry_path(r, drives)
                    if p and os.path.isfile(p):
                        items.append(dict(r, path=p))
                if len(items) > 1:
                    probable_groups.append(items)
        conn.close()
        exact_groups.sort(key=lambda g: -g[0]["size"] * (len(g) - 1))
        self.finished_groups.emit(exact_groups, probable_groups)


def trash_root(path):
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return os.path.join(drive + os.sep, TRASH) if drive else os.path.join(os.sep, TRASH)


def move_to_trash(path):
    """Deplace un fichier dans la corbeille Phoque Al de son disque. Retourne le nouveau chemin."""
    base = trash_root(path)
    rel = os.path.splitdrive(os.path.abspath(path))[1].lstrip("\\/")
    dest = os.path.join(base, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    stem, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = "%s (%d)%s" % (stem, n, ext)
        n += 1
    try:
        os.replace(path, dest)
    except OSError:
        shutil.move(path, dest)
    return dest


def forget_entry(conn, video_id):
    conn.execute("DELETE FROM playlist_items WHERE video_id=?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
    conn.commit()


def trash_folders():
    """Corbeilles Phoque Al presentes sur les disques branches : [(chemin, taille_octets, nb_fichiers)]."""
    out = []
    for root in volumes.connected_drives().values():
        folder = os.path.join(root, TRASH)
        if os.path.isdir(folder):
            size = count = 0
            for dp, _, files in os.walk(folder):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(dp, f))
                        count += 1
                    except OSError:
                        pass
            out.append((folder, size, count))
    return out


def human(n):
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return ("%.1f %s" % (n, unit)).replace(".0 ", " ") if unit != "o" else "%d o" % n
        n /= 1024.0
