"""Doublons visuels : la meme video en plusieurs qualites / encodages (tailles de fichier differentes).

1. Empreinte visuelle (dHash 64 bits) de chaque miniature deja creee : comparaison instantanee.
2. Les paires proches (et de duree voisine) sont confirmees sur 3 autres images de la video (FFmpeg).
3. Resultat par groupes ; Phoque Al propose de garder la meilleure qualite (definition, puis debit).
Les fichiers retires vont dans la corbeille Phoque Al (comme pour les doublons exacts).
"""
import io
import os
import subprocess
import sys

from PySide6.QtCore import QThread, Signal

from .. import db, volumes
from ..config import find_tool
from .common import log

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
THUMB_BITS = 10        # ecart toleré entre miniatures (sur 64)
FRAME_BITS = 12        # ecart toleré sur les images de confirmation
POPCOUNT = bytes(bin(i).count("1") for i in range(256))


def dhash_image(img):
    from PIL import Image
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    v = 0
    for y in range(8):
        for x in range(8):
            v = (v << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
    return v


def dhash_file(path):
    from PIL import Image
    with Image.open(path) as im:
        return dhash_image(im)


def dist(a, b):
    return bin(a ^ b).count("1")


def frame_hash(ffmpeg, path, t):
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % t, "-i", path,
                          "-frames:v", "1", "-vf", "scale=64:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, timeout=60, creationflags=_FLAGS).stdout
    if not out:
        return None
    from PIL import Image
    return dhash_image(Image.open(io.BytesIO(out)))


def probe(ffprobe, path):
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0:s=x", path],
                             capture_output=True, text=True, timeout=30, creationflags=_FLAGS).stdout.strip()
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def candidate_pairs(items, max_bits=THUMB_BITS):
    """items : [(id, hash, duration, size)] -> [(i, j)] proches visuellement, de duree voisine, tailles differentes."""
    try:
        import numpy as np
        ids = [it[0] for it in items]
        hs = np.array([it[1] for it in items], dtype=np.uint64)
        durs = np.array([it[2] or 0 for it in items], dtype=np.float64)
        sizes = np.array([it[3] or 0 for it in items], dtype=np.int64)
        table = np.frombuffer(POPCOUNT, dtype=np.uint8)
        pairs = []
        for i in range(len(items)):
            x = (hs[i + 1:] ^ hs[i]).view(np.uint8).reshape(-1, 8)
            d = table[x].sum(axis=1)
            idx = np.nonzero(d <= max_bits)[0] + i + 1
            for j in idx:
                a, b = durs[i], durs[j]
                if a and b and abs(a - b) > max(2.0, 0.02 * max(a, b)):
                    continue
                if sizes[i] == sizes[j]:
                    continue                                  # meme fichier : deja traite par les doublons exacts
                pairs.append((ids[i], ids[j]))
        return pairs
    except ImportError:
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if dist(items[i][1], items[j][1]) <= max_bits and items[i][3] != items[j][3]:
                    a, b = items[i][2] or 0, items[j][2] or 0
                    if a and b and abs(a - b) > max(2.0, 0.02 * max(a, b)):
                        continue
                    pairs.append((items[i][0], items[j][0]))
        return pairs


def groups_from(pairs):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    out = {}
    for x in parent:
        out.setdefault(find(x), []).append(x)
    return [g for g in out.values() if len(g) > 1]


class Scanner(QThread):
    progress = Signal(str)
    result = Signal(list)

    def __init__(self, deep=True):
        super().__init__()
        self.deep = deep

    def run(self):
        conn = db.connect()
        try:
            # 1. empreintes des miniatures (gardees en base pour les fois suivantes)
            rows = conn.execute("SELECT v.id, v.thumb, v.duration, v.size, h.h FROM videos v LEFT JOIN vhash h "
                                "ON h.video_id=v.id WHERE v.missing=0 AND v.kind='video' AND v.thumb IS NOT NULL").fetchall()
            items = []
            for k, r in enumerate(rows, 1):
                if self.isInterruptionRequested():
                    return
                h = int(r["h"], 16) if r["h"] else None
                if h is None and os.path.isfile(r["thumb"]):
                    try:
                        h = dhash_file(r["thumb"])
                        conn.execute("INSERT OR REPLACE INTO vhash(video_id, h) VALUES (?,?)", (r["id"], "%016x" % h))
                    except Exception:
                        h = None
                if h is not None and h not in (0, 2 ** 64 - 1):        # image unie (noire...) : ignoree
                    items.append((r["id"], h, r["duration"], r["size"]))
                if k % 200 == 0:
                    conn.commit()
                    self.progress.emit("Empreintes visuelles : %d / %d" % (k, len(rows)))
            conn.commit()
            self.progress.emit("Comparaison de %d vidéos…" % len(items))
            pairs = candidate_pairs(items)
            # 2. confirmation sur 3 autres images
            ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
            drives = volumes.connected_drives()
            info = {}

            def path_of(vid):
                if vid not in info:
                    r = conn.execute("SELECT v.*, s.serial, s.rel_root, s.label FROM videos v JOIN sources s "
                                     "ON s.id=v.source_id WHERE v.id=?", (vid,)).fetchone()
                    root = volumes.resolve(r["serial"], r["rel_root"], drives) if r else None
                    info[vid] = (dict(r) if r else None, os.path.join(root, r["rel_path"]) if root else None)
                return info[vid]

            confirmed = []
            cache = {}
            for k, (a, b) in enumerate(pairs, 1):
                if self.isInterruptionRequested():
                    return
                self.progress.emit("Vérification des ressemblances : %d / %d" % (k, len(pairs)))
                if not self.deep or not ffmpeg:
                    confirmed.append((a, b))
                    continue
                ra, pa = path_of(a)
                rb, pb = path_of(b)
                if not pa or not pb:
                    continue
                dur = min(ra["duration"] or 0, rb["duration"] or 0)
                if dur < 20:
                    confirmed.append((a, b))
                    continue
                ok = 0
                for frac in (0.35, 0.55, 0.75):
                    ha = cache.get((a, frac)) or frame_hash(ffmpeg, pa, dur * frac)
                    hb = cache.get((b, frac)) or frame_hash(ffmpeg, pb, dur * frac)
                    cache[(a, frac)], cache[(b, frac)] = ha, hb
                    if ha is not None and hb is not None and dist(ha, hb) <= FRAME_BITS:
                        ok += 1
                if ok >= 2:
                    confirmed.append((a, b))
            # 3. groupes + qualite
            out = []
            for g in groups_from(confirmed):
                entries = []
                for vid in g:
                    r, p = path_of(vid)
                    if not r or not p or not os.path.isfile(p):
                        continue
                    w, h = probe(ffprobe, p) if ffprobe else (0, 0)
                    rate = (r["size"] or 0) / r["duration"] if r["duration"] else 0
                    entries.append(dict(r, path=p, width=w, height=h, rate=rate))
                if len(entries) > 1:
                    entries.sort(key=lambda e: (-(e["width"] * e["height"]), -e["rate"]))
                    out.append(entries)
            out.sort(key=lambda g: -sum(e["size"] or 0 for e in g[1:]))
            self.result.emit(out)
        except Exception as e:
            log("doublons visuels : %r" % e)
            self.result.emit([])
        finally:
            conn.close()


def VisualDupesDialog(main):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar,
                                   QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)
    from .. import dupes
    from ..dupes import human

    dlg = QDialog(main)
    dlg.setWindowTitle("Doublons visuels (même vidéo, qualités différentes)")
    dlg.resize(1150, 680)
    lay = QVBoxLayout(dlg)
    intro = QLabel("Phoque Al compare l'<b>image</b> des vidéos pour trouver la même vidéo en plusieurs qualités ou encodages. "
                   "La meilleure version (définition, puis débit) est gardée par défaut ; les autres, cochées, partent "
                   "dans la <b>corbeille Phoque Al</b> (rien n'est effacé tant qu'elle n'est pas vidée).")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    top = QHBoxLayout()
    go = QPushButton("Lancer l'analyse")
    go.setObjectName("heroPrimary")
    deep = QCheckBox("Vérification approfondie (3 images de plus par vidéo : plus sûr, un peu plus lent)")
    deep.setChecked(True)
    state = QLabel()
    state.setObjectName("status")
    top.addWidget(go)
    top.addWidget(deep)
    top.addWidget(state, 1)
    lay.addLayout(top)
    tree = QTreeWidget()
    tree.setHeaderLabels(["Fichier", "Définition", "Taille", "Débit", "Durée", "Vues"])
    tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
    lay.addWidget(tree, 1)
    bottom = QHBoxLayout()
    summary = QLabel()
    trash = QPushButton("Mettre les fichiers cochés à la corbeille")
    close = QPushButton("Fermer")
    bottom.addWidget(summary, 1)
    bottom.addWidget(trash)
    bottom.addWidget(close)
    lay.addLayout(bottom)
    hint = QLabel("Double-clic sur une ligne pour la regarder et comparer.")
    hint.setObjectName("status")
    lay.addWidget(hint)
    holder = {}

    def checked():
        out = []
        for i in range(tree.topLevelItemCount()):
            g = tree.topLevelItem(i)
            for k in range(g.childCount()):
                it = g.child(k)
                if it.checkState(0) == Qt.Checked:
                    out.append((g, it))
        return out

    def update_summary(*_):
        sel = checked()
        summary.setText("%d fichier(s) coché(s) · %s" % (len(sel), human(sum(it.data(0, Qt.UserRole)["size"] or 0
                                                                             for _, it in sel))))
        trash.setEnabled(bool(sel))

    def on_change(item, _col):
        g = item.parent()
        if g is None:
            return
        kids = [g.child(k) for k in range(g.childCount())]
        if all(k.checkState(0) == Qt.Checked for k in kids):
            tree.blockSignals(True)
            item.setCheckState(0, Qt.Unchecked)
            tree.blockSignals(False)
            state.setText("Il doit rester au moins une version de chaque vidéo.")
        update_summary()

    def show(groups):
        go.setEnabled(True)
        tree.clear()
        tree.blockSignals(True)
        gain = 0
        for g in groups:
            head = QTreeWidgetItem(["%d versions  ·  %s" % (len(g), "" if main.discreet else g[0]["title"])])
            f = head.font(0)
            f.setBold(True)
            head.setFont(0, f)
            tree.addTopLevelItem(head)
            for k, e in enumerate(g):
                dims = "%d×%d" % (e["width"], e["height"]) if e["width"] else "?"
                it = QTreeWidgetItem([("Fichier %d" % (k + 1)) if main.discreet else e["path"], dims,
                                      human(e["size"] or 0), "%.1f Mb/s" % (e["rate"] * 8 / 1e6) if e["rate"] else "?",
                                      "%d min" % ((e["duration"] or 0) // 60), str(e["play_count"] or 0)])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if k == 0 else Qt.Checked)
                if k == 0:
                    it.setToolTip(0, "Meilleure qualité : gardée par défaut")
                else:
                    gain += e["size"] or 0
                it.setData(0, Qt.UserRole, e)
                head.addChild(it)
            head.setExpanded(True)
        tree.blockSignals(False)
        state.setText("%d groupe(s) trouvé(s) · %s récupérables" % (len(groups), human(gain)) if groups
                      else "Aucun doublon visuel trouvé.")
        update_summary()

    def start():
        w = holder.get("w")
        if w is not None and w.isRunning():
            return
        go.setEnabled(False)
        tree.clear()
        w = Scanner(deep.isChecked())
        w.progress.connect(state.setText)
        w.result.connect(show)
        holder["w"] = w
        w.start()

    def do_trash():
        sel = checked()
        if not sel or QMessageBox.question(dlg, "Doublons visuels", "Déplacer %d fichier(s) dans la corbeille Phoque Al ?"
                                           % len(sel)) != QMessageBox.Yes:
            return
        main.ctrl.close_all()
        done, errors = 0, []
        for g, it in sel:
            e = it.data(0, Qt.UserRole)
            try:
                dupes.move_to_trash(e["path"])
                dupes.forget_entry(main.conn, e["id"])
                it.setText(0, "✓ déplacé  —  " + it.text(0))
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                it.setData(0, Qt.CheckStateRole, None)
                it.setDisabled(True)
                done += 1
            except OSError as ex:
                errors.append("%s : %s" % (e["path"], ex))
        update_summary()
        QMessageBox.information(dlg, "Doublons visuels", "%d fichier(s) déplacé(s).%s" % (
            done, ("\n\n" + "\n".join(errors[:8])) if errors else ""))
        main.show_home()

    def play(item, _c):
        e = item.data(0, Qt.UserRole)
        if isinstance(e, dict):
            main.ctrl.play(e["id"], main.ask_screen())

    go.clicked.connect(lambda: start())
    trash.clicked.connect(lambda: do_trash())
    close.clicked.connect(dlg.accept)
    tree.itemChanged.connect(on_change)
    tree.itemDoubleClicked.connect(play)

    def finished(_r):
        w = holder.get("w")
        if w is not None and w.isRunning():
            w.requestInterruption()
            w.wait(3000)

    dlg.finished.connect(finished)
    update_summary()
    return dlg


def install(main):
    pass
