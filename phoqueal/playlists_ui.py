"""Interface des playlists : menu « Ajouter à la playlist » et page d'une playlist."""
import os
import random

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from . import db
from .hero import fmt_duration

ROW_ICON = QSize(160, 90)


def ask_name(parent, title, default=""):
    name, ok = QInputDialog.getText(parent, title, "Nom de la playlist :", text=default)
    name = name.strip()
    return name if ok and name else None


def fill_add_menu(main, menu, video_ids):
    """Remplit un sous-menu « Ajouter à la playlist » pour les videos donnees."""
    def add(pid, name):
        pl = db.get_playlist(main.conn, pid)
        if pl and pl["system"] == "likes":
            for vid in video_ids:
                db.set_liked(main.conn, vid, True)
                main.on_like_changed(vid, True)
        else:
            db.add_to_playlist(main.conn, pid, video_ids)
        main.status.setText("%d vidéo(s) ajoutée(s) à la playlist" % len(video_ids) if main.discreet
                            else "%d vidéo(s) ajoutée(s) à « %s »" % (len(video_ids), name))

    def new():
        name = ask_name(main, "Nouvelle playlist")
        if name:
            add(db.create_playlist(main.conn, name), name)

    for pl in db.list_playlists(main.conn):
        act = menu.addAction("%s  (%d)" % (pl["name"], pl["n"]))
        act.triggered.connect(lambda _=False, pid=pl["id"], n=pl["name"]: add(pid, n))
    if menu.actions():
        menu.addSeparator()
    menu.addAction("Nouvelle playlist…").triggered.connect(lambda _=False: new())


class PlaylistPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.conn = main.conn
        self.pid = None

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        self.info = QLabel()
        self.info.setObjectName("status")

        self.play_btn = QPushButton("\u25B6  Lire")
        self.play_btn.setObjectName("heroPrimary")
        self.shuffle_btn = QPushButton("Lecture aléatoire")
        n = len(main.ctrl.screens())
        self.multi_btn = QPushButton("Répartir sur les %d écrans" % n)
        self.multi_btn.setVisible(n > 1)
        self.loop_btn = QPushButton("En boucle")
        self.loop_btn.setCheckable(True)
        rename = QPushButton("Renommer")
        delete = QPushButton("Supprimer la playlist")
        self.rename_btn, self.delete_btn = rename, delete
        self.play_btn.clicked.connect(lambda: self.play(0))
        self.shuffle_btn.clicked.connect(lambda: self.play(0, shuffle=True))
        self.multi_btn.clicked.connect(lambda: self.play_multi())
        self.loop_btn.toggled.connect(lambda on: self.pid and db.set_playlist_loop(self.conn, self.pid, on))
        rename.clicked.connect(lambda: self.rename())
        delete.clicked.connect(lambda: self.delete())

        bar = QHBoxLayout()
        for b in (self.play_btn, self.shuffle_btn, self.multi_btn, self.loop_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(rename)
        bar.addWidget(delete)

        self.list = QListWidget()
        self.list.setIconSize(ROW_ICON)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSpacing(2)
        self.list.itemActivated.connect(lambda it: self.play(self.list.row(it)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(200)
        self._save_timer.timeout.connect(self.save_order)
        model = self.list.model()
        model.rowsMoved.connect(lambda *a: self._save_timer.start())
        model.rowsInserted.connect(lambda *a: self._save_timer.start())
        delete_key = QShortcut(QKeySequence(Qt.Key_Delete), self.list)
        delete_key.setContext(Qt.WidgetShortcut)
        delete_key.activated.connect(self.remove_selected)

        hint = QLabel("Glisser-déposer pour changer l'ordre  ·  Suppr pour retirer  ·  "
                      "double-clic pour lire à partir d'une vidéo  ·  N / P dans le lecteur : suivante / précédente")
        hint.setObjectName("status")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        lay.addSpacing(6)
        lay.addLayout(bar)
        lay.addWidget(self.list, 1)
        lay.addWidget(hint)

    # --- chargement ------------------------------------------------------------
    def open(self, pid):
        self.pid = pid
        self.reload()

    def reload(self):
        pl = db.get_playlist(self.conn, self.pid)
        if not pl:
            return
        hide = self.main.discreet
        self.title.setText("Playlist" if hide else
                           ("\u2665  " + pl["name"] if pl["system"] == "likes" else pl["name"]))
        system = pl["system"] is not None
        self.rename_btn.setVisible(not system)
        self.delete_btn.setVisible(not system)
        self.loop_btn.blockSignals(True)
        self.loop_btn.setChecked(bool(pl["loop"]))
        self.loop_btn.blockSignals(False)
        rows = db.playlist_videos(self.conn, self.pid)
        total = sum(r["duration"] or 0 for r in rows)
        self.info.setText("%d vidéo(s)%s" % (len(rows), ("   ·   " + fmt_duration(total)) if total else ""))
        self.list.blockSignals(True)
        self.list.clear()
        for r in rows:
            if hide:
                text = "Vidéo %d%s" % (self.list.count() + 1,
                                       ("   ·   " + fmt_duration(r["duration"])) if r["duration"] else "")
            else:
                text = "%s\n%s%s" % (r["title"], r["category"],
                                     ("   ·   " + fmt_duration(r["duration"])) if r["duration"] else "")
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, r["id"])
            it.setData(Qt.UserRole + 1, r["item_id"])
            if hide:
                it.setIcon(self.main._discreet_icon)
            elif r["thumb"] and os.path.isfile(r["thumb"]):
                it.setIcon(QIcon(r["thumb"]))
            self.list.addItem(it)
        self.list.blockSignals(False)

    def video_ids(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def name(self):
        pl = db.get_playlist(self.conn, self.pid) if self.pid else None
        return pl["name"] if pl else ""

    # --- edition -------------------------------------------------------------------
    def save_order(self):
        if self.pid:
            ids = [self.list.item(i).data(Qt.UserRole + 1) for i in range(self.list.count())]
            db.reorder_playlist(self.conn, self.pid, ids)

    def remove_selected(self):
        items = self.list.selectedItems()
        if items:
            db.remove_playlist_items(self.conn, [it.data(Qt.UserRole + 1) for it in items])
            self.reload()

    def context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.list.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        play = menu.addAction("Lire à partir d'ici")
        remove = menu.addAction("Retirer de la playlist")
        other = menu.addMenu("Ajouter aussi à…")
        fill_add_menu(self.main, other, [it.data(Qt.UserRole) for it in self.list.selectedItems()])
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen == play:
            self.play(self.list.row(item))
        elif chosen == remove:
            self.remove_selected()

    def rename(self):
        name = ask_name(self, "Renommer la playlist", self.name())
        if name:
            db.rename_playlist(self.conn, self.pid, name)
            self.reload()

    def delete(self):
        if QMessageBox.question(self, "Supprimer la playlist",
                                "Supprimer la playlist « %s » ?\nLes vidéos ne sont pas touchées." % self.name()
                                ) == QMessageBox.Yes:
            db.delete_playlist(self.conn, self.pid)
            self.pid = None
            self.main.show_home()

    # --- lecture ------------------------------------------------------------------
    def play(self, start=0, shuffle=False):
        ids = self.video_ids()
        if not ids:
            return
        if shuffle:
            random.shuffle(ids)
            start = 0
        self.main.ctrl.play_queue(ids, self.main.ask_screen(),
                                  self.name(), start, self.loop_btn.isChecked())

    def play_multi(self):
        ids = self.video_ids()
        if ids:
            self.main.ctrl.play_queue_multi(ids, self.name(), self.loop_btn.isChecked())
