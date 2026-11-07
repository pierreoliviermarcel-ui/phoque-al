"""Vidéos à trier : les nouvelles vidéos de tes « dossiers à trier » regroupées par personne / tag,
à glisser sur le dossier de ton choix (⋯ → Vidéos à trier…).

- Un ou plusieurs dossiers de la bibliothèque sont marqués « à trier ».
- Les vidéos y sont regroupées : personne reconnue, tag validé, tag proposé par l'IA, sinon « Sans tag ».
- Glisser une ou plusieurs vignettes (ou « tout le groupe ») sur un dossier à droite : le fichier est déplacé,
  ses tags, notes, moments et son historique le suivent. Même disque : instantané ; autre disque : copie
  vérifiée puis suppression de l'original. « Annuler le dernier rangement » remet tout en place.
- Doublons signalés AVANT le tri : fichier identique déjà dans la bibliothèque (ou deux fois dans les dossiers
  à trier), ou même vidéo probable (image et durée voisines). Ils sont mis à part, jamais rangés automatiquement.
- Phoque Al retient où tu ranges chaque groupe : il le propose ensuite, et après 2 rangements identiques il
  demande s'il peut le faire tout seul pour les prochaines vidéos (toujours annulable).
"""
import json
import os
import shutil
import threading
import time
from pathlib import PurePath

from .. import db, volumes
from ..config import THUMB_DIR
from .common import log, safe

SCHEMA = """
CREATE TABLE IF NOT EXISTS tri_rules(key TEXT NOT NULL, dest TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    auto INTEGER NOT NULL DEFAULT 0, at REAL, PRIMARY KEY(key, dest));
CREATE TABLE IF NOT EXISTS tri_dhash(video_id INTEGER PRIMARY KEY, h INTEGER);
CREATE TABLE IF NOT EXISTS tri_log(batch INTEGER NOT NULL, video_id INTEGER NOT NULL, old_source INTEGER,
    old_rel TEXT, new_source INTEGER, new_rel TEXT, at REAL);
"""
AUTO_AFTER = 2
STATE = {"busy": False, "msg": "", "timer": None}


# ----------------------------------------------------------------------------------------
# Données
# ----------------------------------------------------------------------------------------
def inbox_ids(conn):
    try:
        v = json.loads(db.get_setting(conn, "tri_inbox", "[]") or "[]")
        return [int(x) for x in v]
    except Exception:
        return []


def set_inbox(conn, ids):
    db.set_setting(conn, "tri_inbox", json.dumps(sorted(set(int(i) for i in ids))))


def source_root(conn, sid):
    r = conn.execute("SELECT serial, rel_root FROM sources WHERE id=?", (sid,)).fetchone()
    return volumes.resolve(r["serial"], r["rel_root"]) if r else None


def inbox_videos(conn):
    ids = inbox_ids(conn)
    if not ids:
        return []
    q = ("SELECT id, source_id, rel_path, title, duration, thumb FROM videos WHERE missing=0 AND kind='video' "
         "AND source_id IN (%s) ORDER BY added_at DESC" % ",".join("?" * len(ids)))
    return conn.execute(q, ids).fetchall()


def keys_for(conn, vid):
    """Clés de regroupement d'une vidéo, de la plus sûre à la moins sûre : [(clé, libellé)]."""
    out = []
    try:
        for r in conn.execute("SELECT p.id, p.name FROM video_people vp JOIN people p ON p.id=vp.person_id "
                              "WHERE vp.video_id=? ORDER BY vp.manual DESC, p.name", (vid,)):
            out.append(("person:%d" % r[0], "Personne : %s" % r[1]))
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT t.id, t.name FROM video_tags vt JOIN tags t ON t.id=vt.tag_id "
                              "WHERE vt.video_id=? AND vt.manual>=0 ORDER BY vt.manual DESC, t.name", (vid,)):
            out.append(("tag:%d" % r[0], "Tag : %s" % r[1]))
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT tag, score FROM tag_suggest WHERE video_id=? AND state=0 ORDER BY score DESC LIMIT 2",
                              (vid,)):
            out.append(("sugg:%s" % r[0], "Proposé : %s" % r[0]))
    except Exception:
        pass
    return out


def rules(conn):
    out = {}
    for r in conn.execute("SELECT key, dest, n, auto FROM tri_rules ORDER BY n DESC"):
        out.setdefault(r[0], []).append({"dest": r[1], "n": r[2], "auto": r[3]})
    return out


def primary_key(conn, vid, rl=None):
    """Clé qui décide du groupe : d'abord une clé qui a déjà une règle, sinon la plus sûre."""
    ks = keys_for(conn, vid)
    rl = rules(conn) if rl is None else rl
    for k, lab in ks:
        if k in rl:
            return k, lab
    return ks[0] if ks else ("none", "Sans tag")


def groups(conn, dupes=None):
    """{clé: {label, vids: [...]}} pour les vidéos à trier (les doublons dans leur propre groupe)."""
    rl = rules(conn)
    out = {}
    dupes = dupes or {}
    for r in inbox_videos(conn):
        if r["id"] in dupes:
            k, lab = "dupes", "⚠ Doublons"
        else:
            k, lab = primary_key(conn, r["id"], rl)
        g = out.setdefault(k, {"label": lab, "rows": []})
        g["rows"].append(r)
    return out


def dest_key(sid, rel_dir):
    return "%d|%s" % (int(sid), rel_dir.replace("\\", "/").strip("/"))


def parse_dest(d):
    sid, _, rel = d.partition("|")
    return int(sid), rel


def dest_label(conn, d):
    sid, rel = parse_dest(d)
    r = conn.execute("SELECT name FROM sources WHERE id=?", (sid,)).fetchone()
    return ("%s / %s" % (r[0], rel) if rel else r[0]) if r else d


def learn(conn, key, dest):
    """Compte un rangement validé ; renvoie le nombre de fois où ce groupe est allé à cet endroit."""
    if not key or key == "none":
        return 0
    conn.execute("INSERT INTO tri_rules(key, dest, n, auto, at) VALUES (?,?,1,0,?) "
                 "ON CONFLICT(key, dest) DO UPDATE SET n=n+1, at=excluded.at", (key, dest, time.time()))
    conn.commit()
    r = conn.execute("SELECT n FROM tri_rules WHERE key=? AND dest=?", (key, dest)).fetchone()
    return r[0] if r else 0


def set_auto(conn, key, dest, on):
    conn.execute("UPDATE tri_rules SET auto=? WHERE key=? AND dest=?", (1 if on else -1, key, dest))
    conn.commit()


# ----------------------------------------------------------------------------------------
# Déplacement (avec mise à jour de la base : rien n'est perdu)
# ----------------------------------------------------------------------------------------
def same_drive(a, b):
    return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    k = 2
    while os.path.exists("%s (%d)%s" % (stem, k, ext)):
        k += 1
    return "%s (%d)%s" % (stem, k, ext)


def move_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if same_drive(src, dst):
        os.replace(src, dst)
        return
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    if os.path.getsize(tmp) != os.path.getsize(src):
        os.remove(tmp)
        raise OSError("copie incomplète")
    os.replace(tmp, dst)
    os.remove(src)


def update_row(conn, vid, sid, rel, source_name):
    parts = PurePath(rel).parts
    category = parts[0] if len(parts) > 1 else source_name
    tags = " ".join(parts[:-1])
    conn.execute("UPDATE videos SET source_id=?, rel_path=?, category=?, tags=?, missing=0 WHERE id=?",
                 (sid, rel, category, tags, vid))


def move_videos(conn, vids, dest, progress=None):
    """Déplace des vidéos vers dest (« id_source|sous/dossier »). -> (n_ok, erreurs, n_lot)."""
    sid, rel_dir = parse_dest(dest)
    droot = source_root(conn, sid)
    if not droot:
        return 0, ["dossier de destination introuvable (disque débranché ?)"], None
    sname = conn.execute("SELECT name FROM sources WHERE id=?", (sid,)).fetchone()[0]
    batch = int(time.time() * 1000)
    ok, errs = 0, []
    for i, vid in enumerate(vids):
        r = conn.execute("SELECT source_id, rel_path FROM videos WHERE id=?", (vid,)).fetchone()
        if not r:
            continue
        sroot = source_root(conn, r["source_id"])
        if not sroot:
            errs.append("%s : disque absent" % r["rel_path"])
            continue
        src = os.path.join(sroot, r["rel_path"])
        dst = unique_path(os.path.join(droot, rel_dir, os.path.basename(r["rel_path"])))
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            continue
        try:
            move_file(src, dst)
            new_rel = os.path.relpath(dst, droot)
            update_row(conn, vid, sid, new_rel, sname)
            conn.execute("INSERT INTO tri_log(batch, video_id, old_source, old_rel, new_source, new_rel, at) "
                         "VALUES (?,?,?,?,?,?,?)", (batch, vid, r["source_id"], r["rel_path"], sid, new_rel, time.time()))
            conn.commit()
            ok += 1
        except Exception as e:
            errs.append("%s : %s" % (os.path.basename(src), e))
            log("tri %s : %r" % (src, e))
        if progress:
            progress(i + 1, len(vids))
    return ok, errs, batch


def undo_last(conn):
    r = conn.execute("SELECT MAX(batch) FROM tri_log").fetchone()
    if not r or r[0] is None:
        return 0, []
    batch = r[0]
    ok, errs = 0, []
    for row in conn.execute("SELECT * FROM tri_log WHERE batch=? ORDER BY rowid DESC", (batch,)).fetchall():
        nroot, oroot = source_root(conn, row["new_source"]), source_root(conn, row["old_source"])
        if not nroot or not oroot:
            errs.append("disque absent")
            continue
        try:
            dst = unique_path(os.path.join(oroot, row["old_rel"]))
            move_file(os.path.join(nroot, row["new_rel"]), dst)
            sname = conn.execute("SELECT name FROM sources WHERE id=?", (row["old_source"],)).fetchone()[0]
            update_row(conn, row["video_id"], row["old_source"], os.path.relpath(dst, oroot), sname)
            ok += 1
        except Exception as e:
            errs.append(str(e))
    conn.execute("DELETE FROM tri_log WHERE batch=?", (batch,))
    conn.commit()
    return ok, errs


def _path(conn, vid):
    r = conn.execute("SELECT source_id, rel_path FROM videos WHERE id=?", (vid,)).fetchone()
    root = source_root(conn, r["source_id"]) if r else None
    return os.path.join(root, r["rel_path"]) if root else None


def _dhash(conn, vid):
    r = conn.execute("SELECT h FROM tri_dhash WHERE video_id=?", (vid,)).fetchone()
    if r:
        return r[0]
    p = THUMB_DIR / ("%d.jpg" % vid)
    if not p.is_file():
        return None
    try:
        from .visualdupes import dhash_file
        h = int(dhash_file(str(p)))
    except Exception:
        return None
    conn.execute("INSERT OR REPLACE INTO tri_dhash(video_id, h) VALUES (?,?)", (vid, h & 0x7FFFFFFFFFFFFFFF))
    return h & 0x7FFFFFFFFFFFFFFF


def find_dupes(conn):
    """{id vidéo à trier: [(id du double, « identique » | « probable »)]}."""
    from ..dupes import quick_hash
    ids = inbox_ids(conn)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    inbox = conn.execute("SELECT id, size, duration FROM videos WHERE missing=0 AND kind='video' AND source_id IN (%s)"
                         % marks, ids).fetchall()
    out = {}
    qh = {}

    def qhash(vid, size):
        if vid not in qh:
            p = _path(conn, vid)
            try:
                qh[vid] = quick_hash(p, size) if p and os.path.isfile(p) else None
            except OSError:
                qh[vid] = None
        return qh[vid]
    for r in inbox:
        found = []
        if r["size"]:
            for o in conn.execute("SELECT id FROM videos WHERE missing=0 AND kind='video' AND size=? AND id<>?",
                                  (r["size"], r["id"])):
                h1 = qhash(r["id"], r["size"])
                if h1 and h1 == qhash(o[0], r["size"]):
                    found.append((o[0], "identique"))
        if not found and r["duration"]:
            h1 = _dhash(conn, r["id"])
            if h1 is not None:
                for o in conn.execute("SELECT id FROM videos WHERE missing=0 AND kind='video' AND id<>? "
                                      "AND duration BETWEEN ? AND ?", (r["id"], r["duration"] - 2, r["duration"] + 2)):
                    h2 = _dhash(conn, o[0])
                    if h2 is not None and bin(h1 ^ h2).count("1") <= 6:
                        found.append((o[0], "probable"))
        if found:
            out[r["id"]] = found
    conn.commit()
    return out


def dupe_label(conn, vid):
    r = conn.execute("SELECT v.rel_path, s.name FROM videos v JOIN sources s ON s.id=v.source_id WHERE v.id=?", (vid,)).fetchone()
    return "%s / %s" % (r[1], r[0]) if r else str(vid)


def trash_video(conn, vid):
    from ..dupes import forget_entry, move_to_trash
    p = _path(conn, vid)
    if p and os.path.isfile(p):
        move_to_trash(p)
    forget_entry(conn, vid)


def auto_moves(conn):
    """[(dest, [vids])] des vidéos à trier dont le groupe a une règle automatique validée."""
    rl = rules(conn)
    plan = {}
    try:
        dup = find_dupes(conn)
    except Exception as e:
        log("tri doublons : %r" % e)
        dup = {}
    for r in inbox_videos(conn):
        if r["id"] in dup:
            continue                          # doublon : c'est toi qui décides
        k, _lab = primary_key(conn, r["id"], rl)
        for x in rl.get(k, []):
            if x["auto"] == 1:
                plan.setdefault(x["dest"], []).append(r["id"])
                break
    return list(plan.items())


def run_auto(main):
    if STATE["busy"] or not inbox_ids(main.conn):
        return
    plan = auto_moves(main.conn)
    if not plan:
        return
    STATE["busy"] = True

    def job():
        c = db.connect()
        total = 0
        try:
            for dest, vids in plan:
                n, _e, _b = move_videos(c, vids, dest)
                total += n
        finally:
            STATE["busy"] = False
            STATE["msg"] = ("Tri automatique : %d vidéo(s) rangée(s) (⋯ → Vidéos à trier pour annuler)" % total) if total else ""
    threading.Thread(target=lambda: safe(job), daemon=True).start()


# ----------------------------------------------------------------------------------------
# Fenêtre
# ----------------------------------------------------------------------------------------
def dest_folders(conn, depth=2):
    """[(dest, libellé, niveau)] : dossiers des sources qui ne sont pas « à trier »."""
    inbox = set(inbox_ids(conn))
    out = []
    for s in db.list_sources(conn):
        if s["id"] in inbox:
            continue
        root = volumes.resolve(s["serial"], s["rel_root"])
        if not root:
            continue
        out.append((dest_key(s["id"], ""), s["name"], 0))

        def walk(path, rel, lvl):
            try:
                names = sorted(n for n in os.listdir(path) if not n.startswith((".", "$", "_Corbeille"))
                               and os.path.isdir(os.path.join(path, n)))
            except OSError:
                return
            for n in names:
                r2 = (rel + "/" + n) if rel else n
                out.append((dest_key(s["id"], r2), n, lvl))
                if lvl < depth:
                    walk(os.path.join(path, n), r2, lvl + 1)
        walk(root, "", 1)
    return out


def open_dialog(main):
    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
                                   QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
                                   QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
    conn = main.conn
    conn.executescript(SCHEMA)
    d = QDialog(main)
    d.setWindowTitle("Vidéos à trier")
    d.resize(1280, 760)
    lay = QVBoxLayout(d)
    head = QLabel("Glisse des vignettes (ou un groupe entier) sur un dossier à droite. Les tags, notes et l'historique suivent "
                  "la vidéo. Après 2 rangements identiques, Phoque Al propose de ranger tout seul les prochaines.")
    head.setWordWrap(True)
    lay.addWidget(head)

    # --- dossiers à trier ---
    top = QHBoxLayout()
    top.addWidget(QLabel("Dossiers à trier :"))
    boxes = QHBoxLayout()
    top.addLayout(boxes, 1)
    add_in = QPushButton("Ajouter un dossier à trier…")
    top.addWidget(add_in)
    lay.addLayout(top)

    drow = QHBoxLayout()
    dlab = QLabel("")
    dlab.setWordWrap(True)
    dlab.setStyleSheet("color:#ff8a8f;font-weight:600")
    dtrash = QPushButton("Mettre les doublons identiques à la corbeille Phoque Al")
    drow.addWidget(dlab, 1)
    drow.addWidget(dtrash)
    lay.addLayout(drow)
    split = QSplitter(Qt.Horizontal)
    glist = QListWidget()
    glist.setMinimumWidth(240)
    vids = QListWidget()
    vids.setViewMode(QListWidget.IconMode)
    vids.setIconSize(QSize(192, 108))
    vids.setGridSize(QSize(208, 150))
    vids.setResizeMode(QListWidget.Adjust)
    vids.setSelectionMode(QAbstractItemView.ExtendedSelection)
    vids.setDragEnabled(True)
    vids.setDragDropMode(QAbstractItemView.DragOnly)
    vids.setMovement(QListWidget.Static)

    class DestTree(QTreeWidget):
        def dragEnterEvent(self, e):
            e.acceptProposedAction() if e.source() in (vids, glist) else e.ignore()

        def dragMoveEvent(self, e):
            it = self.itemAt(e.position().toPoint())
            if it is not None and e.source() in (vids, glist):
                self.setCurrentItem(it)
                e.acceptProposedAction()
            else:
                e.ignore()

        def dropEvent(self, e):
            it = self.itemAt(e.position().toPoint())
            if it is None:
                return e.ignore()
            e.acceptProposedAction()
            if e.source() is glist:
                ids = [r["id"] for r in current_group_rows()]
            else:
                ids = [x.data(Qt.UserRole) for x in vids.selectedItems()]
            safe(do_move, ids, it.data(0, Qt.UserRole))
    tree = DestTree()
    tree.setHeaderLabels(["Ranger dans…"])
    tree.setAcceptDrops(True)
    tree.setDragDropMode(QAbstractItemView.DropOnly)
    glist.setDragEnabled(True)
    glist.setDragDropMode(QAbstractItemView.DragOnly)

    mid = QWidget()
    ml = QVBoxLayout(mid)
    ml.setContentsMargins(0, 0, 0, 0)
    sugg = QLabel("")
    sugg.setWordWrap(True)
    sugg.setStyleSheet("color:#9bb8ff")
    srow = QHBoxLayout()
    use_sugg = QPushButton("Ranger tout le groupe là")
    use_sugg.setVisible(False)
    srow.addWidget(sugg, 1)
    srow.addWidget(use_sugg)
    ml.addLayout(srow)
    ml.addWidget(vids, 1)
    right = QWidget()
    rl_ = QVBoxLayout(right)
    rl_.setContentsMargins(0, 0, 0, 0)
    rl_.addWidget(tree, 1)
    newf = QPushButton("Nouveau dossier ici…")
    rl_.addWidget(newf)
    split.addWidget(glist)
    split.addWidget(mid)
    split.addWidget(right)
    split.setSizes([260, 680, 320])
    lay.addWidget(split, 1)

    bottom = QHBoxLayout()
    status = QLabel("")
    status.setStyleSheet("color:#8b8c99")
    undo = QPushButton("Annuler le dernier rangement")
    autos = QPushButton("Rangements automatiques…")
    close = QPushButton("Fermer")
    bottom.addWidget(status, 1)
    bottom.addWidget(autos)
    bottom.addWidget(undo)
    bottom.addWidget(close)
    lay.addLayout(bottom)
    st = {"groups": {}, "inbox_boxes": [], "suggest": None}

    def fill_inbox():
        while boxes.count():
            w = boxes.takeAt(0).widget()
            if w:
                w.deleteLater()
        ids = set(inbox_ids(conn))
        st["inbox_boxes"] = []
        for s in db.list_sources(conn):
            c = QCheckBox(s["name"])
            c.setChecked(s["id"] in ids)
            c.toggled.connect(lambda on, sid=s["id"]: (set_inbox(conn, (set(inbox_ids(conn)) | {sid}) if on
                                                                   else (set(inbox_ids(conn)) - {sid})), refresh()))
            boxes.addWidget(c)
        boxes.addStretch(1)

    def fill_tree():
        tree.clear()
        stack = []
        for dest, name, lvl in dest_folders(conn):
            it = QTreeWidgetItem([name])
            it.setData(0, Qt.UserRole, dest)
            del stack[lvl:]
            (stack[-1].addChild(it) if stack else tree.addTopLevelItem(it))
            stack.append(it)
        tree.expandToDepth(0)

    def check_dupes():
        try:
            st["dupes"] = find_dupes(conn)
        except Exception as e:
            log("tri doublons : %r" % e)
            st["dupes"] = {}
        n = len(st["dupes"])
        same = sum(1 for v in st["dupes"].values() if any(k == "identique" for _o, k in v))
        dlab.setText(("⚠ %d vidéo(s) ont déjà un double dans ta bibliothèque (%d fichier(s) identique(s)) : "
                      "vérifie le groupe « Doublons » avant de trier." % (n, same)) if n else "")
        dtrash.setVisible(same > 0)

    def fill_groups():
        cur = glist.currentItem().data(Qt.UserRole) if glist.currentItem() else None
        check_dupes()
        st["groups"] = groups(conn, st.get("dupes"))
        glist.clear()
        order = sorted(st["groups"].items(), key=lambda kv: (kv[0] != "dupes", kv[0] == "none", -len(kv[1]["rows"]), kv[1]["label"]))
        for k, g in order:
            it = QListWidgetItem("%s  (%d)" % (g["label"], len(g["rows"])))
            it.setData(Qt.UserRole, k)
            glist.addItem(it)
            if k == cur:
                glist.setCurrentItem(it)
        if glist.currentItem() is None and glist.count():
            glist.setCurrentRow(0)
        if not glist.count():
            vids.clear()
            sugg.setText("Rien à trier." if inbox_ids(conn) else "Coche ci-dessus le ou les dossiers à trier "
                         "(ou « Ajouter un dossier à trier… »).")
            use_sugg.setVisible(False)

    def current_group_rows():
        it = glist.currentItem()
        return st["groups"].get(it.data(Qt.UserRole), {}).get("rows", []) if it else []

    def show_group():
        vids.clear()
        it = glist.currentItem()
        if not it:
            return
        k = it.data(Qt.UserRole)
        for r in current_group_rows():
            title = ("Vidéo %d" % r["id"]) if main.discreet else (r["title"] or os.path.basename(r["rel_path"]))
            tip = r["rel_path"]
            if r["id"] in st.get("dupes", {}):
                tip += "\n" + "\n".join("%s : %s" % ("Identique à" if kind == "identique" else "Ressemble à",
                                                     dupe_label(conn, o)) for o, kind in st["dupes"][r["id"]])
                title = "⚠ " + title
            w = QListWidgetItem(title)
            w.setData(Qt.UserRole, r["id"])
            w.setToolTip(tip)
            if not main.discreet:
                p = THUMB_DIR / ("%d.jpg" % r["id"])
                if p.is_file():
                    w.setIcon(QIcon(QPixmap(str(p))))
            vids.addItem(w)
        if k == "dupes":
            sugg.setText("Ces vidéos existent déjà (survole une vignette pour voir où). Double-clic pour comparer, "
                         "puis range-les quand même ou mets les identiques à la corbeille.")
            use_sugg.setVisible(False)
            st["suggest"] = None
            return
        best = (rules(conn).get(k) or [None])[0]
        st["suggest"] = best["dest"] if best and best["auto"] >= 0 else None
        if st["suggest"]:
            sugg.setText("D'habitude rangé dans « %s » (%d fois)%s" % (
                dest_label(conn, st["suggest"]), best["n"], " · automatique" if best["auto"] == 1 else ""))
            use_sugg.setVisible(True)
        else:
            sugg.setText("Pas encore de dossier habituel pour ce groupe.")
            use_sugg.setVisible(False)

    def refresh():
        fill_tree()
        fill_groups()
        show_group()

    def do_move(ids, dest):
        if not ids or not dest:
            return
        dd = [i for i in ids if i in st.get("dupes", {})]
        if dd and QMessageBox.question(d, "Vidéos à trier", "%d de ces vidéos ont déjà un double dans la bibliothèque.\n"
                                       "Les ranger quand même ?" % len(dd)) != QMessageBox.Yes:
            return
        it = glist.currentItem()
        key = it.data(Qt.UserRole) if it else None
        status.setText("Rangement de %d vidéo(s)…" % len(ids))
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        n, errs, _b = move_videos(conn, ids, dest, lambda i, t: (status.setText("Rangement %d / %d…" % (i, t)),
                                                               QApplication.processEvents()))
        status.setText("%d vidéo(s) rangée(s) dans « %s »%s" % (n, dest_label(conn, dest),
                                                              (" — %d erreur(s)" % len(errs)) if errs else ""))
        if errs:
            QMessageBox.warning(d, "Vidéos à trier", "\n".join(errs[:12]))
        if n and key:
            times = learn(conn, key, dest)
            r = conn.execute("SELECT auto FROM tri_rules WHERE key=? AND dest=?", (key, dest)).fetchone()
            if times >= AUTO_AFTER and r and r[0] == 0:
                lab = st["groups"].get(key, {}).get("label", key)
                ans = QMessageBox.question(d, "Vidéos à trier",
                                           "Tu as rangé « %s » dans « %s » %d fois.\nRanger automatiquement les "
                                           "prochaines vidéos de ce groupe ?\n(Tu pourras toujours annuler ou changer d'avis.)"
                                           % (lab, dest_label(conn, dest), times))
                set_auto(conn, key, dest, ans == QMessageBox.Yes)
        refresh()

    def add_inbox():
        path = QFileDialog.getExistingDirectory(d, "Dossier à trier")
        if not path:
            return
        root, rel = volumes.split_path(path)
        serial, label = volumes.volume_info(root)
        if not serial:
            QMessageBox.warning(d, "Vidéos à trier", "Impossible d'identifier ce disque.")
            return
        name = os.path.basename(os.path.normpath(path)) or (label or root)
        db.add_source(conn, serial, label, rel, name)
        r = conn.execute("SELECT id FROM sources WHERE serial=? AND rel_root=?", (serial, rel)).fetchone()
        if r:
            set_inbox(conn, set(inbox_ids(conn)) | {r[0]})
        fill_inbox()
        status.setText("Analyse du dossier… les vidéos apparaissent au fur et à mesure.")
        try:
            main.start_scan()
        except Exception:
            pass
        QTimer.singleShot(4000, refresh)

    def new_folder():
        it = tree.currentItem()
        if not it:
            QMessageBox.information(d, "Vidéos à trier", "Choisis d'abord le dossier parent à droite.")
            return
        name, ok = QInputDialog.getText(d, "Nouveau dossier", "Nom du dossier :")
        name = "".join(ch for ch in (name or "").strip() if ch not in '<>:"/\\|?*')
        if not ok or not name:
            return
        sid, rel = parse_dest(it.data(0, Qt.UserRole))
        root = source_root(conn, sid)
        if root:
            os.makedirs(os.path.join(root, rel, name), exist_ok=True)
            fill_tree()

    def trash_same():
        same = [v for v, lst in st.get("dupes", {}).items() if any(k == "identique" for _o, k in lst)]
        if not same:
            return
        if QMessageBox.question(d, "Vidéos à trier", "Mettre %d fichier(s) identique(s) à un fichier déjà rangé dans la "
                                "corbeille Phoque Al ?\n(L'exemplaire déjà dans ta bibliothèque est gardé. Rien n'est effacé "
                                "tant que la corbeille n'est pas vidée.)" % len(same)) != QMessageBox.Yes:
            return
        n = 0
        for v in same:
            try:
                trash_video(conn, v)
                n += 1
            except Exception as e:
                log("tri corbeille : %r" % e)
        status.setText("%d doublon(s) mis à la corbeille Phoque Al" % n)
        refresh()

    def do_undo():
        n, errs = undo_last(conn)
        status.setText("Annulé : %d vidéo(s) remise(s) à leur place%s" % (n, (" — " + errs[0]) if errs else ""))
        refresh()

    def manage_autos():
        rows = conn.execute("SELECT key, dest, n, auto FROM tri_rules WHERE auto=1").fetchall()
        if not rows:
            QMessageBox.information(d, "Vidéos à trier", "Aucun rangement automatique pour l'instant.")
            return
        labels = ["%s  →  %s" % (k_label(r["key"]), dest_label(conn, r["dest"])) for r in rows]
        lab, ok = QInputDialog.getItem(d, "Rangements automatiques", "Choisis un rangement à arrêter :", labels, 0, False)
        if ok and lab in labels:
            r = rows[labels.index(lab)]
            set_auto(conn, r["key"], r["dest"], False)
            refresh()

    def k_label(k):
        kind, _, v = k.partition(":")
        try:
            if kind == "person":
                return "Personne : " + conn.execute("SELECT name FROM people WHERE id=?", (int(v),)).fetchone()[0]
            if kind == "tag":
                return "Tag : " + conn.execute("SELECT name FROM tags WHERE id=?", (int(v),)).fetchone()[0]
        except Exception:
            pass
        return ("Proposé : " + v) if kind == "sugg" else k

    glist.currentItemChanged.connect(lambda *_: show_group())
    vids.itemDoubleClicked.connect(lambda w: safe(main.ctrl.play, w.data(Qt.UserRole)))
    use_sugg.clicked.connect(lambda: safe(do_move, [r["id"] for r in current_group_rows()], st["suggest"]))
    add_in.clicked.connect(lambda: safe(add_inbox))
    newf.clicked.connect(lambda: safe(new_folder))
    undo.clicked.connect(lambda: safe(do_undo))
    dtrash.clicked.connect(lambda: safe(trash_same))
    autos.clicked.connect(lambda: safe(manage_autos))
    close.clicked.connect(d.accept)
    fill_inbox()
    refresh()
    d.exec()
    try:
        main.show_home()
    except Exception:
        pass


def menu(main, m):
    n = 0
    try:
        n = len(inbox_videos(main.conn))
    except Exception:
        pass
    m.addAction("Vidéos à trier…%s" % (" (%d)" % n if n else "")).triggered.connect(lambda: safe(open_dialog, main))


def install(main):
    from PySide6.QtCore import QTimer
    main.conn.executescript(SCHEMA)
    main.conn.commit()
    t = QTimer(main)

    def tick():
        if STATE["msg"]:
            main.status.setText(STATE["msg"])
            STATE["msg"] = ""
        safe(run_auto, main)
    t.timeout.connect(tick)
    t.start(120000)
    STATE["timer"] = t
