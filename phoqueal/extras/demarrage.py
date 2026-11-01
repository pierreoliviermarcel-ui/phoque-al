"""Lancer Phoque Al au démarrage de Windows (réglage dans « ⋯ → Démarrage de Windows »).

- Inscription dans la liste de démarrage de ton compte Windows (pas besoin d'être administrateur).
- Par défaut, au démarrage Phoque Al s'ouvre réduit dans la barre des tâches et en mode discret :
  rien ne s'affiche à l'écran tant que tu ne cliques pas dessus.
- La désinstallation retire l'inscription.
"""
import os
import sys

from PySide6.QtCore import QTimer

from .. import db
from ..config import APP_DIR
from .common import log

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Phoque Al"
FLAG = "--demarrage"


def command():
    """Commande à lancer au démarrage (exe installé, ou version de développement)."""
    if getattr(sys, "frozen", False):
        return '"%s" %s' % (sys.executable, FLAG)
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(w):
        exe = w
    return '"%s" "%s" %s' % (exe, os.path.join(str(APP_DIR), "run_phoqueal.py"), FLAG)


def _reg():
    import winreg
    return winreg


def registered():
    try:
        w = _reg()
        with w.OpenKey(w.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _t = w.QueryValueEx(k, NAME)
            return v
    except Exception:
        return None


def set_autostart(on):
    try:
        w = _reg()
        with w.OpenKey(w.HKEY_CURRENT_USER, RUN_KEY, 0, w.KEY_SET_VALUE) as k:
            if on:
                w.SetValueEx(k, NAME, 0, w.REG_SZ, command())
            else:
                try:
                    w.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        log("démarrage Windows : %r" % e)
        return False


def started_by_windows():
    return FLAG in sys.argv


def menu(main, m):
    sub = m.addMenu("Démarrage de Windows")
    a = sub.addAction("Lancer Phoque Al au démarrage de Windows")
    a.setCheckable(True)
    a.setChecked(bool(registered()))

    def toggle(on):
        if set_autostart(on):
            db.set_setting(main.conn, "autostart", "1" if on else "0")
            main.status.setText("Phoque Al se lancera au démarrage de Windows" if on
                                else "Phoque Al ne se lancera plus au démarrage de Windows")
        else:
            main.status.setText("Réglage du démarrage impossible (voir le journal)")
    a.triggered.connect(toggle)
    b = sub.addAction("Au démarrage : réduit dans la barre des tâches et en mode discret")
    b.setCheckable(True)
    b.setChecked(db.get_setting(main.conn, "autostart_hidden", "1") == "1")
    b.triggered.connect(lambda on: db.set_setting(main.conn, "autostart_hidden", "1" if on else "0"))


def install(main):
    if sys.platform != "win32":
        return
    # l'exe a peut-etre change de place (reinstallation) : on garde l'inscription a jour
    cur = registered()
    if cur and cur != command() and db.get_setting(main.conn, "autostart", "0") == "1":
        set_autostart(True)
    if started_by_windows() and db.get_setting(main.conn, "autostart_hidden", "1") == "1":
        try:
            if not main.discreet:
                main.set_discreet(True)
        except Exception as e:
            log("démarrage discret : %r" % e)
        QTimer.singleShot(0, main.showMinimized)
        QTimer.singleShot(800, main.showMinimized)      # apres le show() de la fenetre principale
