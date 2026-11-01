"""Interface de Phoque Al : accueil en rangees, recherche, grille."""
import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox, QComboBox, QFileDialog, QMenu, QMessageBox, QToolButton, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QScrollArea, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import db, volumes
from .config import ROW_LIMIT
from .hero import HERO_STYLE, HeroBanner, HeroWorker, build_slides
from . import keymap
from .music import MusicPlayer
from .settings import SettingsDialog
from .playlists_ui import PlaylistPage, ask_name, fill_add_menu
from .mosaic_ui import MosaicDialog
from . import people as P
from .people_ui import AssignDialog, PeopleWorkshop
from .montage import HAS_NUMPY, Montage
from .players import Controller
from .config import DATA_DIR
from .previews import FRAMES, PreviewService, clip_path, preview_path
from .storage import is_ssd

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False
from .workers import ScanWorker, ThumbWorker

CARD = QSize(256, 144)

STYLE = """
QWidget { background: #0f0f12; color: #e6e6e6; font-family: 'Segoe UI'; font-size: 10pt; }
QLineEdit { background: #1c1c22; border: 1px solid #2c2c35; border-radius: 6px; padding: 7px 10px; }
QLineEdit:focus { border-color: #5b8def; }
QPushButton { background: #1c1c22; border: 1px solid #2c2c35; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #26262e; }
QPushButton#rowTitle { background: transparent; border: none; font-size: 13pt; font-weight: 600;
                       text-align: left; padding: 4px 0; }
QPushButton#rowTitle:hover { color: #5b8def; }
QPushButton#rowArrow { font-size: 15pt; padding: 0; border-radius: 6px; }
QLabel#brand { font-size: 16pt; font-weight: 700; letter-spacing: 2px; }
QLabel#status { color: #8a8a96; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 6px; padding: 4px; color: #cfcfd6; }
QListWidget::item:selected, QListWidget::item:hover { background: #22222a; }
QTreeWidget, QTableWidget { background: #15151a; border: 1px solid #2c2c35; border-radius: 6px;
                             gridline-color: #24242c; }
QHeaderView::section { background: #1c1c22; color: #b9b9c4; border: none; padding: 6px; }
QTabWidget::pane { border: 1px solid #2c2c35; border-radius: 6px; top: -1px; }
QTabBar::tab { background: #1c1c22; padding: 8px 18px; border-top-left-radius: 6px;
               border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: #2a2f45; color: white; }
QKeySequenceEdit QLineEdit { padding: 4px 8px; }
QMenu { background: #1c1c22; border: 1px solid #2c2c35; }
QMenu::item:selected { background: #2a2f45; }
QComboBox { background: #1c1c22; border: 1px solid #2c2c35; border-radius: 6px; padding: 6px 10px; }
QScrollBar:horizontal, QScrollBar:vertical { background: transparent; height: 8px; width: 8px; }
QScrollBar::handle { background: #33333d; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
"""


HELP = """PARTOUT
0 : revenir à l'accueil
1 à 9 : aller au lecteur de l'écran correspondant
Maj + Espace : pause / lecture de tous les lecteurs
Maj + flèches gauche/droite : tous reculent / avancent de 10 s
Maj + flèches haut/bas : volume de tous
Maj + M : couper / remettre le son de tous
Maj + Échap : fermer tous les lecteurs
F12 : touche panique (tout en pause et masqué) ; F12 à nouveau pour revenir

DANS UN LECTEUR (le lecteur actif a un cadre bleu)
Espace : pause / lecture
Flèches gauche/droite : -10 s / +10 s   (Ctrl : -1 min / +1 min)
Flèches haut/bas : volume
M : couper / remettre le son
F : plein écran / fenetre
I : infos (titre, temps, volume)
Échap : fermer le lecteur (la position est mémorisée)

MUSIQUE DE FOND (partout)
Ctrl + Espace : lecture / pause de la musique
Ctrl + N / Ctrl + B : morceau suivant / précédent
Ctrl + flèches haut/bas : volume de la musique

ACCUEIL
Clic droit sur le titre d'une rangée : définir le thème favori"""


def discreet_icon():
    pm = QPixmap(CARD)
    pm.fill(QColor("#1a1b22"))
    p = QPainter(pm)
    p.setPen(QColor("#3a3c48"))
    f = QFont("Segoe UI", 26)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "\u25B6")
    p.end()
    return QIcon(pm)


def placeholder():
    pm = QPixmap(CARD)
    pm.fill(QColor("#1e1e25"))
    return QIcon(pm)


def row_wheel(lst, event, main):
    """Molette sur une rangee horizontale : fait defiler la PAGE vers le bas/haut.
    La rangee ne defile horizontalement qu'avec Maj + molette ou un geste horizontal (pave tactile)."""
    dx, dy = event.angleDelta().x(), event.angleDelta().y()
    if (dx and abs(dx) > abs(dy)) or (event.modifiers() & Qt.ShiftModifier):
        hb = lst.horizontalScrollBar()
        hb.setValue(hb.value() - (dx or dy))
    else:
        vb = main.home_scroll.verticalScrollBar()
        vb.setValue(vb.value() - dy)
    event.accept()


class RowList(QListWidget):
    """Rangee horizontale simple (playlists, albums) avec la molette corrigee."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setHorizontalScrollMode(QListView.ScrollPerPixel)

    def wheelEvent(self, event):
        row_wheel(self, event, self.main)


class CardList(QListWidget):
    """Liste de vignettes : rangee horizontale ou grille."""

    def __init__(self, window, row_mode):
        super().__init__()
        self.main = window
        self.setViewMode(QListView.IconMode)
        self.setIconSize(CARD)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(CARD.width() + 16, CARD.height() + 44))
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.row_mode = row_mode
        self.category = None
        if row_mode:
            self.setHorizontalScrollMode(QListView.ScrollPerPixel)
            self.setFlow(QListView.LeftToRight)
            self.setWrapping(False)
            self.setFixedHeight(CARD.height() + 64)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.setResizeMode(QListView.Adjust)
            self.setWrapping(True)
        self.itemActivated.connect(self.main.play)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.main.card_menu(self, pos))
        self.setMouseTracking(True)
        self.itemEntered.connect(self.main.hover_enter)
        self.viewportEntered.connect(self.main.hover_leave)

    def leaveEvent(self, event):
        self.main.hover_leave()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if self.row_mode:
            row_wheel(self, event, self.main)
        else:
            super().wheelEvent(event)

    def fill(self, rows):
        self.clear()
        for r in rows:
            hide = self.main.discreet
            item = QListWidgetItem(" " if hide else r["title"])
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.UserRole + 2, r["kind"])
            if not hide:
                item.setToolTip(r["title"])
            item.setIcon(self.main.icon_for(r))
            self.addItem(item)
            self.main.register(r["id"], item)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phoque Al")
        self.resize(1400, 860)
        self.setStyleSheet(STYLE + HERO_STYLE)
        self.hero = None
        self.hero_workers = []
        self.conn = db.connect()
        self.items = {}
        self._placeholder = placeholder()
        self._discreet_icon = discreet_icon()
        self.discreet = db.get_setting(self.conn, "discreet", "0") == "1"
        db.likes_playlist(self.conn)      # cree la playlist « J'aime » si besoin
        db.relocate_data(self.conn)       # donnees copiees depuis un autre PC
        self._last_grid = None
        self.scan_worker = None
        self.thumb_worker = None

        # barre du haut
        brand = QLabel("PHOQUE AL")
        brand.setObjectName("brand")
        self.home_btn = QPushButton("Accueil")
        self.home_btn.clicked.connect(self.show_home)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher (titre, studio, thème)…")
        self.search.setClearButtonEnabled(True)
        self.discreet_box = QCheckBox("Mode discret")
        self.discreet_box.setToolTip("Cache les miniatures, les titres et la bannière")
        self.discreet_box.setFocusPolicy(Qt.NoFocus)
        self.discreet_box.setChecked(self.discreet)
        self.discreet_box.toggled.connect(self.set_discreet)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.run_search)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        add_btn = QPushButton("Paramètres")
        add_btn.clicked.connect(lambda: self.open_settings())
        rescan_btn = QPushButton("Actualiser")
        rescan_btn.clicked.connect(self.start_scan)
        self.screen_choice = QComboBox()
        self.screen_choice.setToolTip("Écran sur lequel lancer la prochaine vidéo")
        self.screen_choice.addItem("Écran : auto", 0)
        for i in range(1, len(Controller.screens()) + 1):
            self.screen_choice.addItem("Écran %d" % i, i)
        self.phone_btn = QPushButton("Téléphone")
        self.phone_btn.setCheckable(True)
        self.phone_btn.setToolTip("Piloter Phoque Al depuis un téléphone ou une tablette (même Wi-Fi)")
        self.phone_btn.clicked.connect(lambda: self.open_phone())
        self.remote_btn = QPushButton("Télécommande")
        self.remote_btn.setCheckable(True)
        self.remote_btn.setToolTip("Mode souris : petit panneau de contrôle déplaçable (Ctrl + T)")
        self.remote_btn.clicked.connect(lambda: self.toggle_remote())
        people_btn = QPushButton("Personnes & tags")
        people_btn.setToolTip("Atelier : valider les noms et tags tirés des noms de fichiers")
        people_btn.clicked.connect(lambda: self.open_workshop())
        mosaic_btn = QPushButton("Mosaïque")
        mosaic_btn.setToolTip("Plusieurs vidéos sur un même écran")
        mosaic_btn.clicked.connect(lambda: MosaicDialog(self).exec())
        ident_btn = QPushButton("Identifier")
        ident_btn.setToolTip("Affiche le numéro de chaque écran")
        self.preview_btn = QPushButton("Aperçus")
        self.preview_btn.setToolTip("Mode des aperçus au survol")
        self.preview_btn.clicked.connect(self.preview_menu)
        help_btn = QPushButton("Raccourcis")
        help_btn.clicked.connect(self.show_help)

        top = QHBoxLayout()
        top.addWidget(brand)
        top.addSpacing(20)
        top.addWidget(self.home_btn)
        top.addWidget(self.search, 1)
        top.addWidget(self.discreet_box)
        top.addWidget(self.screen_choice)
        top.addWidget(people_btn)
        top.addWidget(self.phone_btn)
        top.addWidget(self.remote_btn)
        top.addWidget(mosaic_btn)
        top.addWidget(ident_btn)
        top.addWidget(self.preview_btn)
        top.addWidget(help_btn)
        top.addWidget(add_btn)
        top.addWidget(rescan_btn)

        # pages : accueil (rangees) et grille (recherche / categorie)
        self.home = QWidget()
        self.home_layout = QVBoxLayout(self.home)
        self.home_layout.setSpacing(6)
        home_scroll = QScrollArea()
        home_scroll.setWidgetResizable(True)
        home_scroll.setWidget(self.home)
        self.home_scroll = home_scroll

        grid_page = QWidget()
        gl = QVBoxLayout(grid_page)
        self.grid_title = QLabel()
        self.grid_title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        self.grid = CardList(self, row_mode=False)
        # barre « playlist » d'un theme
        self.theme_bar = QWidget()
        tb = QHBoxLayout(self.theme_bar)
        tb.setContentsMargins(0, 0, 0, 4)
        n_screens = len(Controller.screens())
        self.tb_play = QPushButton("\u25B6  Tout lire")
        self.tb_play.setObjectName("heroPrimary")
        self.tb_shuffle = QPushButton("Lecture aléatoire")
        self.tb_multi = QPushButton("Répartir sur les %d écrans" % n_screens)
        self.tb_multi.setVisible(n_screens > 1)
        self.tb_loop = QPushButton("En boucle")
        self.tb_loop.setCheckable(True)
        self.tb_copy = QPushButton("Copier en playlist…")
        self.tb_play.clicked.connect(lambda: self.theme_play())
        self.tb_shuffle.clicked.connect(lambda: self.theme_play(shuffle=True))
        self.tb_multi.clicked.connect(lambda: self.theme_play(multi=True))
        self.tb_loop.toggled.connect(lambda on: self.grid.category and db.set_setting(
            self.conn, self.spec_loop_key(self.grid.category), "1" if on else None))
        self.tb_copy.clicked.connect(lambda: self.theme_copy())
        for b in (self.tb_play, self.tb_shuffle, self.tb_multi, self.tb_loop):
            tb.addWidget(b)
        tb.addStretch(1)
        tb.addWidget(self.tb_copy)
        self.theme_bar.setVisible(False)
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 4)
        self.chips.setVisible(False)
        gl.addWidget(self.grid_title)
        gl.addWidget(self.chips)
        gl.addWidget(self.theme_bar)
        gl.addWidget(self.grid, 1)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.home_scroll)
        self.pages.addWidget(grid_page)
        self.playlist_page = None      # cree apres le controleur

        self.status = QLabel()
        self.status.setObjectName("status")

        # barre de musique
        self.music_folder_btn = QPushButton("\u266A  Dossier musique…")
        self.music_prev = QPushButton("\u23EE")
        self.music_play = QPushButton("\u25B6")
        self.music_next = QPushButton("\u23ED")
        self.music_shuffle = QPushButton("Aléatoire")
        self.music_shuffle.setCheckable(True)
        self.montage_btn = QToolButton()
        self.montage_btn.setText("Montage au rythme")
        self.montage_btn.setCheckable(True)
        self.montage_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.montage_btn.setFocusPolicy(Qt.NoFocus)
        self.montage_btn.setToolTip("Les vidéos changent sur les temps de la musique")
        self.music_volume = QSlider(Qt.Horizontal)
        self.music_volume.setRange(0, 100)
        self.music_volume.setFixedWidth(130)
        self.music_volume.setToolTip("Volume de la musique")
        self.music_track = QLabel("Aucune musique")
        self.music_track.setObjectName("status")
        for b in (self.music_prev, self.music_play, self.music_next, self.music_shuffle,
                  self.music_folder_btn):
            b.setFocusPolicy(Qt.NoFocus)
        self.music_volume.setFocusPolicy(Qt.NoFocus)
        music_bar = QHBoxLayout()
        for w in (self.music_folder_btn, self.music_prev, self.music_play, self.music_next,
                  self.music_shuffle, self.music_volume, self.montage_btn):
            music_bar.addWidget(w)
        music_bar.addWidget(self.music_track, 1)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(20, 14, 20, 10)
        lay.addLayout(top)
        lay.addWidget(self.pages, 1)
        lay.addLayout(music_bar)
        lay.addWidget(self.status)
        self.setCentralWidget(root)

        self.hover_item = None
        self.hover_vid = None
        self.hover_icon = None
        self.hover_frames = []
        self.hover_index = 0
        self.hover_delay = QTimer(self)
        self.hover_delay.setSingleShot(True)
        self.hover_delay.setInterval(350)
        self.hover_delay.timeout.connect(self.hover_start)
        self.hover_anim = QTimer(self)
        self.hover_anim.setInterval(650)
        self.hover_anim.timeout.connect(self.hover_step)
        self.data_on_ssd = is_ssd(DATA_DIR)
        self.previews = PreviewService(self.effective_preview_mode())
        self.clip_player = None
        if HAS_MULTIMEDIA:
            self.clip_player = QMediaPlayer(self)
            self.clip_sink = QVideoSink(self)
            self.clip_player.setVideoSink(self.clip_sink)
            self.clip_player.setLoops(-1)
            self.clip_sink.videoFrameChanged.connect(self.on_clip_frame)
        self.previews.ready.connect(self.on_preview)
        self.previews.start()

        self.ctrl = Controller(self)
        app = QApplication.instance()
        if app is not None:                     # ecran branche / debranche pendant que Phoque Al tourne
            app.screenAdded.connect(lambda _s: QTimer.singleShot(1500, self.ctrl.screens_changed))
            app.screenRemoved.connect(lambda _s: QTimer.singleShot(300, self.ctrl.screens_changed))
        self.music = MusicPlayer(self, self.ctrl.instance)
        self.music_folder_btn.clicked.connect(lambda: self.choose_music_folder())
        self.music_play.clicked.connect(lambda: self.music.toggle())
        self.music_prev.clicked.connect(lambda: self.music.previous())
        self.music_next.clicked.connect(lambda: self.music.next())
        self.music_shuffle.setChecked(self.music.shuffle)
        self.music_shuffle.toggled.connect(self.music.set_shuffle)
        self.music_volume.setValue(self.music.volume)
        self.music_volume.valueChanged.connect(self.music.set_volume)
        self.music.track_changed.connect(self.on_track)
        self.music.state_changed.connect(lambda on: self.music_play.setText("\u23F8" if on else "\u25B6"))
        self.montage = Montage(self)
        self.montage_btn.setChecked(self.montage.enabled)
        self.montage_btn.toggled.connect(self.toggle_montage)
        self.montage_menu = QMenu(self)
        self.montage_menu.aboutToShow.connect(self.fill_montage_menu)
        self.montage_btn.setMenu(self.montage_menu)
        if not HAS_NUMPY:
            self.montage_btn.setEnabled(False)
            self.montage_btn.setToolTip("Module numpy manquant : relance lancer_phoqueal.bat")
        self.remote = None
        from .webremote import WebRemote
        self.web = WebRemote(self)
        from .network import Network
        self.network = Network(self)
        if db.get_setting(self.conn, "phone_on", "0") == "1":
            QTimer.singleShot(800, lambda: self.phone_btn.setChecked(self.web.start()))
        if db.get_setting(self.conn, "remote_on", "0") == "1":
            QTimer.singleShot(500, self.toggle_remote)
        if self.music.folder():
            n = self.music.load()
            self.music_track.setText("%d morceaux prêts" % n)
        ident_btn.clicked.connect(self.ctrl.identify)
        self.bind_home_keys(self.ctrl.keymap)
        self.playlist_page = PlaylistPage(self)
        self.pages.addWidget(self.playlist_page)
        self.update_preview_button()
        try:
            from .extras import install as _install_extras      # fonctions avancees
            _install_extras(self)
        except Exception:
            import traceback
            from .extras.common import log
            log(traceback.format_exc())
        self.show_home()
        self.start_scan()

    # --- vignettes -----------------------------------------------------------
    def set_discreet(self, on):
        on = bool(on)
        if self.discreet_box.isChecked() != on:
            self.discreet_box.setChecked(on)     # rappelle set_discreet
            return
        self.discreet = on
        db.set_setting(self.conn, "discreet", "1" if on else "0")
        self.hover_leave()
        page = self.pages.currentIndex()
        if page == 0:
            self.show_home()
        elif page == 1 and self._last_grid:
            self.show_grid(*self._last_grid)
        elif page == 2:
            self.playlist_page.reload()
        self.status.setText("Mode discret activé" if on else "Mode discret désactivé")

    def icon_for(self, row):
        if self.discreet:
            return self._discreet_icon
        thumb = row["thumb"] if row["thumb"] and os.path.isfile(row["thumb"]) else None
        liked = "liked" in row.keys() and row["liked"]
        if not liked:
            return QIcon(thumb) if thumb else self._placeholder
        # vignette aimee : petit coeur rouge en haut a droite
        pm = QPixmap(CARD)
        pm.fill(QColor("#1e1e25"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if thumb:
            src = QPixmap(thumb)
            if not src.isNull():
                src = src.scaled(CARD, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                p.drawPixmap(0, 0, src, (src.width() - CARD.width()) // 2, (src.height() - CARD.height()) // 2,
                             CARD.width(), CARD.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(CARD.width() - 40, 6, 32, 32)
        p.setPen(QColor("#ff4d5e"))
        f = QFont("Segoe UI Symbol", 15)
        p.setFont(f)
        p.drawText(CARD.width() - 40, 6, 32, 32, Qt.AlignCenter, "\u2665")
        p.end()
        return QIcon(pm)

    def on_like_changed(self, vid, liked):
        row = self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
        if not row:
            return
        icon = self.icon_for(row)
        for item in self.items.get(vid, []):
            try:
                if item is self.hover_item:
                    self.hover_icon = icon
                else:
                    item.setIcon(icon)
            except RuntimeError:
                pass
        if self.pages.currentIndex() == 2 and self.playlist_page.pid == db.likes_playlist(self.conn):
            self.playlist_page.reload()

    def register(self, vid, item):
        self.items.setdefault(vid, []).append(item)

    def on_thumb(self, vid, path):
        if self.discreet:
            return
        if db.is_liked(self.conn, vid):
            row = self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
            if row:
                for item in self.items.get(vid, []):
                    try:
                        item.setIcon(self.icon_for(row))
                    except RuntimeError:
                        pass
            return
        icon = None
        for item in self.items.get(vid, []):
            try:
                icon = icon or QIcon(path)
                if item is self.hover_item:
                    self.hover_icon = icon
                    if self.hover_frames or getattr(self, "_clip_active", False):
                        continue
                item.setIcon(icon)
            except RuntimeError:
                pass

    # --- musique de fond ----------------------------------------------------
    def choose_music_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choisir le dossier de musique")
        if not path:
            return
        n = self.music.set_folder(path)
        self.music_track.setText("%d morceaux trouvés" % n if n else "Aucun fichier audio dans ce dossier")

    def on_track(self, name):
        if name:
            self.music_track.setText("\u266A  " + name)
            self.music.osd("\u266A " + name)

    # --- choix de l'ecran ------------------------------------------------------
    def ask_screen(self):
        """Ecran ou lancer la video : celui du menu ; en « auto », s'il reste un ecran libre on le prend,
        sinon on demande (les numeros s'affichent sur les ecrans). -1 = annule."""
        choice = self.screen_choice.currentData() or 0
        if choice:
            return choice
        ctrl = self.ctrl
        if any(n not in ctrl.players for n in range(1, len(ctrl.screens()) + 1)):
            return 0
        if db.get_setting(self.conn, "ask_screen", "1") != "1":
            return 0
        ctrl.show_numbers(True)
        menu = QMenu(self)
        menu.addSection("Tous les écrans sont occupés : où lancer la vidéo ?")
        acts = {}
        for n in ctrl.all_numbers():
            p = ctrl.players.get(n)
            if p is None:
                what = "libre"
            elif self.discreet:
                what = "occupé"
            else:
                what = p.display_title() if hasattr(p, "display_title") else "mosaïque"
            label = "&%d   Écran %d  —  %s" % (n % 10, n, what[:70]) if n < 10 else "Écran %d  —  %s" % (n, what[:70])
            if getattr(self, "network", None) is not None and self.network.owns(n):
                label += "   (%s)" % self.network.label(n)
            acts[menu.addAction(label)] = n
        menu.addSeparator()
        menu.addAction("Annuler")
        from PySide6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())
        ctrl.show_numbers(False)
        return acts.get(chosen, -1)

    # --- satellites ---------------------------------------------------------
    def refresh_screen_choice(self):
        cur = self.screen_choice.currentData()
        self.screen_choice.blockSignals(True)
        self.screen_choice.clear()
        self.screen_choice.addItem("Écran : auto", 0)
        for n in self.ctrl.all_numbers():
            label = self.network.label(n) if self.network.owns(n) else ""
            self.screen_choice.addItem("Écran %d%s" % (n, ("  ·  " + label) if label else ""), n)
        i = self.screen_choice.findData(cur)
        self.screen_choice.setCurrentIndex(i if i >= 0 else 0)
        self.screen_choice.blockSignals(False)

    def on_screens_changed(self):
        """Un satellite vient d'apparaitre / disparaitre : menus d'ecran et telecommande a jour."""
        self.refresh_screen_choice()
        if self.remote is not None:
            was = self.remote.enabled
            self.remote.set_enabled(False)
            self.remote.close()
            self.remote.deleteLater()
            self.remote = None
            if was:
                self.toggle_remote()

    # --- telecommande sur telephone ---------------------------------------
    def open_phone(self):
        from .webremote import PhoneDialog
        PhoneDialog(self).exec()
        self.phone_btn.setChecked(self.web.running())

    # --- telecommande (mode souris) ------------------------------------------
    def on_playback_started(self):
        show = db.get_setting(self.conn, "remote_on_play", "0") == "1"
        if self.remote is None and not show:
            return
        if show and (self.remote is None or not self.remote.enabled):
            self.toggle_remote()
        if self.remote is not None and self.remote.enabled:
            QTimer.singleShot(300, lambda: self.remote.playback_started(show))

    def toggle_remote(self):
        from .remote import Remote
        if self.remote is None:
            self.remote = Remote(self)
        show = not self.remote.enabled
        self.remote.set_enabled(show)
        self.remote_btn.setChecked(show)
        db.set_setting(self.conn, "remote_on", "1" if show else "0")

    # --- montage au rythme ---------------------------------------------------
    def toggle_montage(self, on):
        self.montage.set_enabled(on)
        if on and not self.music.is_playing():
            self.status.setText("Montage activé : lance la musique (Ctrl + Espace) pour démarrer.")
        elif not on:
            self.status.setText("Montage désactivé")

    def fill_montage_menu(self):
        m = self.montage_menu
        m.clear()
        mt = self.montage
        sub = m.addMenu("Durée minimale d'un extrait : %g s" % mt.min_dur)
        for v in (1, 2, 3, 4, 6, 8, 12, 16, 24):
            a = sub.addAction("%d s" % v)
            a.setCheckable(True)
            a.setChecked(mt.min_dur == v)
            a.triggered.connect(lambda _=False, v=v: self._montage_set("min_dur", float(v)))
        a = sub.addAction("Autre…")
        a.triggered.connect(lambda _=False: self._montage_custom_min())
        sub = m.addMenu("Couper sur")
        for v, label in ((1, "chaque temps"), (2, "un temps sur deux"), (4, "un temps sur quatre (≈ mesure)")):
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(mt.every == v)
            a.triggered.connect(lambda _=False, v=v: self._montage_set("every", v))
        sub = m.addMenu("Piocher les vidéos dans")
        for v, label in (("context", "le thème ou la playlist en cours"), ("all", "toute la bibliothèque")):
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(mt.source == v)
            a.triggered.connect(lambda _=False, v=v: self._montage_set("source", v))
        sub = m.addMenu("Mosaïques")
        a = sub.addAction("Appliquer le montage aux mosaïques")
        a.setCheckable(True)
        a.setChecked(mt.include_mosaics)
        a.triggered.connect(lambda on: self._montage_set("include_mosaics", on))
        sub.addSeparator()
        for v, label in (("one", "Une case change à chaque temps"), ("all", "Toutes les cases changent ensemble")):
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(mt.mosaic_mode == v)
            a.setEnabled(mt.include_mosaics)
            a.triggered.connect(lambda _=False, v=v: self._montage_set("mosaic_mode", v))
        a = m.addAction("Couper le son des vidéos pendant le montage")
        a.setCheckable(True)
        a.setChecked(mt.mute_videos)
        a.triggered.connect(lambda on: self._montage_set("mute_videos", on))
        m.addSeparator()
        info = m.addAction("Tempo du morceau : %s" % ("%s BPM" % mt.bpm if mt.bpm else "pas encore analysé"))
        info.setEnabled(False)

    def _montage_set(self, attr, value):
        setattr(self.montage, attr, value)
        self.montage.save()
        self.montage.apply_mute()

    def _montage_custom_min(self):
        from PySide6.QtWidgets import QInputDialog
        v, ok = QInputDialog.getDouble(self, "Montage", "Durée minimale d'un extrait (secondes) :",
                                       self.montage.min_dur, 0.5, 120, 1)
        if ok:
            self._montage_set("min_dur", v)

    # --- mode des apercus : images (disque dur) ou extraits video (SSD) -----
    def effective_preview_mode(self):
        choice = db.get_setting(self.conn, "preview_mode", "auto")
        if choice == "auto":
            return "video" if self.data_on_ssd and HAS_MULTIMEDIA else "images"
        if choice == "video" and not HAS_MULTIMEDIA:
            return "images"
        return choice

    def update_preview_button(self):
        choice = db.get_setting(self.conn, "preview_mode", "auto")
        mode = self.effective_preview_mode()
        label = "Aperçus : vidéo" if mode == "video" else "Aperçus : images"
        if choice == "auto":
            label += " (auto)"
        self.preview_btn.setText(label)
        disk = {True: "SSD détecté", False: "disque dur détecté", None: "type de disque inconnu"}[self.data_on_ssd]
        self.preview_btn.setToolTip("Données de Phoque Al : %s" % disk)

    def preview_menu(self):
        menu = QMenu(self)
        current = db.get_setting(self.conn, "preview_mode", "auto")
        disk = {True: "SSD", False: "disque dur", None: "inconnu"}[self.data_on_ssd]
        options = [("auto", "Automatique (vidéo si SSD) — ici : %s" % disk),
                   ("video", "Extraits vidéo"), ("images", "Images (léger)")]
        actions = {}
        for key, text in options:
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(key == current)
            if key == "video" and not HAS_MULTIMEDIA:
                act.setEnabled(False)
            actions[act] = key
        chosen = menu.exec(self.preview_btn.mapToGlobal(self.preview_btn.rect().bottomLeft()))
        if chosen in actions:
            db.set_setting(self.conn, "preview_mode", actions[chosen])
            self.hover_leave()
            self.previews.set_mode(self.effective_preview_mode())
            self.update_preview_button()

    # --- apercus animes au survol -----------------------------------------
    def hover_enter(self, item):
        if (self.discreet or item.data(Qt.UserRole + 2) == "photo"
                or (isinstance(item.data(Qt.UserRole), int) and item.data(Qt.UserRole) < 0)):
            self.hover_leave()
            return
        if item is self.hover_item:
            return
        self.hover_leave()
        self.hover_item = item
        self.hover_vid = item.data(Qt.UserRole)
        self.hover_icon = item.icon()
        self.hover_delay.start()

    def hover_start(self):
        if self.hover_vid is None:
            return
        if self.previews.mode == "video":
            clip = clip_path(self.hover_vid)
            if clip.is_file():
                self.hover_clip(str(clip))
                return
            strip = preview_path(self.hover_vid)
            if strip.is_file():
                self.hover_play(str(strip))   # en attendant l'extrait
            self.previews.request(self.hover_vid)
            return
        path = preview_path(self.hover_vid)
        if path.is_file():
            self.hover_play(str(path))
        else:
            self.previews.request(self.hover_vid)

    def on_preview(self, vid, path):
        if vid == self.hover_vid and self.hover_item is not None and not self.hover_delay.isActive():
            if path.endswith(".mp4"):
                self.hover_clip(path)
            else:
                self.hover_play(path)

    def hover_clip(self, path):
        if not self.clip_player:
            return
        self.hover_anim.stop()
        self.hover_frames = []
        self._clip_active = True
        self.clip_player.setSource(QUrl.fromLocalFile(path))
        self.clip_player.play()

    def on_clip_frame(self, frame):
        if not getattr(self, "_clip_active", False) or self.hover_item is None:
            return
        img = frame.toImage()
        if img.isNull():
            return
        try:
            self.hover_item.setIcon(QIcon(QPixmap.fromImage(img)))
        except RuntimeError:
            self.hover_item = None

    def hover_play(self, path):
        pm = QPixmap(path)
        if pm.isNull():
            return
        fw = pm.width() // FRAMES
        self.hover_frames = [QIcon(pm.copy(i * fw, 0, fw, pm.height())) for i in range(FRAMES)]
        self.hover_index = 0
        self.hover_step()
        self.hover_anim.start()

    def hover_step(self):
        if not self.hover_frames or self.hover_item is None:
            return
        try:
            self.hover_item.setIcon(self.hover_frames[self.hover_index])
        except RuntimeError:
            self.hover_item = None
            self.hover_anim.stop()
            return
        self.hover_index = (self.hover_index + 1) % len(self.hover_frames)

    def hover_leave(self):
        self.hover_delay.stop()
        self.hover_anim.stop()
        if getattr(self, "_clip_active", False):
            self._clip_active = False
            self.clip_player.stop()
            self.clip_player.setSource(QUrl())
        if self.hover_item is not None:
            try:
                self.hover_item.setIcon(self.hover_icon)
            except RuntimeError:
                pass
        self.hover_item = None
        self.hover_vid = None
        self.hover_frames = []

    # --- pages ---------------------------------------------------------------
    def clear_home(self):
        self.hover_leave()
        if self.hero is not None:
            try:
                self.hero.stop()
            except RuntimeError:
                pass
        self.hero = None
        while self.home_layout.count():
            w = self.home_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.items = {}

    def add_row(self, title, rows, category=None):
        if not rows:
            return
        btn = QPushButton(title)
        btn.setObjectName("rowTitle")
        btn.setCursor(Qt.PointingHandCursor)
        if category is not None:
            btn.clicked.connect(lambda _=False, c=category: self.show_category(c))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, c=category: self.row_menu(b, c, pos))
        lst = CardList(self, row_mode=True)
        lst.category = category
        lst.fill(rows)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(btn)
        hl.addStretch(1)
        for text, sign in (("\u2039", -1), ("\u203A", 1)):
            arrow = QPushButton(text)
            arrow.setObjectName("rowArrow")
            arrow.setFixedSize(34, 28)
            arrow.setFocusPolicy(Qt.NoFocus)
            arrow.setCursor(Qt.PointingHandCursor)
            arrow.setToolTip("Faire défiler la rangée (ou Maj + molette)")
            arrow.clicked.connect(lambda _=False, l=lst, k=sign: l.horizontalScrollBar().setValue(
                l.horizontalScrollBar().value() + k * int(l.viewport().width() * 0.85)))
            hl.addWidget(arrow)
        self.home_layout.addWidget(head)
        self.home_layout.addWidget(lst)

    def add_hero(self):
        slides = build_slides(self.conn)
        if not slides:
            return
        self.hero = HeroBanner(self, slides)
        self.home_layout.addWidget(self.hero)
        self.home_layout.addSpacing(8)
        ids = [sl["video"]["id"] for sl in slides]
        self.hero_workers = [w for w in self.hero_workers if w.isRunning()]
        worker = HeroWorker(ids)
        worker.ready.connect(self.on_hero_image)
        self.hero_workers.append(worker)
        worker.start()

    # --- playlists ---------------------------------------------------------------
    def add_playlist_row(self):
        pls = db.list_playlists(self.conn)
        head = QHBoxLayout()
        title = QPushButton("Mes playlists  (%d)" % len(pls))
        title.setObjectName("rowTitle")
        new_btn = QPushButton("+ Nouvelle playlist")
        new_btn.setFocusPolicy(Qt.NoFocus)
        new_btn.clicked.connect(lambda: self.new_playlist())
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(new_btn)
        box = QWidget()
        box.setLayout(head)
        self.home_layout.addWidget(box)
        if not pls:
            hint = QLabel("Aucune playlist pour l'instant : clic droit sur une vignette → « Ajouter à la playlist ».")
            hint.setObjectName("status")
            self.home_layout.addWidget(hint)
            return
        lst = RowList(self)
        lst.setViewMode(QListView.IconMode)
        lst.setIconSize(CARD)
        lst.setMovement(QListView.Static)
        lst.setFlow(QListView.LeftToRight)
        lst.setWrapping(False)
        lst.setGridSize(QSize(CARD.width() + 16, CARD.height() + 44))
        lst.setFixedHeight(CARD.height() + 64)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.ElideRight)
        for k, pl in enumerate(pls, 1):
            if self.discreet:
                it = QListWidgetItem("Playlist %d  ·  %d" % (k, pl["n"]))
                it.setIcon(self._discreet_icon)
            else:
                label = ("\u2665 " + pl["name"]) if pl["system"] == "likes" else pl["name"]
                it = QListWidgetItem("%s  ·  %d" % (label, pl["n"]))
                it.setToolTip(pl["name"])
                it.setIcon(QIcon(pl["cover"]) if pl["cover"] and os.path.isfile(pl["cover"]) else self._placeholder)
            it.setData(Qt.UserRole, pl["id"])
            lst.addItem(it)
        lst.itemActivated.connect(lambda it: self.show_playlist(it.data(Qt.UserRole)))
        self.home_layout.addWidget(lst)

    def add_photo_row(self):
        albums = db.photo_albums(self.conn)
        if not albums:
            return
        title = QPushButton("Albums photo  (%d albums · %d photos)" % (len(albums), db.photo_count(self.conn)))
        title.setObjectName("rowTitle")
        self.home_layout.addWidget(title)
        lst = RowList(self)
        lst.setViewMode(QListView.IconMode)
        lst.setIconSize(CARD)
        lst.setMovement(QListView.Static)
        lst.setFlow(QListView.LeftToRight)
        lst.setWrapping(False)
        lst.setGridSize(QSize(CARD.width() + 16, CARD.height() + 44))
        lst.setFixedHeight(CARD.height() + 64)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.ElideRight)
        for k, al in enumerate(albums, 1):
            if self.discreet:
                it = QListWidgetItem("Album %d  ·  %d" % (k, al["n"]))
                it.setIcon(self._discreet_icon)
            else:
                it = QListWidgetItem("%s  ·  %d" % (al["category"], al["n"]))
                it.setToolTip(al["category"])
                it.setIcon(QIcon(al["cover"]) if al["cover"] and os.path.isfile(al["cover"]) else self._placeholder)
            it.setData(Qt.UserRole, al["category"])
            lst.addItem(it)
        lst.itemActivated.connect(lambda it: self.show_album(it.data(Qt.UserRole)))
        self.home_layout.addWidget(lst)

    def show_album(self, album):
        rows = db.album_photos(self.conn, album)
        self.show_grid("%s  (%d photos)" % ("Album" if self.discreet else album, len(rows)), rows)

    def new_playlist(self):
        name = ask_name(self, "Nouvelle playlist")
        if name:
            pid = db.create_playlist(self.conn, name)
            self.show_playlist(pid)

    def show_playlist(self, pid):
        self.hover_leave()
        if self.hero is not None:
            try:
                self.hero.stop()
            except RuntimeError:
                pass
        self.playlist_page.open(pid)
        self.pages.setCurrentIndex(2)

    def card_menu(self, lst, pos):
        item = lst.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            lst.clearSelection()
            item.setSelected(True)
        vids = [it.data(Qt.UserRole) for it in lst.selectedItems()]
        if any(isinstance(v, int) and v < 0 for v in vids):           # videos d'un satellite
            menu = QMenu(self)
            a = menu.addAction("Lire" if len(vids) == 1 else "Lire les %d à la suite" % len(vids))
            if menu.exec(lst.viewport().mapToGlobal(pos)) == a:
                choice = self.ask_screen()
                if len(vids) == 1:
                    self.ctrl.play(vids[0], choice)
                else:
                    self.ctrl.play_queue(vids, choice, "sélection")
            return
        menu = QMenu(self)
        play = menu.addAction("Lire" if len(vids) == 1 else "Lire les %d à la suite" % len(vids))
        all_liked = all(db.is_liked(self.conn, v) for v in vids)
        like = menu.addAction("\u2665  Je n'aime plus" if all_liked else "\u2665  J'aime")
        sub = menu.addMenu("Ajouter à la playlist")
        fill_add_menu(self, sub, vids)
        assign = menu.addAction("Personnes et tags…")
        extra = self.extras_card_menu(menu, vids) if hasattr(self, "extras_card_menu") else {}
        chosen = menu.exec(lst.viewport().mapToGlobal(pos))
        if chosen in extra:
            extra[chosen]()
            return
        if chosen == assign:
            if AssignDialog(self, vids).exec() and self._last_grid and self.pages.currentIndex() == 1:
                self.show_grid(*self._last_grid)
        elif chosen == like:
            for v in vids:
                db.set_liked(self.conn, v, not all_liked)
                self.on_like_changed(v, not all_liked)
        elif chosen == play:
            choice = self.ask_screen()
            if len(vids) == 1:
                self.ctrl.play(vids[0], choice)
            else:
                self.ctrl.play_queue(vids, choice, "sélection")

    def bind_home_keys(self, mapping):
        for sc in getattr(self, "_home_shortcuts", []):
            sc.setEnabled(False)
            sc.deleteLater()
        self._home_shortcuts = []
        actions = {
            "h_next": lambda: self.hero_call("go", 1),
            "h_prev": lambda: self.hero_call("go", -1),
            "h_play": lambda: self.hero_call("play_current"),
            "h_multi": lambda: self.hero_call("play_multi"),
        }
        for key, fn in actions.items():
            seq = mapping.get(key)
            if seq:
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(fn)
                self._home_shortcuts.append(sc)

    def hero_call(self, name, *args):
        if self.hero is not None and self.pages.currentIndex() == 0:
            try:
                getattr(self.hero, name)(*args)
            except RuntimeError:
                self.hero = None

    def on_hero_image(self, vid, path):
        try:
            if self.hero:
                self.hero.set_image(vid, path)
        except RuntimeError:
            self.hero = None

    def row_menu(self, button, category, pos):
        if isinstance(category, tuple):
            return
        menu = QMenu(self)
        fav = db.get_setting(self.conn, "favorite_category")
        if fav == category:
            act = menu.addAction("Retirer des favoris")
            value = None
        else:
            act = menu.addAction("Définir comme thème favori")
            value = category
        if menu.exec(button.mapToGlobal(pos)) == act:
            db.set_setting(self.conn, "favorite_category", value)
            self.show_home()

    def show_home(self):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.clear_home()
        if not self.discreet:
            self.add_hero()
        self.add_playlist_row()
        self.add_photo_row()
        if not self.discreet:
            self.add_people_rows()
        self.add_row("Ajoutées récemment", db.recent(self.conn, ROW_LIMIT))
        for n, cat in enumerate(db.categories(self.conn), 1):
            label = ("Collection %d" % n) if self.discreet else cat["category"]
            self.add_row("%s  (%d)" % (label, cat["n"]),
                         db.by_category(self.conn, cat["category"], ROW_LIMIT), cat["category"])
        if getattr(self, "network", None) is not None:
            self.network.add_home_rows(ROW_LIMIT)
        if not db.list_sources(self.conn):
            hint = QLabel("Ouvre « Paramètres » → Bibliothèque → « Ajouter un dossier source… » "
                          "et choisis le dossier de tes vidéos sur le disque externe.")
            hint.setStyleSheet("color: #8a8a96; font-size: 12pt; padding: 40px;")
            self.home_layout.addWidget(hint)
        self.home_layout.addStretch(1)
        self.pages.setCurrentIndex(0)
        self.update_count()

    def show_grid(self, title, rows, category=None):
        self._last_grid = (title, rows, category)
        self.grid.category = category
        self.theme_bar.setVisible(category is not None)
        self.set_chips([], [])
        if category is not None:
            self.tb_loop.blockSignals(True)
            self.tb_loop.setChecked(db.get_setting(self.conn, self.spec_loop_key(category)) == "1")
            self.tb_loop.blockSignals(False)
        self.hover_leave()
        self.items = {}
        self.grid_title.setText(title)
        self.grid.fill(rows)
        self.pages.setCurrentIndex(1)

    def show_category(self, category):
        if isinstance(category, tuple) and category[0] == "node":    # theme d'un satellite
            rows = self.network.category_rows(category[1], category[3])
            self.show_grid("\U0001F4BB  %s  (%d)" % ("Collection" if self.discreet else category[2], len(rows)),
                           rows, category)
            return
        rows = db.by_category(self.conn, category)
        self.show_grid("%s  (%d)" % ("Collection" if self.discreet else category, len(rows)), rows, category)

    def run_search(self):
        text = self.search.text().strip()
        if not text:
            self.show_home()
            return
        rows = db.search(self.conn, text)
        if getattr(self, "network", None) is not None:
            rows = list(rows) + self.network.search(text)
        self.show_grid("Recherche « %s » : %d résultat(s)" % (text, len(rows)), rows)
        if not self.discreet:
            ppl, tags = P.find(self.conn, text)
            self.set_chips(ppl, tags)

    # --- personnes et tags --------------------------------------------------------
    def set_chips(self, ppl, tags):
        while self.chips_lay.count():
            w = self.chips_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in ppl:
            b = QPushButton("\U0001F464  " + r["name"])
            b.clicked.connect(lambda _=False, i=r["id"]: self.show_person(i))
            self.chips_lay.addWidget(b)
        for r in tags:
            b = QPushButton("#  " + r["name"])
            b.clicked.connect(lambda _=False, i=r["id"]: self.show_tag(i))
            self.chips_lay.addWidget(b)
        self.chips_lay.addStretch(1)
        self.chips.setVisible(bool(ppl or tags))

    def show_person(self, pid):
        row = self.conn.execute("SELECT name FROM people WHERE id=?", (pid,)).fetchone()
        if not row:
            return
        spec = ("person", pid, row["name"])
        rows = P.rows_for(self.conn, self.spec_ids(spec))
        self.show_grid("\U0001F464  %s  (%d)" % ("Personne" if self.discreet else row["name"], len(rows)), rows, spec)

    def show_tag(self, tid):
        row = self.conn.execute("SELECT name FROM tags WHERE id=?", (tid,)).fetchone()
        if not row:
            return
        spec = ("tag", tid, row["name"])
        rows = P.rows_for(self.conn, self.spec_ids(spec))
        self.show_grid("#  %s  (%d)" % ("Tag" if self.discreet else row["name"], len(rows)), rows, spec)

    def spec_ids(self, spec):
        if isinstance(spec, tuple) and spec[0] == "node":
            return self.network.category_ids(spec[1], spec[3])
        if isinstance(spec, tuple):
            f = db.kind_filter(self.conn, "v.")
            return (P.person_video_ids if spec[0] == "person" else P.tag_video_ids)(self.conn, spec[1], f)
        return db.category_video_ids(self.conn, spec)

    @staticmethod
    def spec_name(spec):
        return spec[2] if isinstance(spec, tuple) else spec

    @staticmethod
    def spec_loop_key(spec):
        return "loop:%s:%d" % (spec[0], spec[1]) if isinstance(spec, tuple) else "theme_loop:" + spec

    def open_workshop(self):
        dlg = PeopleWorkshop(self)
        dlg.exec()
        if dlg.dirty and self.pages.currentIndex() == 0:
            self.show_home()

    def add_people_rows(self):
        ppl = [r for r in P.list_people(self.conn) if r["n"] > 0][:40]
        tags = [r for r in P.list_tags(self.conn) if r["n"] > 0][:40]
        for title, rows, kind in (("Personnes", ppl, "person"), ("Tags", tags, "tag")):
            if not rows:
                continue
            btn = QPushButton("%s  (%d)" % (title, len(rows)))
            btn.setObjectName("rowTitle")
            btn.clicked.connect(lambda: self.open_workshop())
            btn.setToolTip("Gérer : atelier Personnes & tags")
            self.home_layout.addWidget(btn)
            lst = RowList(self)
            lst.setViewMode(QListView.IconMode)
            lst.setIconSize(QSize(160, 90))
            lst.setMovement(QListView.Static)
            lst.setFlow(QListView.LeftToRight)
            lst.setWrapping(False)
            lst.setGridSize(QSize(176, 128))
            lst.setFixedHeight(146)
            lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setTextElideMode(Qt.ElideRight)
            for r in rows:
                it = QListWidgetItem("%s  ·  %d" % (r["name"], r["n"]))
                it.setData(Qt.UserRole, r["id"])
                it.setIcon(self.cover_icon(r, kind))
                lst.addItem(it)
            if kind == "person":
                lst.itemActivated.connect(lambda it: self.show_person(it.data(Qt.UserRole)))
            else:
                lst.itemActivated.connect(lambda it: self.show_tag(it.data(Qt.UserRole)))
            self.home_layout.addWidget(lst)

    def cover_icon(self, item, kind):
        """Illustration d'une personne ou d'un tag : la vignette d'une de ses videos, tiree au hasard."""
        link, col = ("video_people", "person_id") if kind == "person" else ("video_tags", "tag_id")
        row = self.conn.execute(
            "SELECT v.thumb FROM %s l JOIN videos v ON v.id = l.video_id "
            "WHERE l.%s=? AND l.manual>=0 AND v.missing=0 AND v.thumb IS NOT NULL%s "
            "ORDER BY RANDOM() LIMIT 1" % (link, col, db.kind_filter(self.conn, "v.")), (item["id"],)).fetchone()
        if not row or not os.path.isfile(row["thumb"]):
            return self.label_icon(item["name"], kind)
        label = ("\U0001F464 " if kind == "person" else "#") + item["name"]
        tag = item
        w, h = 160, 90
        src = QPixmap(row["thumb"])
        if src.isNull():
            return self.label_icon(item["name"], kind)
        src = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, src, (src.width() - w) // 2, (src.height() - h) // 2, w, h)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(0, h * 0.35, 0, h)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 210))
        p.fillRect(pm.rect(), g)
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 12, QFont.Bold))
        p.drawText(pm.rect().adjusted(8, 0, -8, -6), Qt.AlignLeft | Qt.AlignBottom,
                   p.fontMetrics().elidedText(label, Qt.ElideRight, w - 16))
        p.end()
        return QIcon(pm)

    def label_icon(self, name, kind):
        pm = QPixmap(160, 90)
        hue = (sum(map(ord, name)) * 37) % 360
        pm.fill(QColor.fromHsv(hue, 90 if kind == "person" else 60, 70))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(255, 255, 255, 230))
        if kind == "person":
            initials = "".join(w[0] for w in name.split()[:2]).upper()
            p.setFont(QFont("Segoe UI", 28, QFont.Bold))
            p.drawText(pm.rect(), Qt.AlignCenter, initials)
        else:
            p.setFont(QFont("Segoe UI", 15, QFont.Bold))
            p.drawText(pm.rect().adjusted(6, 0, -6, 0), Qt.AlignCenter | Qt.TextWordWrap, "#" + name)
        p.end()
        return QIcon(pm)

    # --- sources et taches de fond ------------------------------------------
    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, "Choisir un dossier de vidéos")
        if not path:
            return
        root, rel = volumes.split_path(path)
        serial, label = volumes.volume_info(root)
        if not serial:
            self.status.setText("Impossible d'identifier ce disque.")
            return
        name = os.path.basename(os.path.normpath(path)) or (label or root)
        db.add_source(self.conn, serial, label, rel, name)
        self.start_scan()

    def start_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self.scan_worker = ScanWorker()
        self.scan_worker.progress.connect(self.status.setText)
        self.scan_worker.done.connect(self.scan_done)
        self.scan_worker.start()

    def scan_done(self, report):
        P.rebuild(self.conn)
        self.status.setText(report)
        if self.pages.currentIndex() == 0:
            self.show_home()
        self.start_thumbs()

    def start_thumbs(self):
        if self.thumb_worker and self.thumb_worker.isRunning():
            return
        self.thumb_worker = ThumbWorker()
        self.thumb_worker.thumb_ready.connect(self.on_thumb)
        self.thumb_worker.progress.connect(self.status.setText)
        self.thumb_worker.done.connect(self.update_count)
        self.thumb_worker.done.connect(self.previews.start_background)
        self.thumb_worker.start()

    def update_count(self):
        n, t = db.count(self.conn)
        self.setWindowTitle("Phoque Al  -  %d vidéos" % n)

    def play(self, item):
        choice = self.ask_screen()
        if item.data(Qt.UserRole + 2) == "photo":
            # photo : diaporama de la liste affichee, a partir de cette photo
            lst = item.listWidget()
            items = [lst.item(i) for i in range(lst.count())]
            photos_ = [it for it in items if it.data(Qt.UserRole + 2) == "photo"]
            ids = [it.data(Qt.UserRole) for it in photos_]
            self.ctrl.play_queue(ids, choice, "photos", photos_.index(item))
            return
        lst = item.listWidget()
        category = getattr(lst, "category", None)
        if category is not None and (isinstance(category, tuple)
                                     or db.get_setting(self.conn, "theme_playlists", "1") == "1"):
            ids = self.spec_ids(category)
            vid = item.data(Qt.UserRole)
            if vid in ids:
                self.ctrl.play_queue(ids, choice, self.spec_name(category), ids.index(vid),
                                     loop=db.get_setting(self.conn, self.spec_loop_key(category)) == "1", resume=True)
                return
        self.ctrl.play(item.data(Qt.UserRole), choice)

    # --- un theme comme une playlist -------------------------------------------------
    def theme_play(self, shuffle=False, multi=False):
        category = self.grid.category
        if not category:
            return
        ids = self.spec_ids(category)
        if not ids:
            return
        loop = self.tb_loop.isChecked()
        if shuffle:
            import random
            random.shuffle(ids)
        name = self.spec_name(category)
        if multi:
            self.ctrl.play_queue_multi(ids, name, loop)
        else:
            self.ctrl.play_queue(ids, self.ask_screen(), name, 0, loop)

    def theme_copy(self):
        category = self.grid.category
        if not category:
            return
        name = ask_name(self, "Copier en playlist", "" if self.discreet else self.spec_name(category))
        if name:
            pid = db.create_playlist(self.conn, name)
            db.add_to_playlist(self.conn, pid, self.spec_ids(category))
            self.show_playlist(pid)

    def show_help(self):
        QMessageBox.information(self, "Raccourcis clavier", keymap.help_text(self.ctrl.keymap))

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
        if dlg.library_changed:
            self.show_home()
            self.start_scan()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.hero is not None:
            try:
                self.hero.apply_height()
            except RuntimeError:
                self.hero = None

    def closeEvent(self, event):
        self.network.stop()
        self.web.stop()
        if self.remote is not None:
            self.remote.close()
        self.montage.stop()
        if self.hero is not None:
            try:
                self.hero.stop()
            except RuntimeError:
                pass
        self.music.stop()
        self.ctrl.close_all()
        self.hover_leave()
        self.previews.stop()
        for w in self.hero_workers:
            w.wait(5000)
        for w in (self.scan_worker, self.thumb_worker):
            if w and w.isRunning():
                w.requestInterruption()
                w.wait(3000)
        super().closeEvent(event)
