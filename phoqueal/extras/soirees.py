"""Soirees en un clic : enregistrer TOUTE la configuration en cours (videos sur chaque ecran, mosaiques,
playlists en cours, volumes, formats, fond flou, musique, montage au rythme) et la relancer d'un clic."""
import json
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QPushButton

from .. import db
from .common import log, play_at


# ----------------------------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------------------------
def capture(main):
    ctrl = main.ctrl
    screens = []
    for n, w in sorted(ctrl.players.items()):
        if getattr(w, "is_remote", False):
            continue
        if getattr(w, "is_mosaic", False):
            screens.append({"n": n, "type": "mosaic", "config": dict(w.config),
                            "tiles": [t.state() for t in w.tiles]})
            continue
        if not w.video_id:
            continue
        t = w.player.get_time()
        screens.append({
            "n": n, "type": "player", "video_id": w.video_id, "position": (t / 1000.0) if t and t > 0 else 0,
            "queue": list(w.queue) if w.queue else None, "qi": w.qi, "qname": w.qname,
            "loop": bool(w.loop), "random": bool(getattr(w, "random_mode", False)),
            "volume": int(w.volume), "muted": bool(w.muted), "view": w.view_mode,
            "blur": bool(getattr(w, "_b_on", True)),
        })
    mu = main.music
    mt = main.montage
    music = {"playing": bool(mu.is_playing()), "volume": int(mu.volume), "shuffle": bool(mu.shuffle),
             "track": mu.current_path() if mu.files else None}
    montage = {"on": bool(main.montage_btn.isChecked())}
    for k in ("min_dur", "every", "source", "mute_videos", "include_mosaics", "mosaic_mode"):
        if hasattr(mt, k):
            montage[k] = getattr(mt, k)
    return {"screens": screens, "music": music, "montage": montage, "v": 1}


def save(main, sid=None, name=None):
    data = json.dumps(capture(main))
    if sid:
        main.conn.execute("UPDATE soirees SET data=?, updated_at=? WHERE id=?", (data, time.time(), sid))
    else:
        main.conn.execute("INSERT INTO soirees(name, data, updated_at) VALUES (?,?,?)", (name, data, time.time()))
    main.conn.commit()


def soirees(conn):
    return conn.execute("SELECT * FROM soirees ORDER BY updated_at DESC").fetchall()


# ----------------------------------------------------------------------------------------
# Relance
# ----------------------------------------------------------------------------------------
def load(main, sid):
    r = main.conn.execute("SELECT * FROM soirees WHERE id=?", (sid,)).fetchone()
    if not r:
        return
    try:
        data = json.loads(r["data"])
    except ValueError:
        return
    ctrl = main.ctrl
    ctrl.close_all()
    nscreens = len(ctrl.screens())
    steps = []
    for s in data.get("screens", []):
        n = min(int(s["n"]), nscreens)
        if s["type"] == "mosaic":
            steps.append(lambda s=s, n=n: ctrl.open_mosaic(s["config"], state=s.get("tiles"), screen=n))
        else:
            steps.append(lambda s=s, n=n: _player(main, s, n))
    steps.append(lambda: _music(main, data.get("music") or {}, data.get("montage") or {}))
    for k, fn in enumerate(steps):                       # un ecran a la fois : menage le disque externe
        QTimer.singleShot(400 * k, lambda fn=fn: _safe(fn))
    main.status.setText("Soirée « %s » en cours de lancement…" % ("" if main.discreet else r["name"]))


def _safe(fn):
    try:
        fn()
    except Exception as e:
        log("soiree : %r" % e)


def _player(main, s, n):
    q = s.get("queue")
    kw = {}
    if q:
        kw = {"queue": q, "qi": int(s.get("qi") or 0), "qname": s.get("qname"), "loop": bool(s.get("loop"))}
    if not play_at(main, s["video_id"], float(s.get("position") or 0), choice=n, **kw):
        return
    p = main.ctrl.players.get(n)
    if p is None or getattr(p, "is_mosaic", False):
        return
    p.loop = bool(s.get("loop"))
    p.random_mode = bool(s.get("random"))
    p.volume = int(s.get("volume", p.volume))

    def finish():
        try:
            p.player.audio_set_volume(p.volume)
            p.set_mute(bool(s.get("muted")))
            if s.get("view"):
                p.set_view(s["view"], show=False)
            if hasattr(p, "set_blur"):
                p.set_blur(bool(s.get("blur", True)), show=False)
        except RuntimeError:
            pass

    QTimer.singleShot(1300, finish)


def _music(main, m, mt):
    mu = main.music
    if "shuffle" in m and bool(m["shuffle"]) != bool(mu.shuffle):
        main.music_shuffle.setChecked(bool(m["shuffle"]))
    if "volume" in m:
        mu.set_volume(int(m["volume"]))
        main.music_volume.blockSignals(True)
        main.music_volume.setValue(int(m["volume"]))
        main.music_volume.blockSignals(False)
    mont = main.montage
    for k, v in mt.items():
        if k != "on" and hasattr(mont, k):
            setattr(mont, k, v)
    if hasattr(mont, "save"):
        mont.save()
    if m.get("playing") and not mu.is_playing():
        mu.toggle()
    elif not m.get("playing") and mu.is_playing():
        mu.pause()
    want = bool(mt.get("on"))
    if main.montage_btn.isChecked() != want:
        main.montage_btn.setChecked(want)


# ----------------------------------------------------------------------------------------
# Interface : bouton « Soirées », rangee de l'accueil
# ----------------------------------------------------------------------------------------
def save_dialog(main):
    if not capture(main)["screens"]:
        QMessageBox.information(main, "Soirées", "Lance d'abord tes vidéos / mosaïques sur les écrans, "
                                "puis enregistre la soirée.")
        return
    names = [r["name"] for r in soirees(main.conn)]
    name, ok = QInputDialog.getText(main, "Enregistrer la soirée", "Nom de la soirée :")
    name = name.strip()
    if not ok or not name:
        return
    old = main.conn.execute("SELECT id FROM soirees WHERE name=?", (name,)).fetchone()
    if old and QMessageBox.question(main, "Soirées", "Remplacer la soirée « %s » ?" % name) != QMessageBox.Yes:
        return
    save(main, old["id"] if old else None, name)
    main.status.setText("Soirée « %s » enregistrée" % name)
    if main.pages.currentIndex() == 0:
        main.show_home()


def build_menu(main, btn):
    menu = QMenu(main)
    menu.addAction("Enregistrer la soirée en cours…").triggered.connect(lambda: save_dialog(main))
    rows = soirees(main.conn)
    if rows:
        menu.addSeparator()
        for k, r in enumerate(rows, 1):
            label = ("Soirée %d" % k) if main.discreet else r["name"]
            menu.addAction("▶  " + label).triggered.connect(lambda _=False, i=r["id"]: load(main, i))
        menu.addSeparator()
        sub = menu.addMenu("Mettre à jour avec la configuration actuelle")
        dsub = menu.addMenu("Supprimer")
        for k, r in enumerate(rows, 1):
            label = ("Soirée %d" % k) if main.discreet else r["name"]
            sub.addAction(label).triggered.connect(lambda _=False, i=r["id"]: (save(main, i), main.status.setText("Soirée mise à jour")))
            dsub.addAction(label).triggered.connect(lambda _=False, i=r["id"]: _delete(main, i))
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


def _delete(main, sid):
    if QMessageBox.question(main, "Soirées", "Supprimer cette soirée ?") == QMessageBox.Yes:
        main.conn.execute("DELETE FROM soirees WHERE id=?", (sid,))
        main.conn.commit()
        if main.pages.currentIndex() == 0:
            main.show_home()


def toolbar_buttons(main):
    b = QPushButton("Soirées")
    b.setToolTip("Enregistrer / relancer d'un clic toute une configuration (écrans, mosaïques, musique, montage)")
    b.clicked.connect(lambda: build_menu(main, b))
    return [b]


def home_rows(main):
    rows = soirees(main.conn)
    if not rows:
        return []
    items, ids = [], []
    for k, r in enumerate(rows, 1):
        try:
            data = json.loads(r["data"])
        except ValueError:
            continue
        vid = None
        for s in data.get("screens", []):
            if s.get("type") == "player":
                vid = s.get("video_id")
                break
            tiles = [t.get("video_id") for t in s.get("tiles") or [] if t.get("video_id")]
            if tiles:
                vid = tiles[0]
                break
        v = main.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone() if vid else None
        d = dict(v) if v else {"id": -1, "thumb": None, "liked": 0, "rating": 0}
        d["title"] = ("Soirée %d" % k) if main.discreet else "%s  ·  %d écran(s)" % (r["name"], len(data.get("screens", [])))
        d["kind"] = "photo"
        items.append(d)
        ids.append(r["id"])
    return [{"title": "✨ Mes soirées  (%d)" % len(items), "rows": items,
             "activate": lambda i: load(main, ids[i])}]


def install(main):
    pass
