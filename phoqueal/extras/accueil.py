"""Nouvel accueil : barre du haut allégée, bandeau simplifié, rangées dans un ordre clair.

- Barre : Accueil, recherche, Mode discret, Écran, Télécommande, Mosaïque, Soirées et un bouton « ⋯ »
  qui regroupe tout le reste (Paramètres, Actualiser, Personnes & tags, Téléphone, Aperçus, Identifier,
  Raccourcis, statistiques, moments, Mes écrans, mur d'écrans…).
- Bandeau : sans la frise de vignettes, 4 diapositives au plus, types au choix.
- Rangées : Reprendre → Coups de cœur → Recommandé → Playlists → Soirées → Moments → Mieux notées →
  Personnes / tags → Ajoutées récemment → Albums → thèmes.
Tout se règle dans « ⋯ → Accueil » ; « Barre complète » remet l'ancienne barre.
"""
import json
import re
import unicodedata

from PySide6.QtWidgets import QMenu, QPushButton

from .. import db
from .common import log, safe

# boutons d'origine rangés dans « ⋯ » (début du texte du bouton)
MOVED = ["Paramètres", "Actualiser", "Personnes tags", "Téléphone", "Aperçus", "Identifier", "Raccourcis",
         "Plus ▾"]

# ordre des rangées : mots-clés (sans accents, minuscules) -> rang
ROW_ORDER = [
    (("reprendre", "en cours", "continuer"), 0),
    (("j'aime", "coups de", "coup de", "♥", "❤", "favori"), 1),
    (("recommand",), 2),
    (("playlist",), 3),
    (("soiree",), 4),
    (("moment",), 5),
    (("mieux not", "★"), 6),
    (("personne", "acteur", "actrice", "tag"), 7),
    (("ajoutee", "recent"), 8),
    (("album", "photo"), 9),
]
OTHER = 10

SLIDE_TYPES = [("random", "Vidéo au hasard"), ("resume", "Reprendre"), ("likes", "Coups de cœur"),
               ("theme", "Thème mis en avant"), ("video", "Vidéo proposée")]


def norm(text):
    t = unicodedata.normalize("NFKD", text or "").lower()
    return "".join(c for c in t if not unicodedata.combining(c))


def rank(title):
    t = norm(title)
    for words, r in ROW_ORDER:
        for w in words:
            w = norm(w)
            if (re.search(r"(^|[^a-z])" + re.escape(w), t) if w[:1].isalpha() else w in t):
                return r
    return OTHER


def setting(main, k, default):
    return db.get_setting(main.conn, k, default)


def compact(main):
    return setting(main, "home_compact", "1") == "1"


# ----------------------------------------------------------------------------------------
# Barre du haut
# ----------------------------------------------------------------------------------------
def toolbar_buttons(main):
    b = QPushButton("⋯")
    b.setToolTip("Tout le reste : paramètres, téléphone, personnes & tags, statistiques, écrans…")
    b.setFixedWidth(46)
    b.setStyleSheet("font-size:18px;font-weight:700")
    b.clicked.connect(lambda: safe(open_menu, main, b))
    main._more_btn = b
    return [b]


def text(b):
    """Texte du bouton comparable : sans les « & » (raccourcis Qt) ni espaces en double."""
    from .i18n import untr
    return " ".join(untr(b.text()).replace("&", " ").split())


def label(b):
    return b.text().replace("& ", "&& ") if "&&" not in b.text() else b.text()


def moved_buttons(main):
    out = []
    for b in main.findChildren(QPushButton):
        if b is getattr(main, "_more_btn", None):
            continue
        t = text(b)
        if any(t.startswith(m) for m in MOVED) and b.parent() is not None:
            out.append(b)
    out.sort(key=lambda b: next(i for i, m in enumerate(MOVED) if text(b).startswith(m)))
    return out


def apply_toolbar(main):
    on = compact(main)
    for b in getattr(main, "_moved", []):
        b.setVisible(not on)


def open_menu(main, btn):
    """Menu « ⋯ » rangé par thèmes : les actions courantes en haut, le reste dans des sous-menus."""
    from . import (ameliorer, apercu, autotags, pop, definition, demarrage, ecrans, forts, gamepad, i18n, mur, plus, pmvgen, rythme, seance, taches,
                   separation, sourdine, transitions, tri)
    m = QMenu(main)
    moved = {}
    for b in getattr(main, "_moved", []):
        for key in MOVED:
            if text(b).startswith(key):
                moved[key] = b
                break

    def add_moved(menu, key, title=None):
        b = moved.get(key)
        if b is None:
            return
        if b.menu() is not None:
            sub = menu.addMenu(title or label(b))
            for act in b.menu().actions():
                sub.addAction(act)
            return
        act = menu.addAction(title or label(b))
        act.setEnabled(b.isEnabled())
        if b.isCheckable():
            act.setCheckable(True)
            act.setChecked(b.isChecked())
        act.triggered.connect(lambda _=False, x=b: x.click())

    # --- le plus utilisé ---
    add_moved(m, "Paramètres")
    add_moved(m, "Actualiser")
    tri.menu(main, m)
    add_moved(m, "Téléphone")
    m.addSeparator()

    lib = m.addMenu("Bibliothèque")
    add_moved(lib, "Personnes tags")
    plus.library_items(main, lib)
    definition.menu(main, lib)
    add_moved(lib, "Aperçus")

    scr = m.addMenu("Écrans et lecture")
    scr.addAction("Mes écrans (numéros)…").triggered.connect(lambda: ecrans.open_dialog(main))
    add_moved(scr, "Identifier")
    scr.addAction("Mur d'écrans…").triggered.connect(lambda: mur.open_setup(main))
    a = scr.addAction("Choisir l'écran sur une mini-carte (4 écrans et plus)")
    a.setCheckable(True); a.setChecked(db.get_setting(main.conn, "picker_map", "1") == "1")
    a.triggered.connect(lambda on: db.set_setting(main.conn, "picker_map", "1" if on else "0"))
    scr.addSeparator()
    transitions.menu(main, scr)
    plus.scenes_menu(main, scr)
    forts.menu(main, scr)
    gamepad.menu(main, scr)

    mus = m.addMenu("Musique et PMV")
    mus.addAction("Créer un PMV…").triggered.connect(lambda: pmvgen.open_dialog(main))
    mus.addAction("Compilation de mes moments favoris (PMV)…").triggered.connect(
        lambda: pmvgen.open_dialog(main, None, "moments"))
    seance.menu(main, mus)
    pop.menu(main, mus)
    mus.addSeparator()
    rythme.menu(main, mus)
    sourdine.menu(main, mus)

    ia = m.addMenu("Outils IA")
    autotags.menu(main, ia)
    separation.menu(main, ia)
    ameliorer.menu(main, ia)

    pref = m.addMenu("Préférences")
    home_menu(main, pref.addMenu("Accueil"))
    demarrage.menu(main, pref)
    taches.menu(main, pref)
    apercu.menu(main, pref)
    add_moved(pref, "Raccourcis")
    m.addSeparator()
    i18n.menu(main, m)
    m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


def home_menu(main, sub):
    a = sub.addAction("Barre allégée (le reste dans « ⋯ »)")
    a.setCheckable(True); a.setChecked(compact(main))
    a.triggered.connect(lambda on: (db.set_setting(main.conn, "home_compact", "1" if on else "0"), apply_toolbar(main)))
    a = sub.addAction("Bandeau simplifié (sans frise de vignettes)")
    a.setCheckable(True); a.setChecked(setting(main, "hero_simple", "1") == "1")
    a.triggered.connect(lambda on: (db.set_setting(main.conn, "hero_simple", "1" if on else "0"), apply_hero(main),
                                    main.show_home()))
    a = sub.addAction("Rangées dans l'ordre conseillé")
    a.setCheckable(True); a.setChecked(setting(main, "home_order", "1") == "1")
    a.triggered.connect(lambda on: (db.set_setting(main.conn, "home_order", "1" if on else "0"), main.show_home()))
    if _SLIDE_KEY:
        st = sub.addMenu("Diapositives du bandeau")
        chosen = slide_types(main)
        for k, lab in SLIDE_TYPES:
            a = st.addAction(lab)
            a.setCheckable(True); a.setChecked(k in chosen)
            a.triggered.connect(lambda on, k=k: toggle_type(main, k, on))
    n = sub.addMenu("Nombre de diapositives")
    cur = int(setting(main, "hero_max", "4") or 4)
    for k in (2, 3, 4, 6, 8):
        a = n.addAction(str(k)); a.setCheckable(True); a.setChecked(k == cur)
        a.triggered.connect(lambda _=False, k=k: (db.set_setting(main.conn, "hero_max", str(k)), main.show_home()))


# ----------------------------------------------------------------------------------------
# Bandeau
# ----------------------------------------------------------------------------------------
_SLIDE_KEY = None            # cle du type de diapositive dans build_slides (detectee)
_ORIG_CARD_W = None


def slide_types(main):
    try:
        v = json.loads(setting(main, "hero_types", "") or "null")
    except Exception:
        v = None
    return set(v) if isinstance(v, list) else {k for k, _ in SLIDE_TYPES}


def toggle_type(main, k, on):
    s = slide_types(main)
    (s.add if on else s.discard)(k)
    if not s:
        s = {"random"}
    db.set_setting(main.conn, "hero_types", json.dumps(sorted(s)))
    main.show_home()


def slide_kind(sl):
    v = str(sl.get(_SLIDE_KEY, "")) if _SLIDE_KEY else ""
    v = norm(v)
    for k, words in (("resume", ("resume", "reprendre", "continue")), ("likes", ("like", "aime", "fav")),
                     ("theme", ("theme", "categ")), ("random", ("random", "hasard")),
                     ("video", ("video", "clip", "preview"))):
        if any(w in v for w in words):
            return k
    return "video"


def install_hero(main):
    global _SLIDE_KEY, _ORIG_CARD_W
    from .. import hero, ui
    if getattr(hero, "_extras_slides", False):
        return
    orig = hero.build_slides
    _ORIG_CARD_W = getattr(hero, "CARD_W", None)

    def build_slides(*a, **kw):
        global _SLIDE_KEY
        slides = orig(*a, **kw)
        try:
            if not isinstance(slides, list) or not slides:
                return slides
            if _SLIDE_KEY is None and isinstance(slides[0], dict):
                for k in ("kind", "type", "mode", "source"):
                    if k in slides[0]:
                        _SLIDE_KEY = k
                        break
                else:
                    log("bandeau : cles des diapositives = %s" % sorted(slides[0].keys()))
                    _SLIDE_KEY = ""
            wanted = slide_types(main)
            out = [s for s in slides if not _SLIDE_KEY or slide_kind(s) in wanted] or slides[:1]
            return out[:max(1, int(setting(main, "hero_max", "4") or 4))]
        except Exception as e:
            log("bandeau : %r" % e)
            return slides

    hero.build_slides = build_slides
    if getattr(ui, "build_slides", None) is orig:
        ui.build_slides = build_slides
    hero._extras_slides = True


def apply_hero(main):
    from .. import hero
    if _ORIG_CARD_W is None:
        return
    if setting(main, "hero_simple", "1") == "1":
        hero.CARD_W = 10 ** 6            # plus de place pour la frise : elle disparait
        if hasattr(hero, "SEG"):
            hero.SEG = 8                 # points au lieu de longs segments
    else:
        hero.CARD_W = _ORIG_CARD_W
        if hasattr(hero, "SEG"):
            hero.SEG = 34
    h = getattr(main, "hero", None)
    if h is not None:
        try:
            h.update()
        except RuntimeError:
            pass


# ----------------------------------------------------------------------------------------
# Rangées
# ----------------------------------------------------------------------------------------
def block_title(w):
    for b in [w] + w.findChildren(QPushButton):
        if isinstance(b, QPushButton) and b.objectName() == "rowTitle":
            from .i18n import untr
            return untr(b.text())
    return None


def reorder(main):
    if setting(main, "home_order", "1") != "1":
        return
    lay = main.home_layout
    items = [lay.itemAt(k) for k in range(lay.count())]
    head, blocks, tail = [], [], []
    for it in items:
        w = it.widget()
        if w is None:                         # ressort de fin de page
            tail.append(it)
            continue
        t = block_title(w)
        if t is not None:
            blocks.append([t, [w]])
        elif blocks:
            blocks[-1][1].append(w)
        else:
            head.append(w)                    # bandeau
    if len(blocks) < 2:
        return
    order = sorted(range(len(blocks)), key=lambda i: (rank(blocks[i][0]), i))
    if order == list(range(len(blocks))):
        return
    widgets = [w for i in order for w in blocks[i][1]]
    for w in widgets:
        lay.removeWidget(w)
    pos = len(head)
    for w in widgets:
        lay.insertWidget(pos, w)
        pos += 1


def install(main):
    install_hero(main)
    apply_hero(main)


def late_install(main):
    """Après les rangées des fonctions avancées : remet l'accueil dans l'ordre."""
    from ..ui import MainWindow
    main._moved = moved_buttons(main)
    apply_toolbar(main)
    if not getattr(MainWindow, "_extras_order", False):
        orig = MainWindow.show_home

        def show_home(self):
            orig(self)
            try:
                reorder(self)
            except Exception as e:
                log("ordre accueil : %r" % e)

        MainWindow.show_home = show_home
        MainWindow._extras_order = True
