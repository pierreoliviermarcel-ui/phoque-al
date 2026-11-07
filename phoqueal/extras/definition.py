"""Définition des vidéos (4K, Full HD, HD, basse définition) : lue une fois avec ffprobe, puis gardée.

- Lecture en tâche de fond, tranquillement (une vidéo toutes les ~0,5 s, pause pendant la lecture
  si on le souhaite) ; les vidéos demandées tout de suite (PMV) passent avant.
- « ⋯ → Bibliothèque → Définition des vidéos… » : nombre de vidéos par définition, affichage de la liste.
- Créer un PMV : filtre « Définition » pour ne pas mélanger HD et basse définition.
Le classement se fait sur le petit côté de l'image : une vidéo verticale 1080×1920 est de la Full HD.
"""
import json
import os
import subprocess
import sys
import threading
import time

from .. import db, volumes
from .common import log, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
SCHEMA = """CREATE TABLE IF NOT EXISTS vres(video_id INTEGER PRIMARY KEY, w INTEGER, h INTEGER, key TEXT, done_at REAL);"""
LEVELS = [("4k", "4K (2160p)", 2000), ("fhd", "Full HD (1080p)", 1000), ("hd", "HD (720p)", 700),
          ("sd", "Basse définition (moins de 720p)", 1)]
LABEL = {k: lab for k, lab, _m in LEVELS}
SHORT = {"4k": "4K", "fhd": "1080p", "hd": "720p", "sd": "SD"}
# filtres proposés (création de PMV, etc.) : clé -> niveaux acceptés
FILTERS = [("all", "Toutes (HD et basse définition mélangées)", None),
           ("hd", "HD seulement (720p et plus)", {"4k", "fhd", "hd"}),
           ("fhd", "Full HD seulement (1080p et plus)", {"4k", "fhd"}),
           ("sd", "Basse définition seulement (rendu vintage, VHS)", {"sd"})]
WORKER = None


def level_of(w, h):
    s = min(w or 0, h or 0)
    if s <= 0:
        return None
    for k, _lab, mini in LEVELS:
        if s >= mini:
            return k
    return "sd"


def file_key(path):
    try:
        st = os.stat(path)
        return "%d:%d" % (st.st_size, int(st.st_mtime))
    except OSError:
        return None


def probe(path):
    """(largeur, hauteur) affichées (rotation et pixels non carrés pris en compte) ou None."""
    from ..config import find_tool
    fp = find_tool("ffprobe")
    if not fp:
        return None
    try:
        out = subprocess.run([fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,sample_aspect_ratio:stream_side_data=rotation:stream_tags=rotate",
                              "-of", "json", path], capture_output=True, text=True, timeout=15,
                             creationflags=_FLAGS, errors="replace").stdout
        st = (json.loads(out or "{}").get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        if not w or not h:
            return None
        sar = st.get("sample_aspect_ratio") or "1:1"
        try:
            a, b = (int(x) for x in sar.split(":"))
            if a > 0 and b > 0 and a != b:
                w = int(round(w * a / b))
        except ValueError:
            pass
        rot = (st.get("tags") or {}).get("rotate")
        for sd in st.get("side_data_list") or []:
            rot = sd.get("rotation", rot)
        if rot is not None and abs(int(float(rot))) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception as e:
        log("définition %s : %r" % (os.path.basename(path), e))
        return None


def path_of(conn, vid):
    r = conn.execute("SELECT v.rel_path, v.archive, s.serial, s.rel_root FROM videos v JOIN sources s "
                     "ON s.id=v.source_id WHERE v.id=?", (vid,)).fetchone()
    if not r or r["archive"]:
        return None
    root = volumes.resolve(r["serial"], r["rel_root"])
    return os.path.join(root, r["rel_path"]) if root else None


def measure(conn, vid, path=None):
    """Lit et enregistre la définition d'une vidéo ; renvoie le niveau ou None (disque absent…)."""
    path = path or path_of(conn, vid)
    if not path or not os.path.isfile(path):
        return None
    wh = probe(path)
    w, h = wh if wh else (0, 0)
    conn.execute("INSERT OR REPLACE INTO vres(video_id, w, h, key, done_at) VALUES (?,?,?,?,?)",
                 (vid, w, h, file_key(path), time.time()))
    conn.commit()
    return level_of(w, h)


def levels(conn, vids=None):
    """{video_id: niveau} pour les vidéos déjà mesurées (toutes, ou celles de vids)."""
    try:
        if vids is None:
            rows = conn.execute("SELECT video_id, w, h FROM vres WHERE w>0").fetchall()
        else:
            vids = [int(v) for v in vids if v and int(v) > 0]
            rows = []
            for i in range(0, len(vids), 500):
                part = vids[i:i + 500]
                rows += conn.execute("SELECT video_id, w, h FROM vres WHERE w>0 AND video_id IN (%s)"
                                     % ",".join("?" * len(part)), part).fetchall()
        return {r[0]: level_of(r[1], r[2]) for r in rows}
    except Exception:
        return {}


def keep(main, vids, flt, progress=None):
    """Garde les vidéos de vids qui correspondent au filtre ; mesure sur place celles pas encore vues."""
    ok = dict((k, s) for k, _l, s in FILTERS).get(flt)
    if not ok:
        return list(vids)
    conn = main.conn
    conn.executescript(SCHEMA)
    lv = levels(conn, vids)
    todo = [v for v in dict.fromkeys(vids) if v not in lv and v and v > 0]
    t0 = time.time()
    for i, v in enumerate(todo):
        if progress:
            progress(i, len(todo))
        lv[v] = measure(conn, v)
        if time.time() - t0 > 90:                        # bibliothèque énorme : on n'attend pas plus
            break
    return [v for v in vids if lv.get(v) in ok]


def counts(conn):
    conn.executescript(SCHEMA)
    out = {k: 0 for k, _l, _m in LEVELS}
    for lv in levels(conn).values():
        if lv:
            out[lv] += 1
    total = conn.execute("SELECT COUNT(*) FROM videos WHERE missing=0 AND kind='video'").fetchone()[0]
    out["?"] = max(0, total - sum(out.values()))
    return out, total


# ----------------------------------------------------------------------------------------
# Tâche de fond
# ----------------------------------------------------------------------------------------
class Worker:
    def __init__(self, main):
        self.main = main
        self.stopping = False
        self.current = None
        self.wake = threading.Event()
        self.thread = threading.Thread(target=lambda: safe(self.run), daemon=True)

    def start(self):
        self.thread.start()

    def busy_playing(self):
        try:
            return db.get_setting(self.main.conn, "vres_idle", "1") == "1" and len(self.main.ctrl.players) > 0
        except Exception:
            return False

    def run(self):
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout=8000")
        conn.executescript(SCHEMA)
        time.sleep(20)                                   # laisse Phoque Al démarrer tranquillement
        while not self.stopping:
            if self.busy_playing():
                self.wake.wait(15); self.wake.clear()
                continue
            rows = conn.execute("SELECT v.id FROM videos v LEFT JOIN vres r ON r.video_id=v.id "
                                "WHERE r.video_id IS NULL AND v.missing=0 AND v.kind='video' AND v.archive IS NULL "
                                "ORDER BY v.liked DESC, COALESCE(v.rating,0) DESC, v.added_at DESC LIMIT 40").fetchall()
            if not rows:
                self.wake.wait(600); self.wake.clear()
                continue
            got = 0
            for (vid,) in rows:
                if self.stopping or self.busy_playing():
                    break
                self.current = vid
                try:
                    if measure(conn, vid) is not None:
                        got += 1
                except Exception as e:
                    log("définition %s : %r" % (vid, e))
                finally:
                    self.current = None
                time.sleep(0.4)
            if not got:                                  # disque débranché : on réessaiera plus tard
                self.wake.wait(300); self.wake.clear()


# ----------------------------------------------------------------------------------------
# Fenêtre « Définition des vidéos »
# ----------------------------------------------------------------------------------------
def show_level(main, keys, label):
    conn = main.conn
    ids = [v for v, lv in levels(conn).items() if lv in keys]
    rows = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        rows += conn.execute("SELECT * FROM videos WHERE missing=0 AND id IN (%s) ORDER BY title"
                             % ",".join("?" * len(part)), part).fetchall()
    main.show_grid("%s  (%d)" % (label, len(rows)), rows)


def open_dialog(main):
    from PySide6.QtWidgets import QCheckBox, QDialog, QGridLayout, QLabel, QPushButton, QVBoxLayout
    c, total = counts(main.conn)
    d = QDialog(main)
    d.setWindowTitle("Définition des vidéos")
    lay = QVBoxLayout(d)
    info = QLabel("Classement d'après la taille réelle de l'image (petit côté). Utile pour créer des PMV "
                  "cohérents : dans « Créer un PMV », choisis la définition voulue.")
    info.setWordWrap(True)
    lay.addWidget(info)
    g = QGridLayout()
    for i, (k, lab, _m) in enumerate(LEVELS):
        g.addWidget(QLabel(lab), i, 0)
        n = QLabel("%d" % c[k]); n.setStyleSheet("font-weight:600")
        g.addWidget(n, i, 1)
        b = QPushButton("Afficher")
        b.setEnabled(c[k] > 0)
        b.clicked.connect(lambda _=False, k=k, lab=lab: (d.accept(), show_level(main, {k}, lab)))
        g.addWidget(b, i, 2)
    i = len(LEVELS)
    b = QPushButton("Toute la HD (720p et plus)")
    b.clicked.connect(lambda: (d.accept(), show_level(main, {"4k", "fhd", "hd"}, "HD (720p et plus)")))
    g.addWidget(b, i, 0, 1, 3)
    lay.addLayout(g)
    if c["?"]:
        lay.addWidget(QLabel("Pas encore lues : %d vidéo(s) — lecture en tâche de fond "
                             "(disque débranché : reprise quand il revient)." % c["?"]))
    idle = QCheckBox("Faire une pause pendant la lecture des vidéos")
    idle.setChecked(db.get_setting(main.conn, "vres_idle", "1") == "1")
    idle.toggled.connect(lambda on: db.set_setting(main.conn, "vres_idle", "1" if on else "0"))
    lay.addWidget(idle)
    close = QPushButton("Fermer"); close.clicked.connect(d.accept)
    lay.addWidget(close)
    d.exec()


def menu(main, m):
    m.addAction("Définition des vidéos (HD / basse déf.)…").triggered.connect(lambda: safe(open_dialog, main))


def card_menu(main, menu, vids):
    """Affiche la définition dans le menu clic droit d'une vidéo (si déjà connue)."""
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if len(vids) != 1:
        return {}
    try:
        r = main.conn.execute("SELECT w, h FROM vres WHERE video_id=?", (vids[0],)).fetchone()
    except Exception:
        r = None
    if r and r[0]:
        a = menu.addAction("Définition : %s (%d × %d)" % (SHORT.get(level_of(r[0], r[1]), "?"), r[0], r[1]))
        a.setEnabled(False)
    return {}


def install(main):
    global WORKER
    main.conn.executescript(SCHEMA)
    WORKER = Worker(main)
    WORKER.start()
