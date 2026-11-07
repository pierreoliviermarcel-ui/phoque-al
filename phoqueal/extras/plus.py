"""Bouton « Plus ▾ » de la barre du haut : statistiques, moments, doublons visuels, transitions, scenes, manette."""
from PySide6.QtWidgets import QMenu, QPushButton

from .common import safe


def open_menu(main, btn):
    m = QMenu(main)
    fill_menu(main, m)
    m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


def fill_menu(main, m):
    from . import gamepad, transitions
    library_items(main, m)
    m.addSeparator()
    transitions.menu(main, m)
    scenes_menu(main, m)
    gamepad.menu(main, m)


def library_items(main, m, stats_too=True):
    from . import moments, stats, visualdupes
    if stats_too:
        m.addAction("Statistiques…").triggered.connect(lambda: safe(lambda: stats.StatsDialog(main).exec()))
    m.addAction("Mes moments favoris…").triggered.connect(lambda: safe(lambda: moments.MomentsDialog(main).exec()))
    m.addAction("Doublons visuels…").triggered.connect(lambda: safe(lambda: visualdupes.VisualDupesDialog(main).exec()))


def scenes_menu(main, m):
    from . import scenes
    from .. import db
    s = m.addMenu("Changements de scène")
    done, total = scenes.progress(main.conn)
    info = s.addAction("Analysées : %d / %d vidéos (PgUp / PgDn dans le lecteur)" % (done, total))
    info.setEnabled(False)
    bg = s.addAction("Analyser toute la bibliothèque en tâche de fond")
    bg.setCheckable(True)
    bg.setChecked(db.get_setting(main.conn, "scenes_background", "0") == "1")
    bg.triggered.connect(lambda on: scenes.set_background(main, on))
    ms = s.addAction("Le montage au rythme démarre sur des débuts de scène")
    ms.setCheckable(True)
    ms.setChecked(db.get_setting(main.conn, "montage_scenes", "1") == "1")
    ms.triggered.connect(lambda on: db.set_setting(main.conn, "montage_scenes", "1" if on else "0"))


def toolbar_buttons(main):
    b = QPushButton("Plus ▾")
    b.setToolTip("Statistiques, moments, doublons visuels, transitions, scènes, manette")
    b.clicked.connect(lambda: open_menu(main, b))
    return [b]


def install(main):
    pass
