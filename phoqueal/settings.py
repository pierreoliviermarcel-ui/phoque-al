"""Fenetre Parametres : dossiers de la bibliotheque et attribution des touches."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import db, keymap, photos, view, volumes

SKIP = ("$", ".")


class SettingsDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.setWindowTitle("Paramètres")
        self.resize(860, 640)
        self.library_changed = False
        self.excluded = db.excluded_folders(self.conn)
        self.mapping = dict(main.ctrl.keymap)

        tabs = QTabWidget()
        tabs.addTab(self._library_tab(), "Bibliothèque")
        tabs.addTab(self._display_tab(), "Affichage")
        tabs.addTab(self._photos_tab(), "Photos")
        tabs.addTab(self._keys_tab(), "Clavier")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(buttons)

    # ======================= BIBLIOTHEQUE ====================================
    def _library_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Coche les dossiers à inclure dans la bibliothèque. Les vidéos d'un dossier décoché "
                       "disparaissent de Phoque Al (les fichiers ne sont pas touchés).")
        intro.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dossier", "Disque", "Vidéos", "État"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        add = QPushButton("Ajouter un dossier source…")
        add.clicked.connect(lambda: self.add_source())
        remove = QPushButton("Retirer la source sélectionnée")
        remove.clicked.connect(lambda: self.remove_source())
        allon = QPushButton("Tout cocher")
        allon.clicked.connect(lambda: self.check_all(True))
        alloff = QPushButton("Tout décocher")
        alloff.clicked.connect(lambda: self.check_all(False))
        self.theme_pl = QCheckBox("Traiter chaque thème (sous-dossier) comme une playlist : "
                                  "les vidéos s'enchaînent automatiquement")
        self.theme_pl.setChecked(db.get_setting(self.conn, "theme_playlists", "1") == "1")
        sat = QPushButton("Ordinateurs satellites…")
        sat.setToolTip("Utiliser un autre ordinateur (ex. un portable relié par câble) comme écrans et bibliothèque en plus")
        sat.clicked.connect(lambda: self.open_nodes())
        dup = QPushButton("Rechercher les doublons…")
        dup.clicked.connect(lambda: self.open_dupes())
        row = QHBoxLayout()
        for b in (add, remove, dup, sat):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(allon)
        row.addWidget(alloff)
        lay.addWidget(intro)
        lay.addWidget(self.tree, 1)
        lay.addLayout(row)
        lay.addWidget(self.theme_pl)
        self.fill_tree()
        return w

    def fill_tree(self):
        self.tree.clear()
        drives = volumes.connected_drives()
        for src in db.list_sources(self.conn):
            path = volumes.resolve(src["serial"], src["rel_root"], drives)
            counts = db.folder_counts(self.conn, src["id"])
            top = QTreeWidgetItem([src["name"], src["label"] or src["serial"],
                                   str(sum(counts.values())), "branché" if path else "disque absent"])
            top.setData(0, Qt.UserRole, src["id"])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            if path:
                try:
                    names = sorted((e.name for e in os.scandir(path)
                                    if e.is_dir() and not e.name.startswith(SKIP)
                                    and e.name not in ("System Volume Information", "_Corbeille_Focal")),
                                   key=str.lower)
                except OSError:
                    names = []
            else:
                names = sorted((c for c in counts if c != src["name"]), key=str.lower)
            excl = self.excluded.get(src["id"], set())
            for name in names:
                child = QTreeWidgetItem([name, "", str(counts.get(name, "–")), ""])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if name in excl else Qt.Checked)
                child.setData(0, Qt.UserRole, name)
                top.addChild(child)
            top.setExpanded(True)

    def open_nodes(self):
        from .network import NodesDialog
        NodesDialog(self.main).exec()

    def open_dupes(self):
        from .dupes_ui import DuplicatesDialog
        DuplicatesDialog(self.main).exec()
        self.fill_tree()

    def collect_excluded(self):
        result = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            sid = top.data(0, Qt.UserRole)
            off = {top.child(j).data(0, Qt.UserRole) for j in range(top.childCount())
                   if top.child(j).checkState(0) == Qt.Unchecked}
            # garder les exclusions de sous-dossiers non affiches (disque absent)
            shown = {top.child(j).data(0, Qt.UserRole) for j in range(top.childCount())}
            off |= {n for n in self.excluded.get(sid, set()) if n not in shown}
            if off:
                result[sid] = off
        return result

    def check_all(self, on):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, "Choisir un dossier de vidéos")
        if not path:
            return
        root, rel = volumes.split_path(path)
        serial, label = volumes.volume_info(root)
        if not serial:
            QMessageBox.warning(self, "Paramètres", "Impossible d'identifier ce disque.")
            return
        name = os.path.basename(os.path.normpath(path)) or (label or root)
        self.excluded = self.collect_excluded()
        db.add_source(self.conn, serial, label, rel, name)
        self.library_changed = True
        self.fill_tree()

    def remove_source(self):
        item = self.tree.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is None:
            return
        if QMessageBox.question(
                self, "Retirer la source",
                "Retirer « %s » de Phoque Al ?\nLes vidéos ne sont pas supprimées du disque, "
                "elles disparaissent seulement de la bibliothèque." % item.text(0)) != QMessageBox.Yes:
            return
        self.excluded = self.collect_excluded()
        sid = item.data(0, Qt.UserRole)
        self.excluded.pop(sid, None)
        db.remove_source(self.conn, sid)
        self.library_changed = True
        self.fill_tree()

    # ======================= AFFICHAGE =======================================
    def _display_tab(self):
        from PySide6.QtWidgets import QComboBox
        w = QWidget()
        form = QFormLayout(w)
        self.view_combo = QComboBox()
        current = db.get_setting(self.conn, "view_mode", "original")
        for key, label in view.MODES:
            self.view_combo.addItem(label, key)
            if key == current:
                self.view_combo.setCurrentIndex(self.view_combo.count() - 1)
        help_ = QLabel("« Remplir l'écran » rogne chaque vidéo au format exact de SON écran (et de chaque case "
                       "en mosaïque) : idéal avec des écrans de formats différents. Les formats fixes (4:3, 16:9…) "
                       "rognent la vidéo à ce format. Pendant la lecture : touche C (lecteur actif), Maj + C (tous), "
                       "ou le bouton ▭ de la télécommande.")
        help_.setWordWrap(True)
        help_.setObjectName("status")
        from .hero import HeroBanner
        self.hero_size = QComboBox()
        cur = db.get_setting(self.conn, "hero_size", "0.72")
        for key, label in HeroBanner.SIZES:
            self.hero_size.addItem(label, key)
            if key == cur:
                self.hero_size.setCurrentIndex(self.hero_size.count() - 1)
        form.addRow("Taille de la grande bannière", self.hero_size)
        self.hero_mode = QComboBox()
        self.hero_mode.addItem("Vidéo au hasard + thèmes, favoris, à reprendre…", "mix")
        self.hero_mode.addItem("Uniquement des vidéos au hasard", "random")
        self.hero_mode.setCurrentIndex(1 if db.get_setting(self.conn, "hero_mode", "mix") == "random" else 0)
        form.addRow("Contenu de la bannière", self.hero_mode)
        self.hero_secs = QDoubleSpinBox()
        self.hero_secs.setRange(3, 300)
        self.hero_secs.setDecimals(0)
        self.hero_secs.setSuffix(" s")
        self.hero_secs.setValue(float(db.get_setting(self.conn, "hero_seconds", "10")))
        form.addRow("Changement de diapositive toutes les", self.hero_secs)
        form.addRow("Format d'image par défaut", self.view_combo)
        self.blur_box = QCheckBox("Combler les bords avec un fond flou quand l'image ne remplit pas l'écran")
        self.blur_box.setChecked(db.get_setting(self.conn, "blur_fill", "1") == "1")
        form.addRow("", self.blur_box)
        self.ask_box = QCheckBox("Quand tous les écrans sont occupés, demander où lancer la vidéo (numéros affichés)")
        self.ask_box.setChecked(db.get_setting(self.conn, "ask_screen", "1") == "1")
        form.addRow("", self.ask_box)
        self.start_mode = QComboBox()
        self.start_mode.addItem("À un moment au hasard (dans les 80 derniers %)", "random")
        self.start_mode.addItem("Au début (ou là où je m'étais arrêté)", "begin")
        self.start_mode.setCurrentIndex(0 if db.get_setting(self.conn, "start_mode", "random") == "random" else 1)
        form.addRow("Départ des vidéos", self.start_mode)
        start_help = QLabel("S'applique aux lecteurs, playlists, thèmes, mosaïques et à la vidéo de fond de la bannière. "
                            "La diapositive « Reprendre » reprend toujours là où tu t'étais arrêté.")
        start_help.setWordWrap(True)
        start_help.setObjectName("status")
        form.addRow(start_help)
        self.remote_on_play = QCheckBox("Afficher la télécommande quand une vidéo démarre")
        self.remote_on_play.setChecked(db.get_setting(self.conn, "remote_on_play", "0") == "1")
        self.remote_wake = QCheckBox("Faire réapparaître la télécommande quand la souris bouge")
        self.remote_wake.setChecked(db.get_setting(self.conn, "remote_wake", "1") == "1")
        form.addRow("Télécommande", self.remote_on_play)
        form.addRow("", self.remote_wake)
        form.addRow(help_)
        return w

    # ======================= PHOTOS ==========================================
    def _photos_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.photos_on = QCheckBox("Inclure les photos (fichiers JPG, PNG, WEBP… et archives ZIP / RAR / CBZ / CBR)")
        self.photos_on.setChecked(db.get_setting(self.conn, "photos_on", "1") == "1")
        self.mix = QCheckBox("Mélanger photos et vidéos")
        self.mix.setChecked(db.mix_photos(self.conn))
        mix_help = QLabel("Coché : les albums photo apparaissent aussi parmi les thèmes, dans la recherche, les "
                          "mosaïques et le montage au rythme. Décoché : les photos restent dans la rangée "
                          "« Albums photo ».")
        mix_help.setWordWrap(True)
        mix_help.setObjectName("status")
        self.photo_secs = QDoubleSpinBox()
        self.photo_secs.setRange(1, 120)
        self.photo_secs.setDecimals(0)
        self.photo_secs.setSuffix(" s")
        self.photo_secs.setValue(float(db.get_setting(self.conn, "photo_seconds", "6")))
        tool = photos.setup_rar()
        rar = QLabel(("Archives RAR : lues avec %s." % tool) if tool else
                     "Archives RAR : aucun outil trouvé. Installe 7-Zip ou WinRAR, ou place UnRAR.exe "
                     "dans Phoque Al\\tools. Les archives ZIP fonctionnent toujours.")
        rar.setWordWrap(True)
        form.addRow(self.photos_on)
        form.addRow(self.mix)
        form.addRow(mix_help)
        form.addRow("Durée d'affichage d'une photo", self.photo_secs)
        form.addRow(rar)
        return w

    # ======================= CLAVIER =========================================
    def _keys_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Clique dans une case puis appuie sur la nouvelle touche (ou combinaison). "
                       "↺ remet la touche par défaut, ✕ la désactive.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Action", "Touche", "", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 40)
        self.table.setColumnWidth(3, 40)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.editors = {}
        for scope, title in keymap.SCOPES:
            r = self.table.rowCount()
            self.table.insertRow(r)
            head = QTableWidgetItem(title)
            fh = QFont(head.font())
            fh.setBold(True)
            head.setFont(fh)
            head.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, head)
            self.table.setSpan(r, 0, 1, 4)
            for key, sc, label, default in keymap.ACTIONS:
                if sc != scope:
                    continue
                r = self.table.rowCount()
                self.table.insertRow(r)
                it = QTableWidgetItem(label)
                it.setFlags(Qt.ItemIsEnabled)
                it.setToolTip("Par défaut : %s" % keymap.pretty(default))
                self.table.setItem(r, 0, it)
                ed = QKeySequenceEdit(QKeySequence(self.mapping.get(key) or ""))
                if hasattr(ed, "setMaximumSequenceLength"):
                    ed.setMaximumSequenceLength(1)
                ed.keySequenceChanged.connect(lambda seq, k=key: self.key_changed(k, seq))
                self.editors[key] = ed
                self.table.setCellWidget(r, 1, ed)
                reset = QPushButton("↺")
                reset.setToolTip("Remettre : %s" % keymap.pretty(default))
                reset.clicked.connect(lambda _=False, k=key: self.set_key(k, keymap.DEFAULTS[k]))
                clear = QPushButton("✕")
                clear.setToolTip("Désactiver cette touche")
                clear.clicked.connect(lambda _=False, k=key: self.set_key(k, ""))
                self.table.setCellWidget(r, 2, reset)
                self.table.setCellWidget(r, 3, clear)
        self.conflict_label = QLabel()
        self.conflict_label.setWordWrap(True)
        self.conflict_label.setStyleSheet("color: #e5686a;")
        all_default = QPushButton("Tout remettre par défaut")
        all_default.clicked.connect(lambda: [self.set_key(k, v) for k, v in keymap.DEFAULTS.items()])
        row = QHBoxLayout()
        row.addWidget(self.conflict_label, 1)
        row.addWidget(all_default)
        lay.addWidget(intro)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        self.refresh_conflicts()
        return w

    def key_changed(self, key, seq):
        self.mapping[key] = seq.toString(QKeySequence.PortableText)
        self.refresh_conflicts()

    def set_key(self, key, seq):
        self.editors[key].setKeySequence(QKeySequence(seq))
        self.mapping[key] = seq
        self.refresh_conflicts()

    def refresh_conflicts(self):
        clash = keymap.conflicts(self.mapping)
        if clash:
            lines = ["%s est utilisée pour : %s" % (keymap.pretty(s), ", ".join(n)) for s, n in clash.items()]
            self.conflict_label.setText("Conflits :\n" + "\n".join(lines))
        else:
            self.conflict_label.setText("")
        return clash

    # ======================= ENREGISTRER =====================================
    def save(self):
        if self.refresh_conflicts():
            QMessageBox.warning(self, "Paramètres",
                                "Certaines touches sont attribuées à plusieurs actions. Corrige-les avant "
                                "d'enregistrer (voir en bas de l'onglet Clavier).")
            return
        new_excluded = self.collect_excluded()
        if new_excluded != db.excluded_folders(self.conn):
            db.set_excluded_folders(self.conn, new_excluded)
            self.library_changed = True
        photos_on = "1" if self.photos_on.isChecked() else "0"
        if photos_on != db.get_setting(self.conn, "photos_on", "1"):
            db.set_setting(self.conn, "photos_on", photos_on)
            self.library_changed = True
        if self.mix.isChecked() != db.mix_photos(self.conn):
            db.set_setting(self.conn, "mix_photos", "1" if self.mix.isChecked() else "0")
            self.library_changed = True
        db.set_setting(self.conn, "photo_seconds", str(int(self.photo_secs.value())))
        db.set_setting(self.conn, "theme_playlists", "1" if self.theme_pl.isChecked() else "0")
        db.set_setting(self.conn, "hero_seconds", str(int(self.hero_secs.value())))
        if self.main.hero is not None:
            try:
                self.main.hero.slide_ms = int(self.hero_secs.value()) * 1000
            except RuntimeError:
                pass
        if self.hero_mode.currentData() != db.get_setting(self.conn, "hero_mode", "mix"):
            db.set_setting(self.conn, "hero_mode", self.hero_mode.currentData())
            self.library_changed = True          # reconstruit l'accueil
        if self.hero_size.currentData() != db.get_setting(self.conn, "hero_size", "0.72"):
            db.set_setting(self.conn, "hero_size", self.hero_size.currentData())
            if self.main.hero is not None:
                self.main.hero.apply_height()
        db.set_setting(self.conn, "start_mode", self.start_mode.currentData())
        db.set_setting(self.conn, "remote_on_play", "1" if self.remote_on_play.isChecked() else "0")
        db.set_setting(self.conn, "remote_wake", "1" if self.remote_wake.isChecked() else "0")
        if self.main.remote is not None:
            self.main.remote.wake_on_move = self.remote_wake.isChecked()
        db.set_setting(self.conn, "ask_screen", "1" if self.ask_box.isChecked() else "0")
        blur = "1" if self.blur_box.isChecked() else "0"
        if blur != db.get_setting(self.conn, "blur_fill", "1"):
            db.set_setting(self.conn, "blur_fill", blur)
            for w in self.main.ctrl.players.values():
                for t in (w.tiles if hasattr(w, "tiles") else [w]):
                    if hasattr(t, "set_blur"):
                        t.set_blur(blur == "1", show=False)
        mode = self.view_combo.currentData()
        if mode != db.get_setting(self.conn, "view_mode", "original"):
            db.set_setting(self.conn, "view_mode", mode)
            self.main.ctrl.each(lambda p: p.set_view(mode, show=False))
        keymap.save(self.conn, self.mapping)
        self.main.ctrl.apply_keymap(keymap.load(self.conn))
        self.accept()
