"""Fenetre de creation / chargement des mosaiques, et selecteur de video."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from . import db
from .mosaic import GRIDS


class VideoPicker(QDialog):
    """Petite fenetre de recherche pour choisir une video."""

    def __init__(self, parent, conn):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Choisir une vidéo")
        self.resize(560, 520)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher (titre, studio, thème)…")
        self.list = QListWidget()
        self.search.textChanged.connect(self.refresh)
        self.list.itemActivated.connect(lambda _: self.accept())
        lay = QVBoxLayout(self)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        self.refresh("")

    def refresh(self, text):
        self.list.clear()
        rows = db.search(self.conn, text, 300) if text.strip() else db.recent(self.conn, 300)
        for r in rows:
            it = QListWidgetItem("%s   —   %s" % (r["title"], r["category"]))
            it.setData(Qt.UserRole, r["id"])
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    @classmethod
    def pick(cls, parent, conn):
        dlg = cls(parent, conn)
        if dlg.exec() and dlg.list.currentItem():
            return dlg.list.currentItem().data(Qt.UserRole)
        return None


class MosaicDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.ctrl = main.ctrl
        self.setWindowTitle("Mosaïque")
        self.resize(900, 540)

        # --- mosaiques enregistrees
        saved_box = QGroupBox("Mosaïques enregistrées")
        self.saved = QListWidget()
        self.saved.itemActivated.connect(lambda _: self.load_selected())
        load_btn = QPushButton("Charger")
        load_btn.clicked.connect(lambda: self.load_selected())
        del_btn = QPushButton("Supprimer")
        del_btn.clicked.connect(lambda: self.delete_selected())
        self.load_screen = QComboBox()
        self.load_screen.addItem("sur son écran d'origine", 0)
        n = len(self.ctrl.screens())
        for i in range(1, n + 1):
            self.load_screen.addItem("sur l'écran %d" % i, i)
        sl = QVBoxLayout(saved_box)
        sl.addWidget(self.saved, 1)
        sl.addWidget(self.load_screen)
        row = QHBoxLayout()
        row.addWidget(load_btn)
        row.addWidget(del_btn)
        sl.addLayout(row)
        self.fill_saved()

        # --- nouvelle mosaique
        new_box = QGroupBox("Nouvelle mosaïque")
        form = QFormLayout(new_box)
        self.screen = QComboBox()
        for i in range(1, n + 1):
            self.screen.addItem("Écran %d" % i, i)
        self.screen.setCurrentIndex(max(0, self.ctrl.pick_screen(0) - 1))
        self.count = QComboBox()
        for c, (r, k) in sorted(GRIDS.items()):
            self.count.addItem("%d vidéos  (%d × %d)" % (c, r, k), c)
        self.count.setCurrentIndex(list(sorted(GRIDS)).index(4))
        self.source = QComboBox()
        self.source.addItem("Toute la bibliothèque", ("all", None))
        for cat in db.categories(self.conn):
            self.source.addItem("Thème : %s  (%d)" % (cat["category"], cat["n"]), ("theme", cat["category"]))
        for pl in db.list_playlists(self.conn):
            self.source.addItem("Playlist : %s  (%d)" % (pl["name"], pl["n"]), ("playlist", pl["id"]))
        from . import people as P
        for r in P.list_people(self.conn):
            if r["n"]:
                self.source.addItem("Personne : %s  (%d)" % (r["name"], r["n"]), ("person", r["id"]))
        for r in P.list_tags(self.conn):
            if r["n"]:
                self.source.addItem("Tag : %s  (%d)" % (r["name"], r["n"]), ("tag", r["id"]))
        self.order = QComboBox()
        self.order.addItem("Aléatoire", "random")
        self.order.addItem("Dans l'ordre", "ordered")
        self.start = QComboBox()
        self.start.addItem("À un moment au hasard", "random")
        self.start.addItem("Au début", "begin")
        self.end = QComboBox()
        self.end.addItem("Passer à la vidéo suivante (du thème / de la playlist)", "next")
        self.end.addItem("Rejouer la même vidéo", "replay")
        self.sound = QComboBox()
        self.sound.addItem("Première vidéo seulement", "first")
        self.sound.addItem("Toutes les vidéos", "all")
        self.sound.addItem("Aucune", "none")
        self.name = QLineEdit()
        self.name.setPlaceholderText("nom automatique si vide")
        self.save_now = QCheckBox("Enregistrer cette mosaïque dès son lancement")
        form.addRow("Écran", self.screen)
        form.addRow("Nombre de vidéos", self.count)
        form.addRow("Vidéos tirées de", self.source)
        form.addRow("Ordre", self.order)
        form.addRow("Départ des vidéos", self.start)
        form.addRow("Quand une vidéo finit", self.end)
        form.addRow("Son", self.sound)
        form.addRow("Nom", self.name)
        form.addRow("", self.save_now)
        launch = QPushButton("\u25B6  Lancer la mosaïque")
        launch.setObjectName("heroPrimary")
        launch.clicked.connect(lambda: self.launch())
        form.addRow("", launch)
        hint = QLabel("Dans la mosaïque : souris = une vidéo (clic droit pour le menu, molette = volume, "
                      "Ctrl + molette = avancer, double-clic = agrandir), clavier = toutes les vidéos.")
        hint.setWordWrap(True)
        hint.setObjectName("status")
        form.addRow(hint)

        lay = QHBoxLayout(self)
        lay.addWidget(saved_box, 2)
        lay.addWidget(new_box, 3)

    def fill_saved(self):
        self.saved.clear()
        for m in db.list_mosaics(self.conn):
            when = time.strftime("%d/%m %H:%M", time.localtime(m["updated_at"] or 0))
            it = QListWidgetItem("%s\nécran %s  ·  %s" % (m["name"], m["screen"] or "?", when))
            it.setData(Qt.UserRole, m["id"])
            self.saved.addItem(it)

    def config(self):
        src = self.source.currentData()
        name = self.name.text().strip() or "%s – %d vidéos" % (
            self.source.currentText().split("  (")[0], self.count.currentData())
        return {"name": name, "count": self.count.currentData(),
                "source": {"type": src[0], "value": src[1]}, "order": self.order.currentData(),
                "start": self.start.currentData(), "end": self.end.currentData(),
                "sound": self.sound.currentData()}

    def launch(self):
        mw = self.ctrl.open_mosaic(self.config(), screen=self.screen.currentData())
        if mw and self.save_now.isChecked():
            mw.mosaic_id = db.save_mosaic(self.conn, None, mw.config["name"], mw.number, mw.config,
                                          [t.state() for t in mw.tiles])
        self.accept()

    def load_selected(self):
        item = self.saved.currentItem()
        if not item:
            return
        m = db.get_mosaic(self.conn, item.data(Qt.UserRole))
        if not m:
            return
        screen = self.load_screen.currentData() or m["screen"] or 0
        self.ctrl.open_mosaic(m["config"], state=m["state"], screen=screen, mosaic_id=m["id"])
        self.accept()

    def delete_selected(self):
        item = self.saved.currentItem()
        if item and QMessageBox.question(self, "Supprimer", "Supprimer cette mosaïque enregistrée ?") == QMessageBox.Yes:
            db.delete_mosaic(self.conn, item.data(Qt.UserRole))
            self.fill_saved()
