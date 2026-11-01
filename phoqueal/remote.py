"""Telecommande flottante (mode souris) : tout controler d'un clic, sur tous les ecrans.

Petit panneau toujours au premier plan, deplacable n'importe ou (glisser par la barre du haut).
Une ligne « Tous », une ligne par ecran, une ligne musique.
"""
import time

from PySide6.QtCore import QPoint, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget,
)

from . import db, view
from .players import fmt_time

STYLE = """
QWidget#remote { background: rgba(18,18,24,235); border: 1px solid #34343f; border-radius: 10px; }
QLabel { color: #d8d8e0; background: transparent; }
QLabel#rtitle { font-weight: 700; letter-spacing: 2px; color: #9aa6c8; }
QLabel#rscreen { background: #2a2f45; color: white; border-radius: 4px; font-weight: 700; }
QLabel#rdim { color: #7c7c88; }
QPushButton { background: #23232c; color: #e6e6ee; border: 1px solid #30303a; border-radius: 5px;
              padding: 0; font-family: 'Segoe UI Symbol', 'Segoe UI'; font-size: 11pt; }
QPushButton:hover { background: #31313c; }
QPushButton:checked { background: #3b5bb5; border-color: #5b8def; color: white; }
QPushButton:disabled { color: #4a4a55; background: #1b1b22; }
QPushButton#rheart { color: #ff4d5e; font-size: 13pt; }
QPushButton#rheart:checked { background: #4a1e26; border-color: #ff4d5e; color: #ff4d5e; }
QPushButton#rpanic { background: #6b2226; border-color: #8a2d33; font-weight: 700; }
QPushButton#rpanic:hover { background: #8a2d33; }
QSlider::groove:horizontal { height: 4px; background: #34343f; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #5b8def; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: white; border-radius: 6px; }
"""

ICON = {"prev": "\u23EE", "play": "\u25B6", "pause": "\u23F8", "next": "\u23ED", "back": "\u23EA",
        "fwd": "\u23E9", "stop": "\u23F9", "loop": "\u21BB", "rand": "\u21C4", "mute": "\U0001F507",
        "sound": "\U0001F50A"}


def button(text, tip, fn, checkable=False, w=30):
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setFixedSize(QSize(w, 28))
    b.setFocusPolicy(Qt.NoFocus)
    b.setCheckable(checkable)
    b.clicked.connect(lambda _=False: fn())
    return b


class SeekSlider(QSlider):
    """Ligne de temps : un clic place directement la lecture a cet endroit."""

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Cliquer ou glisser pour se déplacer dans la vidéo")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                         int(e.position().x()), self.width()))
        super().mousePressEvent(e)


class TitleCell(QWidget):
    """Titre de la video en cours + bouton coeur juste a cote."""

    def __init__(self, on_like):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.heart = QPushButton("\u2661")
        self.heart.setObjectName("rheart")
        self.heart.setCheckable(True)
        self.heart.setFixedSize(QSize(28, 28))
        self.heart.setFocusPolicy(Qt.NoFocus)
        self.heart.setToolTip("J'aime / je n'aime plus (touche L)")
        self.heart.clicked.connect(lambda _=False: on_like())
        self.label = QLabel()
        self.label.setObjectName("rdim")
        self.label.setFixedWidth(150)
        lay.addWidget(self.heart)
        lay.addWidget(self.label)

    def setText(self, t):
        self.label.setText(t)

    def setToolTip(self, t):
        self.label.setToolTip(t)

    def fontMetrics(self):
        return self.label.fontMetrics()

    def set_liked(self, liked):
        self.heart.setChecked(liked)
        self.heart.setText("\u2665" if liked else "\u2661")


def slider(fn, tip):
    s = QSlider(Qt.Horizontal)
    s.setRange(0, 150)
    s.setFixedWidth(90)
    s.setToolTip(tip)
    s.setFocusPolicy(Qt.NoFocus)
    s.valueChanged.connect(fn)
    return s


class Remote(QWidget):
    def __init__(self, main):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.main = main
        self.ctrl = main.ctrl
        self.music = main.music
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Phoque Al - télécommande")
        self._drag = None
        self.compact = db.get_setting(main.conn, "remote_compact", "0") == "1"
        self.pinned = db.get_setting(main.conn, "remote_pinned", "0") == "1"
        self.enabled = False
        self.hide_after = 5.0
        self.quiet_until = 0.0
        self.wake_on_move = db.get_setting(main.conn, "remote_wake", "1") == "1"
        self.last_activity = time.monotonic()
        self._last_cursor = QCursor.pos()
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.finished.connect(self._fade_done)

        self.panel = QWidget(self)
        self.panel.setObjectName("remote")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        self.panel.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.panel)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(10, 6, 10, 10)
        lay.setSpacing(6)

        # --- barre du haut (poignee de deplacement)
        head = QHBoxLayout()
        title = QLabel("\u2630  PHOQUE AL")
        title.setObjectName("rtitle")
        title.setToolTip("Glisser ici pour déplacer la télécommande")
        self.home_btn = button("\u2302", "Afficher l'accueil", lambda: self.act(lambda: self.ctrl.focus(0)))
        self.compact_btn = button("\u25B4", "Réduire / agrandir la télécommande", self.toggle_compact)
        self.pin_btn = button("\U0001F4CC", "Épingler : ne plus masquer automatiquement après 5 s", self.toggle_pin, True)
        self.pin_btn.setChecked(self.pinned)
        panic = button("!", "Touche panique : tout en pause et masqué", lambda: self.ctrl.panic(), w=30)
        panic.setObjectName("rpanic")
        close = button("\u2715", "Masquer la télécommande (Ctrl + T)", lambda: self.main.toggle_remote())
        head.addWidget(title)
        head.addStretch(1)
        ident = button("#", "Afficher le numéro de chaque écran", lambda: self.act(lambda: self.ctrl.identify()))
        for b in (self.home_btn, ident, self.pin_btn, self.compact_btn, panic, close):
            head.addWidget(b)
        lay.addLayout(head)

        # --- grille : Tous + un ecran par ligne
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(6)
        lay.addLayout(self.grid)
        self.rows = {}
        self._build_all_row(0)
        self.timelines = {}
        for i, n in enumerate(self.ctrl.all_numbers(), 1):
            self._build_screen_row(2 * i - 1, n)

        # --- musique
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #30303a;")
        lay.addWidget(line)
        mus = QHBoxLayout()
        mus.setSpacing(4)
        lab = QLabel("\u266A")
        lab.setObjectName("rscreen")
        lab.setFixedSize(26, 26)
        lab.setAlignment(Qt.AlignCenter)
        mus.addWidget(lab)
        self.m_prev = button(ICON["prev"], "Musique : morceau précédent", lambda: self.music.previous())
        self.m_play = button(ICON["play"], "Musique : lecture / pause", lambda: self.music.toggle())
        self.m_next = button(ICON["next"], "Musique : morceau suivant", lambda: self.music.next())
        self.m_shuffle = button(ICON["rand"], "Musique : lecture aléatoire", lambda: self.main.music_shuffle.toggle(), True)
        self.m_montage = button("\u2669", "Montage au rythme de la musique", lambda: self.main.montage_btn.toggle(), True)
        self.m_vol = QSlider(Qt.Horizontal)
        self.m_vol.setRange(0, 100)
        self.m_vol.setFixedWidth(90)
        self.m_vol.setToolTip("Volume de la musique")
        self.m_vol.setFocusPolicy(Qt.NoFocus)
        self.m_vol.valueChanged.connect(lambda v: (self.music.set_volume(v), self.main.music_volume.setValue(v)))
        self.m_track = QLabel()
        self.m_track.setObjectName("rdim")
        self.m_track.setFixedWidth(150)
        for w in (self.m_prev, self.m_play, self.m_next, self.m_shuffle, self.m_montage, self.m_vol, self.m_track):
            mus.addWidget(w)
        mus.addStretch(1)
        lay.addLayout(mus)

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        # surveillance de la souris : reapparition au moindre mouvement, disparition apres 5 s d'inaction
        self.watch = QTimer(self)
        self.watch.setInterval(150)
        self.watch.timeout.connect(self.watch_activity)
        self.apply_compact()

    # --- construction des lignes ------------------------------------------------------
    def _label(self, text, tip):
        lab = QLabel(text)
        lab.setObjectName("rscreen")
        lab.setFixedSize(26, 26)
        lab.setAlignment(Qt.AlignCenter)
        lab.setToolTip(tip)
        return lab

    def _build_all_row(self, r):
        lab = self._label("\u2217", "Tous les écrans")
        name = QLabel("Tous les écrans")
        name.setFixedWidth(150)
        each = self.ctrl.each
        widgets = [
            lab, name,
            button(ICON["prev"], "Tous : précédent", lambda: self.act(lambda: each(lambda p: self.prev(p)))),
            button(ICON["pause"], "Tous : pause / lecture", lambda: self.act(self.ctrl.pause_all)),
            button(ICON["next"], "Tous : suivant", lambda: self.act(lambda: each(lambda p: self.next(p)))),
            button(ICON["back"], "Tous : -10 s", lambda: self.act(lambda: each(lambda p: p.seek(-10)))),
            button(ICON["fwd"], "Tous : +10 s", lambda: self.act(lambda: each(lambda p: p.seek(10)))),
            button(ICON["loop"], "Tous : boucle", lambda: self.act(lambda: self.set_all("loop")), True),
            button(ICON["rand"], "Tous : aléatoire", lambda: self.act(lambda: self.set_all("random")), True),
            button(ICON["sound"], "Tous : couper / remettre le son", lambda: self.act(self.ctrl.mute_all)),
            button("\u25AD", "Tous : changer le format d'image (bandes noires, remplir, 4:3…)",
                   lambda: self.act(self.ctrl.cycle_view_all)),
            slider(lambda v: self.set_volume(None, v), "Volume de tous les écrans"),
            button(ICON["stop"], "Tous : arrêter (fermer les lecteurs)", lambda: self.act(self.ctrl.close_all)),
            self._rotate_button(),
        ]
        for c, w in enumerate(widgets):
            self.grid.addWidget(w, r, c)
        self.rows[0] = widgets

    def _rotate_button(self):
        b = button("\u27F3", "Décaler les vidéos d'un écran : 1 → 2 → 3 → 1  (clic droit : dans l'autre sens)",
                   lambda: self.act(lambda: self.ctrl.rotate_screens(1)))
        b.setContextMenuPolicy(Qt.CustomContextMenu)
        b.customContextMenuRequested.connect(lambda _pos: self.act(lambda: self.ctrl.rotate_screens(-1)))
        return b

    def _build_screen_row(self, r, n):
        lab = self._label(str(n), "Écran %d : clic = aller sur ce lecteur" % n)
        lab.mousePressEvent = lambda e, n=n: self.act(lambda: self.ctrl.focus(n))
        name = TitleCell(lambda n=n: self.like(n))

        def on(fn):
            def run():
                p = self.ctrl.players.get(n)
                if p is not None:
                    self.act(lambda: fn(p))
            return run

        widgets = [
            lab, name,
            button(ICON["prev"], "Précédent", on(self.prev)),
            button(ICON["pause"], "Pause / lecture", on(lambda p: p.toggle_pause())),
            button(ICON["next"], "Suivant", on(self.next)),
            button(ICON["back"], "-10 s", on(lambda p: p.seek(-10))),
            button(ICON["fwd"], "+10 s", on(lambda p: p.seek(10))),
            button(ICON["loop"], "Boucle", on(lambda p: self.toggle_flag(p, "loop")), True),
            button(ICON["rand"], "Aléatoire", on(lambda p: self.toggle_flag(p, "random")), True),
            button(ICON["sound"], "Couper / remettre le son", on(lambda p: p.toggle_mute())),
            button("\u25AD", "Format d'image", on(lambda p: p.cycle_view())),
            slider(lambda v, n=n: self.set_volume(n, v), "Volume de l'écran %d" % n),
            button(ICON["stop"], "Arrêter (fermer ce lecteur)", on(lambda p: p.close())),
        ]
        for c, w in enumerate(widgets):
            self.grid.addWidget(w, r, c)
        self.rows[n] = widgets

        # ligne de temps sous les boutons
        tl = QWidget()
        hl = QHBoxLayout(tl)
        hl.setContentsMargins(0, 0, 0, 4)
        cur = QLabel("0:00")
        cur.setObjectName("rdim")
        cur.setFixedWidth(52)
        cur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar = SeekSlider()
        length = QLabel("0:00")
        length.setObjectName("rdim")
        length.setFixedWidth(52)
        hl.addWidget(cur)
        hl.addWidget(bar, 1)
        hl.addWidget(length)
        bar.sliderMoved.connect(lambda v, n=n: self.preview_seek(n, v))
        bar.sliderReleased.connect(lambda n=n: self.do_seek(n))
        self.grid.addWidget(tl, r + 1, 1, 1, len(widgets) - 1)
        self.timelines[n] = (tl, cur, bar, length)

    def like(self, n):
        p = self.ctrl.players.get(n)
        if p is not None:
            self.act(lambda: self.ctrl.toggle_like(p))

    # --- ligne de temps ------------------------------------------------------------------
    def media_player(self, n):
        """Lecteur VLC vise par la ligne de temps (en mosaique : la case selectionnee)."""
        p = self.ctrl.players.get(n)
        if p is None:
            return None, None
        if getattr(p, "is_mosaic", False):
            return p.selected, p.selected.player
        return p, p.player

    def preview_seek(self, n, value):
        _, mp = self.media_player(n)
        if mp is not None and mp.get_length() > 0:
            self.timelines[n][1].setText(fmt_time(mp.get_length() * value / 1000))
        self.last_activity = time.monotonic()

    def do_seek(self, n):
        owner, mp = self.media_player(n)
        if mp is None:
            return
        length = mp.get_length()
        if length > 0:
            t = int(length * self.timelines[n][2].value() / 1000)
            mp.set_time(min(t, length - 500))
            owner.osd("%s / %s" % (fmt_time(t), fmt_time(length)), 1500)
        self.last_activity = time.monotonic()

    def refresh_timeline(self, n):
        tl, cur, bar, length_lab = self.timelines[n]
        owner, mp = self.media_player(n)
        length = mp.get_length() if mp is not None else -1
        ok = mp is not None and length > 0
        bar.setEnabled(ok)
        if not ok:
            cur.setText("—")
            length_lab.setText("—")
            if not bar.isSliderDown():
                bar.setValue(0)
            return
        t = max(0, mp.get_time())
        length_lab.setText(fmt_time(length))
        if not bar.isSliderDown():
            cur.setText(fmt_time(t))
            bar.blockSignals(True)
            bar.setValue(int(1000 * t / length))
            bar.blockSignals(False)

    # --- actions ---------------------------------------------------------------------------
    def act(self, fn):
        self.last_activity = time.monotonic()
        fn()
        self.refresh()

    def prev(self, p):
        if getattr(p, "is_mosaic", False):
            p.osd("Précédent : non disponible en mosaïque")
        else:
            self.ctrl.queue_step(p, -1, manual=True)

    def next(self, p):
        if getattr(p, "is_mosaic", False):
            p.next_all()
        else:
            self.ctrl.queue_step(p, 1, manual=True)

    @staticmethod
    def flag(p, which):
        if getattr(p, "is_mosaic", False):
            return p.config["end"] == "replay" if which == "loop" else p.config["order"] == "random"
        return p.loop if which == "loop" else p.random_mode

    def toggle_flag(self, p, which, value=None):
        value = (not self.flag(p, which)) if value is None else value
        if getattr(p, "is_mosaic", False):
            if which == "loop":
                p.config["end"] = "replay" if value else "next"
                p.osd("Fin de vidéo : rejouer" if value else "Fin de vidéo : suivante", 1500)
            else:
                p.config["order"] = "random" if value else "ordered"
                p.build_pool()
                p.osd("Ordre aléatoire" if value else "Dans l'ordre", 1500)
        elif which == "loop":
            p.loop = value
            p.osd("Boucle activée" if value else "Boucle désactivée")
        else:
            p.random_mode = value
            p.osd("Aléatoire activé" if value else "Aléatoire désactivé")

    def set_all(self, which):
        players = list(self.ctrl.players.values())
        if not players:
            return
        value = not all(self.flag(p, which) for p in players)
        for p in players:
            self.toggle_flag(p, which, value)

    def set_volume(self, n, v):
        targets = list(self.ctrl.players.values()) if n is None else [self.ctrl.players.get(n)]
        for p in targets:
            if p is None:
                continue
            if getattr(p, "is_mosaic", False):
                for t in p.tiles:
                    t.volume = v
                    t.player.audio_set_volume(v)
            else:
                p.volume = v
                p.player.audio_set_volume(v)

    # --- rafraichissement ---------------------------------------------------------------------
    def refresh(self):
        players = self.ctrl.players
        discreet = self.main.discreet
        for n, widgets in self.rows.items():
            name, btns = widgets[1], widgets[2:]
            vol = widgets[-2]
            if n == 0:
                active = bool(players)
                if players:
                    any_playing = any(p.is_playing() for p in players.values())
                    btns[1].setText(ICON["pause"] if any_playing else ICON["play"])
                    btns[5].setChecked(all(self.flag(p, "loop") for p in players.values()))
                    btns[6].setChecked(all(self.flag(p, "random") for p in players.values()))
                    btns[7].setText(ICON["mute"] if all(p.muted for p in players.values()) else ICON["sound"])
                for b in btns:
                    b.setEnabled(active)
                continue
            p = players.get(n)
            self.refresh_timeline(n)
            for b in btns:
                b.setEnabled(p is not None)
            name.heart.setEnabled(p is not None)
            if p is None:
                name.setText("libre")
                name.set_liked(False)
                continue
            owner = p.selected if getattr(p, "is_mosaic", False) else p
            name.set_liked(bool(owner.video_id) and db.is_liked(self.main.conn, owner.video_id))
            if getattr(p, "is_mosaic", False):
                text = "Mosaïque" if discreet else "Mosaïque · %s" % p.config.get("name", "")
            elif discreet:
                text = "Lecture en cours"
            else:
                text = p.display_title() if hasattr(p, "display_title") else p.title
            name.setText(name.fontMetrics().elidedText(text, Qt.ElideRight, 146))
            name.setToolTip("" if discreet else text)
            btns[1].setText(ICON["pause"] if p.is_playing() else ICON["play"])
            btns[5].setChecked(self.flag(p, "loop"))
            btns[6].setChecked(self.flag(p, "random"))
            btns[7].setText(ICON["mute"] if p.muted else ICON["sound"])
            btns[8].setToolTip("Format d'image : %s  (clic = changer)" % view.LABELS.get(p.view_mode, p.view_mode))
            if not vol.isSliderDown():
                vol.blockSignals(True)
                vol.setValue(p.volume)
                vol.blockSignals(False)
        # musique
        self.m_play.setText(ICON["pause"] if self.music.is_playing() else ICON["play"])
        self.m_shuffle.setChecked(self.music.shuffle)
        self.m_montage.setChecked(self.main.montage_btn.isChecked())
        if not self.m_vol.isSliderDown():
            self.m_vol.blockSignals(True)
            self.m_vol.setValue(self.music.volume)
            self.m_vol.blockSignals(False)
        name = self.music.current_name() if self.music.files else ""
        self.m_track.setText(self.m_track.fontMetrics().elidedText(name or "—", Qt.ElideRight, 146))

    # --- mode compact -----------------------------------------------------------------------
    def toggle_compact(self):
        self.compact = not self.compact
        db.set_setting(self.main.conn, "remote_compact", "1" if self.compact else "0")
        self.apply_compact()

    def apply_compact(self):
        """Compact : on masque les titres et les boutons -10 s / +10 s."""
        for widgets in self.rows.values():
            for i in (1, 5, 6):
                w = widgets[i]
                (w.label if isinstance(w, TitleCell) else w).setVisible(not self.compact)
        self.m_track.setVisible(not self.compact)
        self.compact_btn.setText("\u25BE" if self.compact else "\u25B4")
        self.adjustSize()

    # --- deplacement -----------------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            db.set_setting(self.main.conn, "remote_pos", "%d,%d" % (self.x(), self.y()))

    # --- apparition / disparition automatique ---------------------------------------------------
    def set_enabled(self, on):
        self.enabled = on
        if on:
            self.last_activity = time.monotonic()
            self.show_at_saved_place()
            self.watch.start()
        else:
            self.watch.stop()
            self.fade.stop()
            self.hide()

    def playback_started(self, show):
        """Une video vient de demarrer : on montre la telecommande, ou on la fait discrete un instant."""
        if show:
            self.last_activity = time.monotonic()
            self.fade_in()
            self.raise_()
        else:
            self.quiet_until = time.monotonic() + 2.0     # ignore le mouvement du double-clic
            self.fade.stop()
            self.hide()

    def toggle_pin(self):
        self.pinned = self.pin_btn.isChecked()
        db.set_setting(self.main.conn, "remote_pinned", "1" if self.pinned else "0")
        self.last_activity = time.monotonic()

    def watch_activity(self):
        pos = QCursor.pos()
        now = time.monotonic()
        if pos != self._last_cursor:
            self._last_cursor = pos
            self.last_activity = now
            if now < self.quiet_until:
                return
            if (not self.isVisible() or self.fade.endValue() == 0.0) and self.wake_on_move:
                self.fade_in()
            return
        if (self.isVisible() and not self.pinned and self._drag is None
                and not self.frameGeometry().contains(pos)
                and now - self.last_activity > self.hide_after
                and self.fade.state() != QPropertyAnimation.Running):
            self.fade_out()

    def fade_in(self):
        self.fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
            self.refresh()
            self.timer.start()
        self.fade.setDuration(150)
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(1.0)
        self.fade.start()

    def fade_out(self):
        self.fade.stop()
        self.fade.setDuration(400)
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_done(self):
        if self.fade.endValue() == 0.0:
            self.hide()

    def show_at_saved_place(self):
        pos = db.get_setting(self.main.conn, "remote_pos")
        target = None
        if pos:
            x, y = (int(v) for v in pos.split(","))
            target = QPoint(x, y)
            if not any(s.availableGeometry().contains(target) for s in self.ctrl.screens()):
                target = None
        if target is None:
            geo = self.main.screen().availableGeometry() if self.main.screen() else self.ctrl.screens()[0].availableGeometry()
            self.adjustSize()
            target = QPoint(geo.right() - self.width() - 30, geo.bottom() - self.height() - 30)
        self.move(target)
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self.refresh()
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)
