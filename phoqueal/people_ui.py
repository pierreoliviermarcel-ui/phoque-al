"""Atelier « Personnes & tags » et fenetre d'attribution manuelle."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import people as P


class _Tab(QWidget):
    """Un onglet : propositions a gauche, elements valides a droite."""

    def __init__(self, dlg, kind):
        super().__init__()
        self.dlg = dlg
        self.conn = dlg.conn
        self.kind = kind                      # "person" | "tag"
        person = kind == "person"

        # --- propositions
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["Proposition (modifiable)", "Fichiers"])
        self.props.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.props.verticalHeader().setVisible(False)
        self.props.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.min_files = QSpinBox()
        self.min_files.setRange(1, 100)
        self.min_files.setValue(3 if person else 10)
        self.min_files.setPrefix("cocher ≥ ")
        self.min_files.setSuffix(" fichiers")
        check = QPushButton("Cocher")
        check.clicked.connect(lambda: self.check_min())
        uncheck = QPushButton("Tout décocher")
        uncheck.clicked.connect(lambda: self.set_checks(False))
        accept = QPushButton("\u2713  Valider les cochés")
        accept.setObjectName("heroPrimary")
        accept.clicked.connect(lambda: self.accept_checked())
        reject = QPushButton("\u2715  Rejeter les cochés")
        reject.clicked.connect(lambda: self.reject_checked())
        swap = QPushButton("C'est plutôt un tag" if person else "C'est plutôt une personne")
        swap.clicked.connect(lambda: self.swap_checked())
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Propositions</b> tirées des noms de fichiers — coche, corrige le nom si besoin, puis valide."))
        left.addWidget(self.props, 1)
        r1 = QHBoxLayout()
        for w in (self.min_files, check, uncheck):
            r1.addWidget(w)
        r1.addStretch(1)
        left.addLayout(r1)
        r2 = QHBoxLayout()
        for w in (accept, reject, swap):
            r2.addWidget(w)
        left.addLayout(r2)

        # --- valides
        self.valid = QListWidget()
        self.valid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        add = QPushButton("Ajouter…")
        add.clicked.connect(lambda: self.add_manual())
        ren = QPushButton("Renommer…")
        ren.clicked.connect(lambda: self.rename())
        extra = QPushButton("Autres noms…" if person else "Mots-clés…")
        extra.setToolTip("Autres orthographes / pseudos reconnus dans les fichiers" if person else
                         "Mots ou expressions qui déclenchent ce tag dans les noms de fichiers")
        extra.clicked.connect(lambda: self.edit_extra())
        dele = QPushButton("Supprimer")
        dele.clicked.connect(lambda: self.delete())
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>%s</b>" % ("Personnes validées" if person else "Tags validés")))
        right.addWidget(self.valid, 1)
        r3 = QHBoxLayout()
        for w in (add, ren, extra, dele):
            r3.addWidget(w)
        if person:
            merge = QPushButton("Fusionner")
            merge.setToolTip("Sélectionne 2 noms ou plus (Ctrl + clic) : le premier garde son nom, les autres deviennent des alias")
            merge.clicked.connect(lambda: self.merge())
            r3.addWidget(merge)
        right.addLayout(r3)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 3)
        lay.addLayout(right, 2)

    # --- remplissage ------------------------------------------------------------------
    def fill(self, proposals):
        self.props.setRowCount(0)
        for name, count, examples in proposals:
            r = self.props.rowCount()
            self.props.insertRow(r)
            it = QTableWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, name)
            if examples:
                it.setToolTip("Exemples :\n" + "\n".join(examples))
            self.props.setItem(r, 0, it)
            c = QTableWidgetItem(str(count))
            c.setFlags(Qt.ItemIsEnabled)
            c.setData(Qt.UserRole, count)
            self.props.setItem(r, 1, c)
        self.fill_valid()

    def fill_valid(self):
        self.valid.clear()
        rows = P.list_people(self.conn) if self.kind == "person" else P.list_tags(self.conn)
        for r in rows:
            extra = r["aliases"] if self.kind == "person" else r["keywords"]
            it = QListWidgetItem("%s   (%d)" % (r["name"], r["n"]))
            it.setData(Qt.UserRole, r["id"])
            it.setData(Qt.UserRole + 1, r["name"])
            if extra:
                it.setToolTip(("Autres noms : " if self.kind == "person" else "Mots-clés : ") + extra.replace("|", ", "))
            self.valid.addItem(it)

    def checked_rows(self):
        return [r for r in range(self.props.rowCount())
                if self.props.item(r, 0).checkState() == Qt.Checked]

    def set_checks(self, on):
        for r in range(self.props.rowCount()):
            self.props.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def check_min(self):
        m = self.min_files.value()
        for r in range(self.props.rowCount()):
            if self.props.item(r, 1).data(Qt.UserRole) >= m:
                self.props.item(r, 0).setCheckState(Qt.Checked)

    def _remove_rows(self, rows):
        for r in sorted(rows, reverse=True):
            self.props.removeRow(r)

    # --- actions sur les propositions ----------------------------------------------------
    def accept_checked(self):
        rows = self.checked_rows()
        for r in rows:
            name = self.props.item(r, 0).text().strip()
            if not name:
                continue
            if self.kind == "person":
                P.add_person(self.conn, name)
            else:
                P.add_tag(self.conn, name.capitalize() if name.islower() else name, [name.lower()])
        self._remove_rows(rows)
        self.dlg.changed()

    def reject_checked(self):
        rows = self.checked_rows()
        for r in rows:
            P.reject(self.conn, self.kind, self.props.item(r, 0).data(Qt.UserRole))
        self._remove_rows(rows)

    def swap_checked(self):
        rows = self.checked_rows()
        for r in rows:
            name = self.props.item(r, 0).text().strip()
            if self.kind == "person":
                P.add_tag(self.conn, name, [name.lower()])
            else:
                P.add_person(self.conn, " ".join(w.capitalize() for w in name.split()))
        self._remove_rows(rows)
        self.dlg.changed()

    # --- actions sur les valides -----------------------------------------------------------
    def table(self):
        return "people" if self.kind == "person" else "tags"

    def current(self):
        it = self.valid.currentItem()
        return (it.data(Qt.UserRole), it.data(Qt.UserRole + 1)) if it else (None, None)

    def add_manual(self):
        name, ok = QInputDialog.getText(self, "Ajouter", "Nom de la personne :" if self.kind == "person" else "Nom du tag :")
        if ok and name.strip():
            if self.kind == "person":
                P.add_person(self.conn, name)
            else:
                P.add_tag(self.conn, name, [name.lower()])
            self.dlg.changed()

    def rename(self):
        iid, old = self.current()
        if iid is None:
            return
        name, ok = QInputDialog.getText(self, "Renommer", "Nouveau nom :", text=old)
        if ok and name.strip():
            P.rename(self.conn, self.table(), iid, name)
            self.dlg.changed()

    def edit_extra(self):
        iid, name = self.current()
        if iid is None:
            return
        row = self.conn.execute("SELECT * FROM %s WHERE id=?" % self.table(), (iid,)).fetchone()
        current = (row["aliases"] if self.kind == "person" else row["keywords"]).replace("|", ", ")
        label = ("Autres noms reconnus pour « %s » (séparés par des virgules) :" if self.kind == "person"
                 else "Mots-clés qui déclenchent le tag « %s » (séparés par des virgules) :") % name
        text, ok = QInputDialog.getText(self, name, label, text=current)
        if ok:
            values = [v.strip() for v in text.split(",") if v.strip()]
            if self.kind == "person":
                P.set_aliases(self.conn, iid, values)
            else:
                P.set_keywords(self.conn, iid, values)
            self.dlg.changed()

    def delete(self):
        items = self.valid.selectedItems()
        if not items:
            return
        if QMessageBox.question(self, "Supprimer", "Supprimer %d élément(s) ? (les vidéos ne sont pas touchées)"
                                % len(items)) == QMessageBox.Yes:
            for it in items:
                P.delete(self.conn, self.table(), it.data(Qt.UserRole))
            self.dlg.changed()

    def merge(self):
        items = self.valid.selectedItems()
        if len(items) < 2:
            QMessageBox.information(self, "Fusionner", "Sélectionne au moins 2 noms (Ctrl + clic).")
            return
        names = [it.data(Qt.UserRole + 1) for it in items]
        keep, ok = QInputDialog.getItem(self, "Fusionner", "Nom à garder :", names, 0, False)
        if not ok:
            return
        keep_id = items[names.index(keep)].data(Qt.UserRole)
        for it in items:
            if it.data(Qt.UserRole) != keep_id:
                P.merge_people(self.conn, keep_id, it.data(Qt.UserRole))
        self.dlg.changed()


class PeopleWorkshop(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.setWindowTitle("Personnes & tags")
        self.resize(1150, 700)
        self.dirty = False
        intro = QLabel("Phoque Al analyse les noms de tes fichiers et propose des noms de personnes et des tags. "
                       "Ce que tu valides est ensuite reconnu automatiquement dans tous les fichiers (même collé : "
                       "« skylarsnow », « SofieMarie »). Pour les fichiers sans nom exploitable : clic droit sur une "
                       "vignette → « Personnes et tags… ».")
        intro.setWordWrap(True)
        self.tabs = QTabWidget()
        self.p_tab = _Tab(self, "person")
        self.t_tab = _Tab(self, "tag")
        self.tabs.addTab(self.p_tab, "Personnes")
        self.tabs.addTab(self.t_tab, "Tags")
        self.status = QLabel()
        self.status.setObjectName("status")
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(bottom)
        self.reload()

    def reload(self):
        ppl, tags = P.propose(self.conn)
        self.p_tab.fill(ppl)
        self.t_tab.fill(tags)

    def changed(self):
        self.dirty = True
        np_, nt = P.rebuild(self.conn)
        self.p_tab.fill_valid()
        self.t_tab.fill_valid()
        self.status.setText("Reconnaissance mise à jour : %d lien(s) personne, %d lien(s) tag." % (np_, nt))


def _st(state):
    return "c" if state == Qt.Checked else "p" if state == Qt.PartiallyChecked else "u"


class AssignDialog(QDialog):
    """Attribuer des personnes / tags a une ou plusieurs videos."""

    def __init__(self, main, video_ids):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.ids = video_ids
        self.setWindowTitle("Personnes et tags — %d vidéo(s)" % len(video_ids))
        self.resize(720, 560)
        links = [P.video_links(self.conn, v) for v in video_ids]
        self.lists = {}
        cols = QHBoxLayout()
        for kind, title, rows in (("person", "Personnes", P.list_people(self.conn)),
                                  ("tag", "Tags", P.list_tags(self.conn))):
            box = QVBoxLayout()
            box.addWidget(QLabel("<b>%s</b>" % title))
            flt = QLineEdit()
            flt.setPlaceholderText("Filtrer…")
            lst = QListWidget()
            for r in rows:
                it = QListWidgetItem(r["name"])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                k = 0 if kind == "person" else 1
                n = sum(1 for l in links if r["id"] in l[k])
                it.setCheckState(Qt.Checked if n == len(links) else Qt.PartiallyChecked if n else Qt.Unchecked)
                it.setData(Qt.UserRole, r["id"])
                it.setData(Qt.UserRole + 1, _st(it.checkState()))
                lst.addItem(it)
            flt.textChanged.connect(lambda t, l=lst: [l.item(i).setHidden(t.lower() not in l.item(i).text().lower())
                                                      for i in range(l.count())])
            new = QPushButton("Nouveau…")
            new.clicked.connect(lambda _=False, k=kind, l=lst: self.new_item(k, l))
            box.addWidget(flt)
            box.addWidget(lst, 1)
            box.addWidget(new)
            cols.addLayout(box)
            self.lists[kind] = lst
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(cols, 1)
        lay.addWidget(buttons)

    def new_item(self, kind, lst):
        name, ok = QInputDialog.getText(self, "Nouveau", "Nom :")
        if ok and name.strip():
            iid = P.add_person(self.conn, name) if kind == "person" else P.add_tag(self.conn, name, [name.lower()])
            it = QListWidgetItem(name.strip())
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, iid)
            it.setData(Qt.UserRole + 1, "u")
            lst.addItem(it)

    def save(self):
        for kind, lst in self.lists.items():
            for i in range(lst.count()):
                it = lst.item(i)
                state, before = _st(it.checkState()), it.data(Qt.UserRole + 1)
                if state == before or state == "p":
                    continue
                for v in self.ids:
                    P.set_link(self.conn, kind, v, it.data(Qt.UserRole), state == "c")
        P.rebuild(self.conn)
        self.accept()
