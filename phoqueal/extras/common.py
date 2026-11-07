"""Outils partages par les fonctions avancees."""
import time
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from ..config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS moments(id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL, t REAL NOT NULL,
    created_at REAL, thumb TEXT);
CREATE INDEX IF NOT EXISTS idx_moments_video ON moments(video_id, t);
CREATE TABLE IF NOT EXISTS scenes(video_id INTEGER NOT NULL, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id, t);
CREATE TABLE IF NOT EXISTS scenes_done(video_id INTEGER PRIMARY KEY, n INTEGER, done_at REAL);
CREATE TABLE IF NOT EXISTS watch(video_id INTEGER NOT NULL, day TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(video_id, day));
CREATE TABLE IF NOT EXISTS soirees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, updated_at REAL);
CREATE TABLE IF NOT EXISTS vhash(video_id INTEGER PRIMARY KEY, h TEXT, w INTEGER, hgt INTEGER);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "rating" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN rating INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def log(text):
    try:
        with open(DATA_DIR / "extras_erreurs.txt", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")
    except OSError:
        pass


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        log(traceback.format_exc())


def owner(win):
    """Lecteur vise : la fenetre elle-meme, ou la case selectionnee d'une mosaique."""
    return win.selected if getattr(win, "is_mosaic", False) else win


def current_time(win):
    """(video_id, temps en secondes) de ce que montre le lecteur."""
    o = owner(win)
    t = o.player.get_time()
    return o.video_id, (t / 1000.0 if t and t > 0 else 0.0)


# ------------------------------------------------------------------------------------------
# Demarrer une video a un instant precis
# ------------------------------------------------------------------------------------------
def quiet_vlc(main):
    """VLC sans aperçu ni message à chaque capture d'image.

    Le fond flou (bandes noires comblées) et les transitions prennent une capture toutes les ~1,5 s :
    VLC affichait alors sur la vidéo la vignette et le nom du fichier capturé, en boucle, dès qu'on
    passait à un format avec bandes noires (bouton « format » de la télécommande, touche du clavier).
    Les messages de Phoque Al sur la vidéo passent par le « marquee », non concerné par --no-osd."""
    ctrl = getattr(main, "ctrl", None)
    if ctrl is None or getattr(ctrl, "_quiet_vlc", False) or getattr(ctrl, "players", None):
        return
    try:
        import vlc
        new = vlc.Instance(["--no-video-title-show", "--quiet", "--aout=directsound,any",
                            "--no-snapshot-preview", "--no-osd"])
        if new is not None:
            ctrl._old_instance = ctrl.instance           # gardée : la musique l'utilise déjà
            ctrl.instance = new
            ctrl._quiet_vlc = True
    except Exception as e:
        log("vlc silencieux : %r" % e)


def quiet_blur():
    """Fond flou : plus de capture tant que la vidéo est en pause (l'image ne change pas)."""
    try:
        from ..blurfill import BlurMixin
    except Exception:
        return
    if getattr(BlurMixin, "_extras_quiet", False):
        return
    orig = BlurMixin.blur_tick

    def blur_tick(self):
        try:
            if getattr(self, "_b_pm", None) is not None and not self.player.is_playing():
                self.blur_layout()
                return
        except Exception:
            pass
        return orig(self)

    BlurMixin.blur_tick = blur_tick
    BlurMixin._extras_quiet = True


def install_core(main):
    quiet_vlc(main)
    quiet_blur()
    from ..players import Controller
    if getattr(Controller, "_extras_start", False):
        return
    orig = Controller.start_position

    def start_position(self, row, resume=False, force_resume=False):
        forced = getattr(self, "_forced_start", None)
        if forced is not None:
            self._forced_start = None
            return forced
        return orig(self, row, resume, force_resume)

    Controller.start_position = start_position
    Controller._extras_start = True


def play_at(main, video_id, t, choice=None, **kw):
    """Lance une video a l'instant t (secondes) ; choice=None -> demande / auto comme d'habitude."""
    if choice is None:
        choice = main.ask_screen()
    if choice == -1:
        return False
    main.ctrl._forced_start = max(0.0, float(t))
    try:
        return main.ctrl.play(video_id, choice, **kw)
    finally:
        main.ctrl._forced_start = None


# ------------------------------------------------------------------------------------------
# Accueil : rangees supplementaires inserees avant « Ajoutees recemment »
# ------------------------------------------------------------------------------------------
def install_home(main, modules):
    from ..ui import MainWindow, CardList
    if getattr(MainWindow, "_extras_home", False):
        return
    orig = MainWindow.show_home

    def show_home(self):
        orig(self)
        try:
            add_rows(self, modules, CardList)
        except Exception:
            log(traceback.format_exc())

    MainWindow.show_home = show_home
    MainWindow._extras_home = True
    try:                                   # le bouton « Accueil » pointait sur l'ancienne fonction
        main.home_btn.clicked.disconnect()
    except (RuntimeError, TypeError):
        pass
    main.home_btn.clicked.connect(lambda: main.show_home())


def add_rows(main, modules, CardList):
    lay = main.home_layout
    pos = None
    for k in range(lay.count()):
        w = lay.itemAt(k).widget()
        if w is None:
            continue
        for b in [w] + w.findChildren(QPushButton):
            if isinstance(b, QPushButton) and _untr(b.text()).startswith("Ajoutées récemment"):
                pos = k
                break
        if pos is not None:
            break
    if pos is None:
        pos = max(0, lay.count() - 1)
    for mod in modules:
        fn = getattr(mod, "home_rows", None)
        if fn is None:
            continue
        for row in fn(main) or []:
            title, items = row["title"], row["rows"]
            if not items:
                continue
            before = lay.count()
            main.add_row(title, items, None)
            new = [lay.itemAt(k).widget() for k in range(before, lay.count())]
            for w in new:
                if w is None:
                    continue
                lay.removeWidget(w)
                lay.insertWidget(pos, w)
                pos += 1
                lst = w if isinstance(w, CardList) else None
                if lst is not None and row.get("activate"):
                    try:
                        lst.itemActivated.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    lst.itemActivated.connect(lambda it, l=lst, f=row["activate"]: safe(f, l.row(it)))
                if lst is None and row.get("title_click"):
                    for b in w.findChildren(QPushButton):
                        if b.objectName() == "rowTitle":
                            b.clicked.connect(lambda _=False, f=row["title_click"]: safe(f))
                            b.setCursor(Qt.PointingHandCursor)
                            if row.get("tooltip"):
                                b.setToolTip(row["tooltip"])


# ------------------------------------------------------------------------------------------
# Menu clic droit des vignettes
# ------------------------------------------------------------------------------------------
def install_card_menu(main, modules):
    def extras_card_menu(menu, vids):
        actions = {}
        for mod in modules:
            fn = getattr(mod, "card_menu", None)
            if fn is not None:
                try:
                    actions.update(fn(main, menu, vids) or {})
                except Exception:
                    log(traceback.format_exc())
        return actions

    main.extras_card_menu = extras_card_menu


# ------------------------------------------------------------------------------------------
# Barre du haut : boutons supplementaires (places a cote de « Mosaique »)
# ------------------------------------------------------------------------------------------
def install_toolbar(main, modules):
    anchor = None
    for b in main.findChildren(QPushButton):
        if _untr(b.text()) == "Mosaïque":
            anchor = b
            break
    target = None
    if anchor is not None:
        for lay in main.findChildren(QHBoxLayout):
            if lay.indexOf(anchor) >= 0:
                target = lay
                break
    for mod in modules:
        fn = getattr(mod, "toolbar_buttons", None)
        if fn is None:
            continue
        for btn in fn(main) or []:
            if target is not None:
                target.insertWidget(target.indexOf(anchor), btn)
            else:
                main.statusBar().addPermanentWidget(btn)


def _untr(text):
    try:
        from .i18n import untr
        return untr(text)
    except Exception:
        return text
