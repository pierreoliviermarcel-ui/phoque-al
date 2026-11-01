"""Mode séance : une session guidée par la musique, en phases d'intensité.

- Phases (calme, montée, intense, redescente…) : chacune règle la vitesse des coupes du montage
  au rythme (1 coupe tous les 4, 2 ou 1 temps), la durée minimale d'un extrait et le volume.
- Vidéos : une sélection (playlist, thème, tag, coups de cœur…), sur tous les écrans ou l'écran principal.
- Minuteur discret, pauses automatiques en option, fin en douceur.
- Commandes : ⋯ → Séance, Ctrl+Maj+S (démarrer / arrêter), Ctrl+Maj+P (pause), Ctrl+Maj+N (phase suivante),
  téléphone (onglet Musique).
"""
import json
import random
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from .. import db
from .common import log, safe

# (nom, part de la durée, coupe tous les N temps, extrait minimum en s, volume musique relatif)
PRESETS = {
    "progressive": ("Progressive (calme → intense → redescente)", [
        ("Calme", 0.25, 4, 4.0, 0.80), ("Montée", 0.30, 2, 2.0, 0.90),
        ("Intense", 0.30, 1, 0.8, 1.00), ("Redescente", 0.15, 4, 4.0, 0.60)]),
    "vagues": ("Vagues (calme / intense en alternance)", [
        ("Calme", 0.15, 4, 4.0, 0.80), ("Intense", 0.15, 1, 0.8, 1.00),
        ("Calme", 0.15, 4, 4.0, 0.80), ("Intense", 0.15, 1, 0.8, 1.00),
        ("Calme", 0.12, 4, 4.0, 0.80), ("Intense", 0.18, 1, 0.8, 1.00), ("Redescente", 0.10, 4, 4.0, 0.60)]),
    "monte": ("Montée lente (de plus en plus rapide)", [
        ("Calme", 0.30, 4, 4.0, 0.75), ("Montée", 0.30, 2, 2.5, 0.85), ("Rapide", 0.20, 2, 1.2, 0.95),
        ("Intense", 0.20, 1, 0.8, 1.00)]),
    "intense": ("Intense tout du long", [("Intense", 0.92, 1, 0.8, 1.00), ("Redescente", 0.08, 4, 4.0, 0.60)]),
    "edging": ("Edging guidé (monte / ralentis / stop, cycles de plus en plus longs)", []),
}
EDGE = {"Monte": "MONTE", "Ralentis": "RALENTIS", "Stop": "STOP", "Final": "FINAL"}


def edging_phases(minutes):
    """Cycles monte → ralentis → stop, chaque cycle un peu plus long ; « Final » libre à la fin."""
    total = (minutes or 45) * 60
    final = min(240.0, total * 0.15)
    out, t, c = [], 0.0, 0
    while True:
        up, slow, stop = 75 + 25 * c, 20 + 3 * c, 20 + 5 * c
        if t + up + slow + stop > total - final:
            break
        out.append(("Monte", t, t + up * 0.6, 2, 1.6, 0.90))
        out.append(("Monte", t + up * 0.6, t + up, 1, 0.8, 1.00))
        t += up
        out.append(("Ralentis", t, t + slow, 4, 4.0, 0.70))
        t += slow
        out.append(("Stop", t, t + stop, 10 ** 6, 10.0 ** 6, 0.35))   # plus aucune coupe pendant le stop
        t += stop
        c += 1
    out.append(("Final", t, max(t + 60, total), 1, 0.8, 1.00))
    return out

SESSION = None


def S(conn, k, d):
    return db.get_setting(conn, "se_" + k, d)


def setS(conn, k, v):
    db.set_setting(conn, "se_" + k, str(v))


# ----------------------------------------------------------------------------------------
# Plan de séance (pur, testable)
# ----------------------------------------------------------------------------------------
def build_phases(preset, minutes):
    """-> [(nom, debut_s, fin_s, every, min_dur, vol)] ; minutes=0 : séance libre (phases de 10 min en boucle)."""
    if preset == "edging":
        return edging_phases(minutes)
    phases = PRESETS.get(preset, PRESETS["progressive"])[1]
    total = (minutes or 0) * 60
    if not total:
        loop = [p for p in phases if p[0] != "Redescente"] or phases
        out, t = [], 0.0
        for _ in range(30):                      # ~5 h
            for name, _share, every, mind, vol in loop:
                out.append((name, t, t + 600 / len(loop) * 2, every, mind, vol))
                t = out[-1][2]
        return out
    s = sum(p[1] for p in phases)
    out, t = [], 0.0
    for name, share, every, mind, vol in phases:
        d = total * share / s
        out.append((name, t, t + d, every, mind, vol))
        t += d
    return out


def phase_at(phases, t):
    for i, p in enumerate(phases):
        if p[1] <= t < p[2]:
            return i
    return len(phases) - 1 if phases and t >= phases[-1][2] else 0


def fmt(s):
    s = max(0, int(s))
    return "%d:%02d" % (s // 60, s % 60) if s < 3600 else "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


# ----------------------------------------------------------------------------------------
# Sélection de vidéos
# ----------------------------------------------------------------------------------------
def sources(conn, discreet):
    out = [("Coups de cœur", ("likes", None)), ("Les mieux notées (4-5 ★)", ("stars", None))]
    try:
        for p in db.list_playlists(conn):
            out.append(("Playlist : %s" % ("Playlist %d" % p["id"] if discreet else p["name"]), ("playlist", p["id"])))
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT t.name, COUNT(*) n FROM tags t JOIN video_tags vt ON vt.tag_id=t.id "
                              "WHERE vt.manual>=0 GROUP BY t.id HAVING n>=3 ORDER BY t.name"):
            out.append(("Tag : %s (%d)" % (r[0], r[1]), ("tag", r[0])))
    except Exception:
        pass
    try:
        for i, t in enumerate(db.categories(conn), 1):
            out.append(("Thème : %s" % ("Collection %d" % i if discreet else t["category"]), ("theme", t["category"])))
    except Exception:
        pass
    out.append(("Toute la bibliothèque", ("all", None)))
    return out


def source_ids(conn, kind, ident):
    try:
        if kind == "likes":
            return [r[0] for r in conn.execute("SELECT id FROM videos WHERE liked=1 AND missing=0 AND kind='video'")]
        if kind == "stars":
            return [r[0] for r in conn.execute("SELECT id FROM videos WHERE rating>=4 AND missing=0 AND kind='video'")]
        if kind == "playlist":
            return [r["id"] for r in db.playlist_videos(conn, int(ident))]
        if kind == "tag":
            return [r[0] for r in conn.execute(
                "SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id JOIN videos v ON v.id=vt.video_id "
                "WHERE t.name=? AND vt.manual>=0 AND v.missing=0 AND v.kind='video'", (ident,))]
        if kind == "theme":
            return list(db.category_video_ids(conn, str(ident)))
        return list(db.all_video_ids(conn))
    except Exception as e:
        log("seance source : %r" % e)
        return []


# ----------------------------------------------------------------------------------------
# Séance
# ----------------------------------------------------------------------------------------
class Session:
    def __init__(self, main, cfg, ids):
        self.main, self.cfg = main, cfg
        self.ids = ids
        self.phases = build_phases(cfg["preset"], cfg["minutes"])
        self.elapsed = 0.0
        self.last = time.monotonic()
        self.paused_until = None          # pause automatique (heure de reprise) ou "manual"
        self.next_pause = cfg["pause_every"] * 60 if cfg["pause_every"] else None
        self.idx = -1
        self.ending = None
        self.announce = 0.0
        mt = main.montage
        self.saved = {"every": mt.every, "min_dur": mt.min_dur, "montage": main.montage_btn.isChecked(),
                      "volume": main.music.volume}
        self.hud = None
        self.timer = QTimer()
        self.timer.timeout.connect(lambda: safe(self.tick))

    # --- demarrage
    def start(self):
        m = self.main
        ids = list(self.ids)
        random.shuffle(ids)
        if self.cfg["screens"] == "main":
            try:
                from .rythme import main_screen_number
                n = main_screen_number(m)
            except Exception:
                n = 1
            m.ctrl.play_queue(ids, n, "Séance", 0, True)
        else:
            m.ctrl.play_queue_multi(ids, "Séance", True)
        if not m.music.is_playing():
            m.music.toggle()
        if not m.montage_btn.isChecked():
            m.montage_btn.setChecked(True)
        if self.cfg.get("frise"):
            try:
                from . import rythme
                if rythme.ENGINE and not rythme.ENGINE.on:
                    rythme.setS(m, "on", "1")
            except Exception:
                pass
        if self.cfg.get("hud", True):
            self.make_hud()
        self.last = time.monotonic()
        self.timer.start(250)
        self.tick()
        m.status.setText("Séance démarrée")

    def make_hud(self):
        from .rythme import Overlay, main_screen_number
        self.hud = Overlay(self, self.paint_hud)
        scr = self.main.ctrl.screens()
        n = main_screen_number(self.main)
        self.hud_screen = scr[n - 1] if 0 < n <= len(scr) else QApplication.primaryScreen()
        self.place_hud(False)
        self.hud.show()

    def place_hud(self, big):
        g = self.hud_screen.geometry()
        if big:
            w, h = 560, 170
            self.hud.setGeometry(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2, w, h)
        else:
            w, h = 300, 64
            self.hud.setGeometry(g.x() + g.width() - w - 24, g.y() + 24, w, h)

    # --- horloge
    def tick(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        m = self.main
        if self.paused_until is not None:
            if self.paused_until != "manual" and now >= self.paused_until:
                self.resume()
            self.update_hud()
            return
        self.elapsed += dt
        if self.ending is not None:
            k = max(0.0, 1.0 - (now - self.ending) / 4.0)          # fin : la musique baisse en 4 s
            self.set_music(self.saved["volume"] * k * self.phases[self.idx][5])
            if k <= 0:
                self.finish()
            return
        total = self.phases[-1][2]
        if self.cfg["minutes"] and self.elapsed >= total:
            self.stop(gentle=True)
            return
        i = phase_at(self.phases, self.elapsed)
        if i != self.idx:
            self.enter(i)
        if self.announce and now > self.announce and self.hud is not None:
            self.announce = 0.0
            self.place_hud(False)
        if self.phases[self.idx][0] == "Stop" and self.hud is not None:
            self.announce = now + 9999
        if self.next_pause is not None and self.elapsed >= self.next_pause:
            self.next_pause += self.cfg["pause_every"] * 60
            self.pause(self.cfg["pause_len"])
        self.update_hud()

    def enter(self, i):
        prev = self.phases[self.idx][0] if self.idx >= 0 else None
        self.idx = i
        name, _a, _b, every, mind, vol = self.phases[i]
        m = self.main
        if name == "Stop":
            m.ctrl.each(lambda p: p.set_pause(True))
        elif prev == "Stop":
            m.ctrl.each(lambda p: p.set_pause(False))
        if name in EDGE and name != prev and self.hud is not None:
            self.announce = time.monotonic() + (9999 if name == "Stop" else 3.5)
            self.place_hud(True)
        mt = self.main.montage
        mt.every = every
        mt.min_dur = mind
        mt.next_i = 0
        self.ramp_to(self.saved["volume"] * vol)
        try:
            from . import rythme
            if rythme.ENGINE:
                e = rythme.ENGINE
                e.e_glitch = name in ("Intense", "Rapide") or e.e_glitch
        except Exception:
            pass

    def ramp_to(self, target, secs=5.0):
        start = self.main.music.mp.audio_get_volume()
        steps = 20
        for k in range(1, steps + 1):
            v = start + (target - start) * k / steps
            QTimer.singleShot(int(secs * 1000 * k / steps), lambda v=v: self.set_music(v))

    def set_music(self, v):
        try:
            self.main.music.mp.audio_set_volume(int(max(0, min(100, v))))
        except Exception:
            pass

    # --- pauses
    def pause(self, secs=None):
        m = self.main
        self.paused_until = "manual" if not secs else time.monotonic() + secs
        m.ctrl.each(lambda p: p.set_pause(True))
        if m.music.is_playing():
            m.music.pause()
        if self.hud is not None:
            self.place_hud(True)
        self.update_hud()

    def resume(self):
        m = self.main
        self.paused_until = None
        self.last = time.monotonic()
        m.ctrl.each(lambda p: p.set_pause(False))
        if not m.music.is_playing():
            m.music.toggle()
        if self.hud is not None:
            self.place_hud(False)
        self.update_hud()

    def toggle_pause(self):
        if self.paused_until is None:
            self.pause()
        else:
            self.resume()

    def next_phase(self):
        if self.idx + 1 < len(self.phases):
            self.elapsed = self.phases[self.idx + 1][1] + 0.1

    # --- fin
    def stop(self, gentle=True):
        if gentle and self.paused_until is None:
            self.ending = time.monotonic()
            self.main.ctrl.each(lambda p: p.osd("Fin de la séance", 3000))
        else:
            self.finish()

    def finish(self):
        global SESSION
        m = self.main
        self.timer.stop()
        m.ctrl.each(lambda p: p.set_pause(True))
        if m.music.is_playing():
            m.music.pause()
        mt = m.montage
        mt.every, mt.min_dur = self.saved["every"], self.saved["min_dur"]
        if m.montage_btn.isChecked() != self.saved["montage"]:
            m.montage_btn.setChecked(self.saved["montage"])
        self.set_music(self.saved["volume"])
        if self.hud is not None:
            self.hud.close()
            self.hud = None
        m.status.setText("Séance terminée (%s)" % fmt(self.elapsed))
        if SESSION is self:
            SESSION = None

    # --- affichage
    def update_hud(self):
        if self.hud is not None:
            self.hud.update()

    def paint_hud(self, p, w):
        W, H = w.width(), w.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 8, 20, 170))
        p.drawRoundedRect(QRectF(0, 0, W, H), 14, 14)
        name = self.phases[self.idx][0] if self.idx >= 0 else ""
        f = QFont("Segoe UI", 10)
        if self.paused_until is not None:
            left = "" if self.paused_until == "manual" else "  ·  reprise dans " + fmt(self.paused_until - time.monotonic())
            f.setPixelSize(40); f.setBold(True); p.setFont(f)
            p.setPen(QColor("#22f3ff"))
            p.drawText(QRectF(0, 18, W, 60), Qt.AlignCenter, "PAUSE")
            f.setPixelSize(17); f.setBold(False); p.setFont(f)
            p.setPen(QColor(236, 232, 255, 220))
            p.drawText(QRectF(0, 86, W, 30), Qt.AlignCenter, "Séance : %s%s" % (fmt(self.elapsed), left))
            p.drawText(QRectF(0, 116, W, 30), Qt.AlignCenter, "Ctrl+Maj+P pour reprendre")
            return
        total = self.phases[-1][2] if self.cfg["minutes"] else None
        if H > 100 and name in EDGE:
            a, b = self.phases[self.idx][1], self.phases[self.idx][2]
            col = {"Monte": "#ff2bd6", "Ralentis": "#8a5cff", "Stop": "#22f3ff", "Final": "#ffe94d"}[name]
            f.setPixelSize(54); f.setBold(True); p.setFont(f)
            p.setPen(QColor(col))
            p.drawText(QRectF(0, 14, W, 74), Qt.AlignCenter, EDGE[name])
            f.setPixelSize(18); f.setBold(False); p.setFont(f)
            p.setPen(QColor(236, 232, 255, 220))
            sub = {"Monte": "accélère", "Ralentis": "lentement", "Stop": "on ne touche plus — reprise dans " +
                   fmt(b - self.elapsed), "Final": "jusqu'au bout"}[name]
            p.drawText(QRectF(0, 96, W, 30), Qt.AlignCenter, sub)
            p.setBrush(QColor(255, 255, 255, 40)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(40, 140, W - 80, 6), 3, 3)
            k = max(0.0, min(1.0, (self.elapsed - a) / max(1.0, b - a)))
            p.setBrush(QColor(col)); p.drawRoundedRect(QRectF(40, 140, (W - 80) * k, 6), 3, 3)
            return
        f.setPixelSize(17); f.setBold(True); p.setFont(f)
        col = {"Calme": "#22f3ff", "Montée": "#8a5cff", "Rapide": "#ff9b3d", "Intense": "#ff2bd6",
               "Redescente": "#7fd1ae", "Monte": "#ff2bd6", "Ralentis": "#8a5cff", "Stop": "#22f3ff",
               "Final": "#ffe94d"}.get(name, "#ffffff")
        p.setPen(QColor(col))
        p.drawText(QRectF(16, 8, W - 32, 26), Qt.AlignLeft | Qt.AlignVCenter, name.upper())
        f.setBold(False); p.setFont(f)
        p.setPen(QColor(236, 232, 255, 230))
        txt = fmt(self.elapsed) + (" / " + fmt(total) if total else "")
        p.drawText(QRectF(16, 8, W - 32, 26), Qt.AlignRight | Qt.AlignVCenter, txt)
        # barre de progression de la phase
        a, b = self.phases[self.idx][1], self.phases[self.idx][2]
        k = max(0.0, min(1.0, (self.elapsed - a) / max(1.0, b - a)))
        p.setBrush(QColor(255, 255, 255, 40)); p.drawRoundedRect(QRectF(16, 42, W - 32, 6), 3, 3)
        p.setBrush(QColor(col)); p.drawRoundedRect(QRectF(16, 42, (W - 32) * k, 6), 3, 3)


# ----------------------------------------------------------------------------------------
# Commandes
# ----------------------------------------------------------------------------------------
def start_from_settings(main):
    global SESSION
    if SESSION is not None:
        return
    try:
        cfg = json.loads(S(main.conn, "cfg", "") or "null")
    except Exception:
        cfg = None
    if not cfg:
        return open_dialog(main)
    begin(main, cfg)


def begin(main, cfg):
    global SESSION
    if not main.music.files:
        main.status.setText("Séance : choisis d'abord un dossier de musique (bouton « Dossier musique… »)")
        return False
    ids = source_ids(main.conn, *cfg["source"])
    if not ids:
        main.status.setText("Séance : aucune vidéo dans cette sélection")
        return False
    if SESSION is not None:
        SESSION.finish()
    SESSION = Session(main, cfg, ids)
    SESSION.start()
    return True


def stop():
    if SESSION is not None:
        SESSION.stop(gentle=True)


def toggle(main):
    if SESSION is None:
        start_from_settings(main)
    else:
        stop()


def remote(main, sub):
    if sub == "start":
        start_from_settings(main) if SESSION is None else None
    elif sub == "stop":
        stop()
    elif sub == "pause" and SESSION is not None:
        SESSION.toggle_pause()
    elif sub == "next" and SESSION is not None:
        SESSION.next_phase()


def open_dialog(main):
    safe(lambda: SeanceDialog(main).exec())


def SeanceDialog(main):
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton,
                                   QVBoxLayout)
    conn = main.conn
    try:
        cfg = json.loads(S(conn, "cfg", "") or "null") or {}
    except Exception:
        cfg = {}
    d = QDialog(main)
    d.setWindowTitle("Séance")
    d.setMinimumWidth(560)
    lay = QVBoxLayout(d)
    f = QFormLayout()
    lay.addLayout(f)

    def combo(items, current):
        c = QComboBox()
        for lab, val in items:
            c.addItem(lab, val)
        vals = [v for _l, v in items]
        if current in vals:
            c.setCurrentIndex(vals.index(current))
        return c

    src_items = sources(conn, main.discreet)
    cur_src = tuple(cfg.get("source", ())) or None
    src = combo([(l, v) for l, v in src_items], cur_src)
    f.addRow("Vidéos", src)
    preset = combo([(v[0], k) for k, v in PRESETS.items()], cfg.get("preset", "progressive"))
    f.addRow("Déroulé", preset)
    mins = combo([("15 min", 15), ("30 min", 30), ("45 min", 45), ("1 h", 60), ("1 h 30", 90), ("2 h", 120),
                  ("Libre (sans fin, phases en boucle)", 0)], cfg.get("minutes", 45))
    f.addRow("Durée", mins)
    scr = combo([("Tous les écrans", "all"), ("Écran principal seulement", "main")], cfg.get("screens", "all"))
    f.addRow("Écrans", scr)
    pe = combo([("Aucune", 0), ("Toutes les 10 min", 10), ("Toutes les 15 min", 15), ("Toutes les 20 min", 20),
                ("Toutes les 30 min", 30)], cfg.get("pause_every", 0))
    pl = combo([("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300)], cfg.get("pause_len", 60))
    row = QHBoxLayout(); row.addWidget(pe, 1); row.addWidget(QLabel("durée")); row.addWidget(pl)
    f.addRow("Pauses automatiques", row)
    frise = QCheckBox("Afficher la frise néon (vidéos pmv et montage)")
    frise.setChecked(cfg.get("frise", True))
    hud = QCheckBox("Minuteur et phase en haut à droite de l'écran principal")
    hud.setChecked(cfg.get("hud", True))
    f.addRow("", frise)
    f.addRow("", hud)
    info = QLabel("La musique en cours (ou le dossier de musique) donne le rythme ; chaque phase règle la vitesse "
                  "des coupes et le volume.<br>Ctrl+Maj+S : démarrer / arrêter · Ctrl+Maj+P : pause · "
                  "Ctrl+Maj+N : phase suivante · téléphone : onglet Musique.")
    info.setWordWrap(True)
    info.setStyleSheet("color:#8b8c99")
    lay.addWidget(info)
    btns = QHBoxLayout()
    btns.addStretch(1)
    b_stop = QPushButton("Arrêter la séance")
    b_stop.setEnabled(SESSION is not None)
    b_close = QPushButton("Fermer")
    b_go = QPushButton("Démarrer")
    b_go.setDefault(True)
    btns.addWidget(b_stop); btns.addWidget(b_close); btns.addWidget(b_go)
    lay.addLayout(btns)

    def go():
        c = {"source": list(src.currentData()), "preset": preset.currentData(), "minutes": mins.currentData(),
             "screens": scr.currentData(), "pause_every": pe.currentData(), "pause_len": pl.currentData(),
             "frise": frise.isChecked(), "hud": hud.isChecked()}
        setS(conn, "cfg", json.dumps(c))
        if begin(main, c):
            d.accept()
    b_go.clicked.connect(go)
    b_close.clicked.connect(d.reject)
    b_stop.clicked.connect(lambda: (stop(), d.accept()))
    return d


def menu(main, m):
    label = "Séance en cours : arrêter" if SESSION is not None else "Séance…"
    if SESSION is not None:
        m.addAction(label).triggered.connect(stop)
    else:
        m.addAction(label).triggered.connect(lambda: open_dialog(main))


def install(main):
    from ..players import Controller
    main._seance_keys = []
    for seq, fn in (("Ctrl+Shift+S", lambda: toggle(main)),
                    ("Ctrl+Shift+P", lambda: SESSION and SESSION.toggle_pause()),
                    ("Ctrl+Shift+N", lambda: SESSION and SESSION.next_phase())):
        sc = QShortcut(QKeySequence(seq), main)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(lambda f=fn: safe(f))
        main._seance_keys.append(sc)
    # la touche panique arrete la seance net
    if not getattr(Controller, "_extras_seance", False):
        orig = Controller.panic

        def panic(self, *a, **kw):
            if SESSION is not None and not self.hidden:
                safe(SESSION.finish)
            return orig(self, *a, **kw)
        Controller.panic = panic
        Controller._extras_seance = True
    # les extraits du montage viennent de la selection de la seance
    from ..montage import Montage
    if not getattr(Montage, "_extras_seance", False):
        orig_pick = Montage.pick_next

        def pick_next(self, pw):
            s = SESSION
            if s is not None and s.ids and not getattr(pw, "is_tile", False):
                showing = {p.video_id for p in self.players()}
                choices = [i for i in s.ids if i not in showing] or s.ids
                for _ in range(10):
                    vid = random.choice(choices)
                    row, path = self.ctrl.resolve(vid)
                    if path:
                        dur = row["duration"] or 0
                        start = random.uniform(0.2 * dur, max(0.2 * dur, dur - self.min_dur * 2 - 5)) \
                            if dur > self.min_dur * 2 + 10 else 0
                        return row, path, start
            return orig_pick(self, pw)
        Montage.pick_next = pick_next
        Montage._extras_seance = True
