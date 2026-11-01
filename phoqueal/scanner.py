"""Scan incremental des dossiers sources (videos, photos, archives de photos)."""
import os
import re
import time
from pathlib import PurePath

from .config import VIDEO_EXT
from .photos import ARCHIVE_EXT, IMAGE_EXT, MIN_SIZE, SEP, list_images


def clean_title(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"[._]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip() or filename


def scan_source(conn, source, root_path, progress=None, should_stop=None, excluded=None,
                include_photos=True):
    """Synchronise une source avec la base. Retourne (nouveaux, modifies, absents)."""
    existing = {
        r["rel_path"]: (r["id"], r["size"], r["mtime"])
        for r in conn.execute(
            "SELECT id, rel_path, size, mtime FROM videos WHERE source_id=?", (source["id"],))
    }
    # date de chaque archive deja connue (pour ne pas la rouvrir si elle n'a pas change)
    archive_mtime = {}
    for rel, (_, _, mt) in existing.items():
        if SEP in rel:
            archive_mtime.setdefault(rel.split(SEP, 1)[0], mt)
    seen = set()
    counters = {"new": 0, "changed": 0, "pending": 0}
    now = time.time()

    def upsert(rel, kind, category, title, tags, size, mtime, archive=None):
        seen.add(rel)
        old = existing.get(rel)
        if old is None:
            conn.execute(
                "INSERT INTO videos(source_id, rel_path, category, title, tags, size, mtime, added_at, kind, archive) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (source["id"], rel, category, title, tags, size, mtime, now, kind, archive))
            counters["new"] += 1
            counters["pending"] += 1
        elif old[1] != size or abs((old[2] or 0) - mtime) > 1:
            conn.execute(
                "UPDATE videos SET size=?, mtime=?, duration=NULL, thumb=NULL, thumb_failed=0, "
                "category=?, title=?, tags=?, missing=0, kind=?, archive=? WHERE id=?",
                (size, mtime, category, title, tags, kind, archive, old[0]))
            counters["changed"] += 1
            counters["pending"] += 1
        if counters["pending"] >= 300:
            conn.commit()
            counters["pending"] = 0
            if progress:
                progress("%s : %d nouveaux éléments…" % (source["name"], counters["new"]))

    for dirpath, dirnames, filenames in os.walk(root_path, onerror=lambda e: None):
        if should_stop and should_stop():
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(("$", "."))
                       and d not in ("System Volume Information", "_Corbeille_Focal")]
        if excluded and os.path.normcase(os.path.abspath(dirpath)) == os.path.normcase(os.path.abspath(root_path)):
            dirnames[:] = [d for d in dirnames if d not in excluded]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            is_video = ext in VIDEO_EXT
            is_photo = include_photos and ext in IMAGE_EXT
            is_archive = include_photos and ext in ARCHIVE_EXT
            if not (is_video or is_photo or is_archive):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_path)
            parts = PurePath(rel).parts
            folder_tags = " ".join(parts[:-1])
            if is_video:
                category = parts[0] if len(parts) > 1 else source["name"]
                upsert(rel, "video", category, clean_title(fn), folder_tags, st.st_size, st.st_mtime)
            elif is_photo:
                if st.st_size < MIN_SIZE:
                    continue
                # album = dossier de la photo (ou la source pour les photos a la racine)
                album = parts[-2] if len(parts) > 1 else source["name"]
                upsert(rel, "photo", album, clean_title(fn), folder_tags + " photo", st.st_size, st.st_mtime)
            else:
                album = clean_title(fn)
                known = archive_mtime.get(rel)
                if known is not None and abs(known - st.st_mtime) <= 1:
                    # archive inchangee : on garde ses photos sans l'ouvrir
                    seen.update(k for k in existing if k.startswith(rel + SEP))
                    continue
                if progress:
                    progress("Lecture de l'archive %s…" % fn)
                try:
                    members = list_images(full)
                except Exception:
                    members = []
                for name, size in members:
                    upsert(rel + SEP + name, "photo", album, clean_title(name),
                           "%s %s photo" % (folder_tags, album), size, st.st_mtime, archive=rel)

    stopped = bool(should_stop and should_stop())
    missing = 0
    if not stopped:
        gone = [v[0] for k, v in existing.items() if k not in seen]
        missing = len(gone)
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            conn.execute("UPDATE videos SET missing=1 WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)
        back = [existing[k][0] for k in seen if k in existing]
        for i in range(0, len(back), 500):
            chunk = back[i:i + 500]
            conn.execute("UPDATE videos SET missing=0 WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)
    conn.commit()
    return counters["new"], counters["changed"], missing
