"""Fonctions avancees de Phoque Al (installees au demarrage, sans toucher au coeur du logiciel).

- etoiles (notes 1 a 5)              - moments favoris (et bobine des moments)
- detection des changements de scene - transitions entre les videos
- soirees en un clic                  - statistiques de visionnage
- recommandations « dans le meme genre » - doublons visuels
- manette de jeu (Xbox / XInput)
- numéros des écrans, nouvel accueil, mur d'écrans
"""
from PySide6.QtGui import QKeySequence, QShortcut

from .. import keymap
from . import common


def install(main):
    """Appele une fois par la fenetre principale, apres la creation du controleur."""
    common.ensure_schema(main.conn)
    from . import i18n
    i18n.early(main)
    common.install_core(main)

    from . import (accueil, ameliorer, autotags, demarrage, ecrans, forts, seance, sourdine, gamepad, moments, multiecrans, mur, plus, rythme, ratings, recommend, scenes, separation, soirees, stats,
                   transitions, visualdupes)
    # l'ordre compte : rangees de l'accueil et boutons de la barre
    modules = [ecrans, multiecrans, soirees, recommend, moments, ratings, scenes, transitions, stats, visualdupes, gamepad,
               plus, mur, rythme, autotags, seance, forts, separation, ameliorer, sourdine, demarrage, accueil]

    # touches supplementaires (modifiables dans Parametres -> Clavier)
    known = {a[0] for a in keymap.ACTIONS}
    for mod in modules:
        for action in getattr(mod, "ACTIONS", []):
            if action[0] not in known:
                keymap.ACTIONS.append(action)
                keymap.DEFAULTS[action[0]] = action[3]
                known.add(action[0])

    player_actions = {}
    mosaic_actions = {}
    for mod in modules:
        player_actions.update(getattr(mod, "PLAYER_KEYS", {}))
        mosaic_actions.update(getattr(mod, "MOSAIC_KEYS", {}))
    _wrap_bind_keys(main, player_actions, mosaic_actions)

    for mod in modules:
        try:
            mod.install(main)
        except Exception as e:                                    # une option ne doit pas bloquer Phoque Al
            common.log("installation %s : %r" % (mod.__name__, e))

    main.ctrl.apply_keymap(keymap.load(main.conn))
    common.install_home(main, modules)
    common.install_card_menu(main, modules)
    common.install_toolbar(main, modules)
    try:
        accueil.late_install(main)
    except Exception as e:
        common.log("accueil : %r" % e)
    try:
        i18n.install(main)
    except Exception as e:
        common.log("langue : %r" % e)


def _wrap_bind_keys(main, player_actions, mosaic_actions):
    from ..players import PlayerWindow
    from ..mosaic import MosaicWindow

    def add(win, mapping, actions):
        for key, fn in actions.items():
            seq = mapping.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), win)
                sc.activated.connect(lambda f=fn, w=win: common.safe(f, w))
                win._shortcuts.append(sc)

    if not getattr(PlayerWindow, "_extras_keys", False):
        orig_p = PlayerWindow.bind_keys

        def bind_p(self, mapping):
            orig_p(self, mapping)
            add(self, mapping, player_actions)

        PlayerWindow.bind_keys = bind_p
        PlayerWindow._extras_keys = True

    if not getattr(MosaicWindow, "_extras_keys", False):
        orig_m = MosaicWindow.bind_keys

        def bind_m(self, mapping):
            orig_m(self, mapping)
            add(self, mapping, mosaic_actions)

        MosaicWindow.bind_keys = bind_m
        MosaicWindow._extras_keys = True
