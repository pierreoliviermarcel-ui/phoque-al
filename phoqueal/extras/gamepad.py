"""Manette de jeu (Xbox ou compatible XInput, filaire ou sans fil) comme telecommande.

A  pause / lecture          B  son coupe / retabli        X  ♥ j'aime         Y  video suivante
LB / RB  ecran precedent / suivant (l'ecran pilote s'affiche)
LT / RT  -10 s / +10 s (maintenu : repete)
Croix ← →  scene precedente / suivante     Croix ↑ ↓  volume
Stick gauche ← →  avance / recul rapide progressif
Stick droit ↑ ↓  volume de la musique ; ← →  morceau precedent / suivant
L3 (clic stick gauche)  marquer un moment     R3  format d'image
Start  afficher les numeros des ecrans        Back (View)  PANIQUE
Sans video en cours : A lance la video de la banniere, LB / RB changent de diapositive.
"""
import ctypes
import sys
import time

from PySide6.QtCore import QTimer

from .. import db
from .common import log

BTN = {"up": 0x0001, "down": 0x0002, "left": 0x0004, "right": 0x0008, "start": 0x0010, "back": 0x0020,
       "l3": 0x0040, "r3": 0x0080, "lb": 0x0100, "rb": 0x0200, "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000}
REPEAT = {"up", "down", "lt", "rt"}


class _Pad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _Pad)]


def load_xinput():
    if sys.platform != "win32":
        return None
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = getattr(ctypes.windll, name)
            dll.XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(_State)]
            dll.XInputGetState.restype = ctypes.c_uint
            return dll
        except (OSError, AttributeError):
            continue
    return None


class Gamepad:
    def __init__(self, main):
        self.main = main
        self.ctrl = main.ctrl
        self.dll = load_xinput()
        self.index = None
        self.prev = set()
        self.held = {}                    # bouton -> prochain instant de repetition
        self.screen = None
        self.stick_next = 0.0
        self.music_next = 0.0
        self.timer = QTimer(main)
        self.timer.timeout.connect(self.poll)
        self.enabled = db.get_setting(main.conn, "gamepad_on", "1") == "1"
        if self.dll is not None and self.enabled:
            self.timer.start(1000)

    def set_enabled(self, on):
        self.enabled = bool(on)
        db.set_setting(self.main.conn, "gamepad_on", "1" if on else "0")
        if on and self.dll is not None:
            self.timer.start(1000)
        else:
            self.timer.stop()

    # --- lecture de la manette ------------------------------------------------------------
    def read(self):
        st = _State()
        if self.index is not None:
            if self.dll.XInputGetState(self.index, ctypes.byref(st)) == 0:
                return st.Gamepad
            self.index = None
            self.main.status.setText("Manette déconnectée")
            self.timer.setInterval(1000)
            return None
        for i in range(4):
            if self.dll.XInputGetState(i, ctypes.byref(st)) == 0:
                self.index = i
                self.timer.setInterval(33)
                self.main.status.setText("Manette connectée")
                return st.Gamepad
        return None

    def poll(self):
        try:
            pad = self.read()
            if pad is None:
                return
            pressed = {k for k, m in BTN.items() if pad.wButtons & m}
            if pad.bLeftTrigger > 120:
                pressed.add("lt")
            if pad.bRightTrigger > 120:
                pressed.add("rt")
            now = time.monotonic()
            for b in pressed - self.prev:
                self.held[b] = now + 0.45
                self.press(b)
            for b in pressed & self.prev:
                if b in REPEAT and now >= self.held.get(b, now + 1):
                    self.held[b] = now + 0.12
                    self.press(b)
            self.prev = pressed
            self.sticks(pad, now)
        except Exception as e:
            log("manette : %r" % e)

    # --- cible : l'ecran pilote par la manette ----------------------------------------------
    def target(self):
        players = self.ctrl.players
        if not players:
            return None
        if self.screen not in players:
            la = getattr(self.ctrl, "last_active", None)
            self.screen = la if la in players else min(players)
        return players[self.screen]

    def cycle(self, step):
        nums = sorted(self.ctrl.players)
        if not nums:
            self.main.hero_call("go", step)
            return
        cur = self.screen if self.screen in nums else nums[0]
        self.screen = nums[(nums.index(cur) + step) % len(nums)]
        p = self.ctrl.players[self.screen]
        p.osd("Manette → écran %d" % self.screen, 1200)

    # --- actions -------------------------------------------------------------------------------
    def press(self, b):
        ctrl = self.ctrl
        if b == "back":
            return ctrl.panic()
        if b == "start":
            return ctrl.identify()
        if b in ("lb", "rb"):
            return self.cycle(-1 if b == "lb" else 1)
        p = self.target()
        if p is None:
            if b == "a":
                self.main.hero_call("play_current")
            return
        own = p.selected if getattr(p, "is_mosaic", False) else p
        if b == "a":
            p.toggle_pause()
        elif b == "b":
            p.toggle_mute()
        elif b == "x":
            ctrl.toggle_like(p)
        elif b == "y":
            if getattr(p, "is_mosaic", False):
                p.next_all()
            else:
                ctrl.queue_step(p, 1, manual=True)
        elif b == "lt":
            p.seek(-10)
        elif b == "rt":
            p.seek(10)
        elif b in ("up", "down"):
            p.change_volume(5 if b == "up" else -5)
        elif b in ("left", "right"):
            from . import scenes
            if scenes.WORKER is not None and own.video_id and own.video_id > 0:
                scenes.jump(p, 1 if b == "right" else -1)
            else:
                p.seek(60 if b == "right" else -60)
        elif b == "l3":
            from . import moments
            moments.mark(p)
        elif b == "r3":
            p.cycle_view()

    def sticks(self, pad, now):
        lx = pad.sThumbLX / 32768.0
        if abs(lx) > 0.35 and now >= self.stick_next:
            p = self.target()
            if p is not None:
                step = int(5 + 55 * (abs(lx) - 0.35) / 0.65)        # 5 s a 60 s selon l'inclinaison
                p.seek(step if lx > 0 else -step)
            self.stick_next = now + 0.25
        ry = pad.sThumbRY / 32768.0
        rx = pad.sThumbRX / 32768.0
        mu = self.main.music
        if now >= self.music_next:
            if abs(ry) > 0.5:
                mu.change_volume(5 if ry > 0 else -5)
                self.music_next = now + 0.15
            elif abs(rx) > 0.7:
                mu.next() if rx > 0 else mu.previous()
                self.music_next = now + 0.8


HELP = __doc__.split("\n\n", 1)[1]


def install(main):
    main._gamepad = Gamepad(main)


def menu(main, parent):
    gp = getattr(main, "_gamepad", None)
    sub = parent.addMenu("Manette de jeu")
    a = sub.addAction("Utiliser une manette (Xbox / compatible)")
    a.setCheckable(True)
    a.setChecked(bool(gp and gp.enabled))
    a.setEnabled(bool(gp and gp.dll is not None))
    a.triggered.connect(lambda on: gp.set_enabled(on))
    state = "connectée" if gp and gp.index is not None else "non détectée"
    info = sub.addAction("État : manette %s" % state)
    info.setEnabled(False)

    def show_help():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(main, "Manette de jeu", HELP)

    sub.addAction("Boutons de la manette…").triggered.connect(lambda: show_help())
    return sub
