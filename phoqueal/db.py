"""Base de donnees SQLite de Phoque Al (une connexion par thread)."""
import re
import sqlite3

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
    id INTEGER PRIMARY KEY,
    serial TEXT NOT NULL,
    label TEXT,
    rel_root TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(serial, rel_root)
);
CREATE TABLE IF NOT EXISTS videos(
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    rel_path TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    size INTEGER,
    mtime REAL,
    duration REAL,
    thumb TEXT,
    thumb_failed INTEGER NOT NULL DEFAULT 0,
    added_at REAL,
    missing INTEGER NOT NULL DEFAULT 0,
    position REAL NOT NULL DEFAULT 0,
    play_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, rel_path)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS playlists(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    loop INTEGER NOT NULL DEFAULT 0,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS playlist_items(
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id),
    video_id INTEGER NOT NULL REFERENCES videos(id),
    pos INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pl_items ON playlist_items(playlist_id, pos);
CREATE TABLE IF NOT EXISTS mosaics(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    screen INTEGER,
    config TEXT NOT NULL,
    state TEXT,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category);
CREATE INDEX IF NOT EXISTS idx_videos_added ON videos(added_at);
CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    title, tags, content='videos', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts(rowid, title, tags) VALUES (new.id, new.title, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, title, tags) VALUES ('delete', old.id, old.title, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE OF title, tags ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, title, tags) VALUES ('delete', old.id, old.title, old.tags);
    INSERT INTO videos_fts(rowid, title, tags) VALUES (new.id, new.title, new.tags);
END;
"""


def connect():
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "last_played" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN last_played REAL")
    if "liked" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN liked INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE videos ADD COLUMN liked_at REAL")
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(playlists)")}
    if "system" not in pcols:
        conn.execute("ALTER TABLE playlists ADD COLUMN system TEXT")
    if "kind" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'")
        conn.execute("ALTER TABLE videos ADD COLUMN archive TEXT")
    conn.commit()
    return conn


# --- sources -----------------------------------------------------------------

def add_source(conn, serial, label, rel_root, name):
    conn.execute(
        "INSERT OR IGNORE INTO sources(serial, label, rel_root, name) VALUES (?,?,?,?)",
        (serial, label, rel_root, name))
    conn.commit()


def list_sources(conn):
    return conn.execute("SELECT * FROM sources ORDER BY name").fetchall()


# --- lecture de la bibliotheque ----------------------------------------------

# --- photos : melangees aux videos ou non --------------------------------------------

def mix_photos(conn):
    return get_setting(conn, "mix_photos", "0") == "1"


def kind_filter(conn, alias=""):
    """Filtre SQL : videos seules, sauf si l'option « melanger photos et videos » est active."""
    return "" if mix_photos(conn) else " AND %skind='video'" % alias


def categories(conn):
    return conn.execute(
        "SELECT category, COUNT(*) AS n FROM videos WHERE missing=0%s "
        "GROUP BY category ORDER BY category COLLATE NOCASE" % kind_filter(conn)).fetchall()


def recent(conn, limit):
    return conn.execute(
        "SELECT * FROM videos WHERE missing=0%s ORDER BY added_at DESC, id DESC LIMIT ?" % kind_filter(conn),
        (limit,)).fetchall()


def photo_albums(conn):
    return conn.execute(
        "SELECT category, COUNT(*) AS n, "
        "  (SELECT thumb FROM videos w WHERE w.category = v.category AND w.kind='photo' AND w.missing=0 "
        "   AND w.thumb IS NOT NULL ORDER BY w.rel_path LIMIT 1) AS cover "
        "FROM videos v WHERE kind='photo' AND missing=0 "
        "GROUP BY category ORDER BY category COLLATE NOCASE").fetchall()


def photo_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM videos WHERE kind='photo' AND missing=0").fetchone()["n"]


def album_photos(conn, category):
    return conn.execute("SELECT * FROM videos WHERE kind='photo' AND missing=0 AND category=? "
                        "ORDER BY rel_path COLLATE NOCASE", (category,)).fetchall()


def by_category(conn, category, limit=None):
    sql = ("SELECT * FROM videos WHERE missing=0 AND category=?%s ORDER BY title COLLATE NOCASE"
           % kind_filter(conn))
    if limit:
        return conn.execute(sql + " LIMIT ?", (category, limit)).fetchall()
    return conn.execute(sql, (category,)).fetchall()


def search(conn, text, limit=500):
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if not words:
        return []
    query = " ".join('"%s"*' % w for w in words)
    return conn.execute(
        "SELECT v.* FROM videos v JOIN videos_fts f ON f.rowid = v.id "
        "WHERE videos_fts MATCH ? AND v.missing=0" + kind_filter(conn, "v.") + " ORDER BY rank LIMIT ?",
        (query, limit)).fetchall()


def count(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(thumb IS NOT NULL) AS t FROM videos WHERE missing=0").fetchone()
    return row["n"] or 0, row["t"] or 0


# --- reglages et mise en avant -------------------------------------------------

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, value))
    conn.commit()


def favorite_category(conn):
    """Theme favori choisi par l'utilisateur, sinon le plus regarde. Retourne (nom, choisi?)."""
    fav = get_setting(conn, "favorite_category")
    if fav and conn.execute("SELECT 1 FROM videos WHERE category=? AND missing=0 LIMIT 1", (fav,)).fetchone():
        return fav, True
    row = conn.execute(
        "SELECT category, SUM(play_count) AS p FROM videos WHERE missing=0 AND kind='video' "
        "GROUP BY category HAVING p > 0 ORDER BY p DESC LIMIT 1").fetchone()
    return (row["category"], False) if row else (None, False)


def random_category(conn, exclude=None):
    row = conn.execute(
        "SELECT category FROM videos WHERE missing=0 AND kind='video' AND category IS NOT ? "
        "GROUP BY category ORDER BY RANDOM() LIMIT 1", (exclude,)).fetchone()
    return row["category"] if row else None


def random_video(conn, category):
    return conn.execute(
        "SELECT * FROM videos WHERE missing=0 AND kind='video' AND category=? "
        "ORDER BY (thumb IS NULL), RANDOM() LIMIT 1", (category,)).fetchone()


def category_count(conn, category):
    return conn.execute("SELECT COUNT(*) AS n FROM videos WHERE missing=0 AND category=?" + kind_filter(conn),
                        (category,)).fetchone()["n"]


def remove_source(conn, source_id):
    conn.execute("DELETE FROM playlist_items WHERE video_id IN (SELECT id FROM videos WHERE source_id=?)",
                 (source_id,))
    conn.execute("DELETE FROM videos WHERE source_id=?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.commit()


def excluded_folders(conn):
    """{source_id: set(noms de sous-dossiers exclus)}"""
    import json
    try:
        raw = json.loads(get_setting(conn, "excluded", "{}") or "{}")
    except ValueError:
        raw = {}
    return {int(k): set(v) for k, v in raw.items()}


def set_excluded_folders(conn, mapping):
    import json
    clean = {str(k): sorted(v) for k, v in mapping.items() if v}
    set_setting(conn, "excluded", json.dumps(clean) if clean else None)


def folder_counts(conn, source_id):
    return {r["category"]: r["n"] for r in conn.execute(
        "SELECT category, COUNT(*) AS n FROM videos WHERE source_id=? GROUP BY category", (source_id,))}


# --- playlists -------------------------------------------------------------------

def create_playlist(conn, name):
    import time
    cur = conn.execute("INSERT INTO playlists(name, created_at) VALUES (?,?)", (name, time.time()))
    conn.commit()
    return cur.lastrowid


def list_playlists(conn):
    return conn.execute(
        "SELECT p.*, COUNT(i.id) AS n, "
        "  (SELECT v.thumb FROM playlist_items j JOIN videos v ON v.id = j.video_id "
        "   WHERE j.playlist_id = p.id AND v.thumb IS NOT NULL ORDER BY j.pos LIMIT 1) AS cover "
        "FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id = p.id "
        "GROUP BY p.id ORDER BY (p.system IS NULL), p.name COLLATE NOCASE").fetchall()


def get_playlist(conn, pid):
    return conn.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()


def playlist_videos(conn, pid):
    return conn.execute(
        "SELECT v.*, i.id AS item_id FROM playlist_items i JOIN videos v ON v.id = i.video_id "
        "WHERE i.playlist_id=? AND v.missing=0 ORDER BY i.pos", (pid,)).fetchall()


def add_to_playlist(conn, pid, video_ids):
    row = conn.execute("SELECT COALESCE(MAX(pos), -1) AS m FROM playlist_items WHERE playlist_id=?",
                       (pid,)).fetchone()
    pos = row["m"] + 1
    for vid in video_ids:
        conn.execute("INSERT INTO playlist_items(playlist_id, video_id, pos) VALUES (?,?,?)", (pid, vid, pos))
        pos += 1
    conn.commit()


def remove_playlist_items(conn, item_ids):
    for iid in item_ids:
        row = conn.execute("SELECT i.video_id, p.system FROM playlist_items i JOIN playlists p "
                           "ON p.id = i.playlist_id WHERE i.id=?", (iid,)).fetchone()
        if row and row["system"] == "likes":
            conn.execute("UPDATE videos SET liked=0, liked_at=NULL WHERE id=?", (row["video_id"],))
    conn.executemany("DELETE FROM playlist_items WHERE id=?", [(i,) for i in item_ids])
    conn.commit()


def reorder_playlist(conn, pid, item_ids):
    conn.executemany("UPDATE playlist_items SET pos=? WHERE id=? AND playlist_id=?",
                     [(n, iid, pid) for n, iid in enumerate(item_ids)])
    conn.commit()


def rename_playlist(conn, pid, name):
    conn.execute("UPDATE playlists SET name=? WHERE id=?", (name, pid))
    conn.commit()


def set_playlist_loop(conn, pid, loop):
    conn.execute("UPDATE playlists SET loop=? WHERE id=?", (1 if loop else 0, pid))
    conn.commit()


def delete_playlist(conn, pid):
    conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
    conn.execute("DELETE FROM playlists WHERE id=?", (pid,))
    conn.commit()


# --- mosaiques enregistrees ----------------------------------------------------------

def save_mosaic(conn, mosaic_id, name, screen, config, state):
    import json
    import time
    if mosaic_id and conn.execute("SELECT 1 FROM mosaics WHERE id=?", (mosaic_id,)).fetchone():
        conn.execute("UPDATE mosaics SET name=?, screen=?, config=?, state=?, updated_at=? WHERE id=?",
                     (name, screen, json.dumps(config), json.dumps(state), time.time(), mosaic_id))
        conn.commit()
        return mosaic_id
    cur = conn.execute("INSERT INTO mosaics(name, screen, config, state, updated_at) VALUES (?,?,?,?,?)",
                       (name, screen, json.dumps(config), json.dumps(state), time.time()))
    conn.commit()
    return cur.lastrowid


def list_mosaics(conn):
    return conn.execute("SELECT * FROM mosaics ORDER BY name COLLATE NOCASE").fetchall()


def get_mosaic(conn, mosaic_id):
    import json
    row = conn.execute("SELECT * FROM mosaics WHERE id=?", (mosaic_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "screen": row["screen"],
            "config": json.loads(row["config"]), "state": json.loads(row["state"] or "[]")}


def delete_mosaic(conn, mosaic_id):
    conn.execute("DELETE FROM mosaics WHERE id=?", (mosaic_id,))
    conn.commit()


def all_video_ids(conn):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM videos WHERE missing=0%s ORDER BY category COLLATE NOCASE, title COLLATE NOCASE"
        % kind_filter(conn))]


def category_video_ids(conn, category):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM videos WHERE missing=0 AND category=?%s ORDER BY title COLLATE NOCASE"
        % kind_filter(conn), (category,))]


# --- j'aime ---------------------------------------------------------------------------

LIKES_NAME = "J'aime"


def likes_playlist(conn):
    """Identifiant de la playlist systeme « J'aime » (creee au besoin)."""
    row = conn.execute("SELECT id FROM playlists WHERE system='likes'").fetchone()
    if row:
        return row["id"]
    import time
    cur = conn.execute("INSERT INTO playlists(name, created_at, system) VALUES (?,?, 'likes')",
                       (LIKES_NAME, time.time()))
    conn.commit()
    return cur.lastrowid


def is_liked(conn, video_id):
    row = conn.execute("SELECT liked FROM videos WHERE id=?", (video_id,)).fetchone()
    return bool(row and row["liked"])


def set_liked(conn, video_id, liked):
    import time
    pid = likes_playlist(conn)
    conn.execute("UPDATE videos SET liked=?, liked_at=? WHERE id=?",
                 (1 if liked else 0, time.time() if liked else None, video_id))
    if liked:
        if not conn.execute("SELECT 1 FROM playlist_items WHERE playlist_id=? AND video_id=?",
                            (pid, video_id)).fetchone():
            # les nouveaux coups de coeur arrivent en tete de la playlist
            conn.execute("UPDATE playlist_items SET pos = pos + 1 WHERE playlist_id=?", (pid,))
            conn.execute("INSERT INTO playlist_items(playlist_id, video_id, pos) VALUES (?,?,0)", (pid, video_id))
    else:
        conn.execute("DELETE FROM playlist_items WHERE playlist_id=? AND video_id=?", (pid, video_id))
    conn.commit()
    return liked


def toggle_like(conn, video_id):
    return set_liked(conn, video_id, not is_liked(conn, video_id))


def liked_videos(conn, limit=None):
    sql = ("SELECT v.* FROM playlist_items i JOIN videos v ON v.id = i.video_id "
           "WHERE i.playlist_id=? AND v.missing=0 ORDER BY i.pos")
    pid = likes_playlist(conn)
    return conn.execute(sql + (" LIMIT %d" % limit if limit else ""), (pid,)).fetchall()


def relocate_data(conn):
    """Si le dossier data a ete copie depuis un autre PC (ou deplace), corrige les chemins des miniatures."""
    from .config import THUMB_DIR
    row = conn.execute("SELECT thumb FROM videos WHERE thumb IS NOT NULL LIMIT 1").fetchone()
    if not row:
        return
    old_dir = row["thumb"][: max(row["thumb"].rfind("\\"), row["thumb"].rfind("/"))]
    new_dir = str(THUMB_DIR)
    if old_dir and old_dir != new_dir:
        conn.execute("UPDATE videos SET thumb = ? || substr(thumb, ?) WHERE thumb LIKE ?",
                     (new_dir, len(old_dir) + 1, old_dir + "%"))
        conn.commit()
