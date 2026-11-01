"""Taches de fond : scan des sources et generation des miniatures."""
import os

from PySide6.QtCore import QThread, Signal

from . import db, volumes
from . import photos
from .config import ROW_LIMIT, THUMB_DIR, THUMB_WIDTH, find_tool
from .scanner import scan_source
from .thumbs import make_thumb


class ScanWorker(QThread):
    progress = Signal(str)
    done = Signal(str)

    def run(self):
        conn = db.connect()
        drives = volumes.connected_drives()
        excluded = db.excluded_folders(conn)
        photos_on = db.get_setting(conn, "photos_on", "1") == "1"
        report = []
        for src in db.list_sources(conn):
            if self.isInterruptionRequested():
                break
            path = volumes.resolve(src["serial"], src["rel_root"], drives)
            if not path:
                report.append("%s : disque absent" % src["name"])
                continue
            self.progress.emit("Analyse de %s..." % src["name"])
            new, changed, missing = scan_source(
                conn, src, path, self.progress.emit, self.isInterruptionRequested,
                excluded.get(src["id"]), photos_on)
            report.append("%s : %d nouveaux, %d modifiés, %d introuvables"
                          % (src["name"], new, changed, missing))
        conn.close()
        self.done.emit(" | ".join(report) if report else "Aucune source. Ajoute un dossier.")


class ThumbWorker(QThread):
    thumb_ready = Signal(int, str)
    progress = Signal(str)
    done = Signal()

    def run(self):
        ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        if not ffmpeg:
            self.progress.emit("FFmpeg introuvable : miniatures désactivées")
            self.done.emit()
            return
        conn = db.connect()
        drives = volumes.connected_drives()
        roots = {}
        for src in db.list_sources(conn):
            roots[src["id"]] = volumes.resolve(src["serial"], src["rel_root"], drives)
        # Ordre : d'abord les vignettes visibles sur l'accueil, une par rangee a tour de role,
        # pour que toutes les rangees se remplissent en meme temps.
        rows = conn.execute(
            "SELECT id, source_id, rel_path, kind, archive FROM ("
            "  SELECT id, source_id, rel_path, kind, archive, thumb, thumb_failed, missing, category, "
            "         ROW_NUMBER() OVER (PARTITION BY category ORDER BY title COLLATE NOCASE) AS rn "
            "  FROM videos WHERE missing=0"
            ") WHERE thumb IS NULL AND thumb_failed=0 "
            "ORDER BY rn > ?, rn, category COLLATE NOCASE", (ROW_LIMIT,)).fetchall()
        total = len(rows)
        for i, r in enumerate(rows, 1):
            if self.isInterruptionRequested():
                break
            root = roots.get(r["source_id"])
            if not root:
                continue
            if r["kind"] == "photo":
                thumb, duration = self.photo_thumb(r, root), None
            else:
                path = os.path.join(root, r["rel_path"])
                thumb, duration = make_thumb(ffmpeg, ffprobe, r["id"], path)
            if thumb:
                conn.execute("UPDATE videos SET thumb=?, duration=? WHERE id=?", (thumb, duration, r["id"]))
                self.thumb_ready.emit(r["id"], thumb)
            else:
                conn.execute("UPDATE videos SET thumb_failed=1, duration=? WHERE id=?", (duration, r["id"]))
            conn.commit()
            if i % 10 == 0 or i == total:
                self.progress.emit("Miniatures : %d / %d" % (i, total))
        self._close_archive()
        conn.close()
        self.done.emit()

    # --- photos ------------------------------------------------------------------
    _arc = None
    _arc_path = None

    def _close_archive(self):
        if self._arc is not None:
            try:
                self._arc.close()
            except Exception:
                pass
        self._arc, self._arc_path = None, None

    def photo_thumb(self, r, root):
        out = THUMB_DIR / ("%d.jpg" % r["id"])
        try:
            if r["archive"]:
                arc_path = os.path.join(root, r["archive"])
                if arc_path != self._arc_path:
                    self._close_archive()
                    self._arc, self._arc_path = photos.open_archive(arc_path), arc_path
                data = self._arc.read(photos.split_rel(r["rel_path"])[1])
                ok = photos.make_thumb(out, THUMB_WIDTH, data=data)
            else:
                ok = photos.make_thumb(out, THUMB_WIDTH, file_path=os.path.join(root, r["rel_path"]))
        except Exception:
            ok = False
        return str(out) if ok else None
