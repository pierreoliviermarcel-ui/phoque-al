"""Fenetre « Doublons » : analyse, selection, mise a la corbeille Phoque Al."""
import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import dupes
from .dupes import DupeScanner, human


class DuplicatesDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.setWindowTitle("Doublons")
        self.resize(1100, 680)
        self.worker = None

        intro = QLabel("Les fichiers cochés seront <b>déplacés dans la corbeille Phoque Al</b> de leur disque "
                       "(dossier _Corbeille_Focal), pas effacés. Il reste toujours au moins une copie de chaque fichier.")
        intro.setWordWrap(True)
        self.scan_btn = QPushButton("Lancer l'analyse")
        self.scan_btn.setObjectName("heroPrimary")
        self.scan_btn.clicked.connect(lambda: self.start())
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.state = QLabel()
        self.state.setObjectName("status")
        top = QHBoxLayout()
        top.addWidget(self.scan_btn)
        top.addWidget(self.bar, 1)
        top.addWidget(self.state, 2)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Fichier", "Disque", "Taille", "Modifié le", "Vues"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self.on_check)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.play(it))

        self.preselect_btn = QPushButton("Présélectionner (garder 1 copie par groupe)")
        self.preselect_btn.clicked.connect(lambda: self.preselect())
        clear = QPushButton("Tout décocher")
        clear.clicked.connect(lambda: self.set_all(False))
        show = QPushButton("Ouvrir l'emplacement")
        show.clicked.connect(lambda: self.reveal())
        self.verify = QCheckBox("Vérifier le fichier entier avant de déplacer (plus lent, plus sûr)")
        self.delete_btn = QPushButton("Mettre les fichiers cochés à la corbeille")
        self.delete_btn.clicked.connect(lambda: self.delete())
        empty = QPushButton("Vider la corbeille Phoque Al…")
        empty.clicked.connect(lambda: self.empty_trash())
        self.summary = QLabel()
        bottom = QHBoxLayout()
        for w in (self.preselect_btn, clear, show):
            bottom.addWidget(w)
        bottom.addStretch(1)
        bottom.addWidget(empty)
        act = QHBoxLayout()
        act.addWidget(self.verify)
        act.addStretch(1)
        act.addWidget(self.summary)
        act.addWidget(self.delete_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(top)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)
        lay.addLayout(act)
        hint = QLabel("Double-clic sur un fichier pour le lire et comparer.")
        hint.setObjectName("status")
        lay.addWidget(hint)
        self.update_summary()

    # --- analyse --------------------------------------------------------------------
    def start(self):
        if self.worker and self.worker.isRunning():
            return
        self.tree.clear()
        self.scan_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.state.setText("Comparaison des fichiers de même taille…")
        self.worker = DupeScanner()
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_groups.connect(self.show_results)
        self.worker.start()

    def on_progress(self, i, total, title):
        self.bar.setRange(0, total)
        self.bar.setValue(i)
        self.state.setText("%d / %d fichiers comparés" % (i, total))

    def show_results(self, exact, probable):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Relancer l'analyse")
        self.bar.setVisible(False)
        gain = sum(g[0]["size"] * (len(g) - 1) for g in exact)
        self.state.setText("%d groupe(s) de doublons exacts (%s récupérables), %d groupe(s) probables"
                           % (len(exact), human(gain), len(probable)))
        self.tree.blockSignals(True)
        if exact:
            head = QTreeWidgetItem(["Doublons exacts — fichiers identiques"])
            self._bold(head)
            self.tree.addTopLevelItem(head)
            for g in exact:
                self._group(head, g, "%d copies  ·  %s chacune  ·  %s récupérables" % (
                    len(g), human(g[0]["size"]), human(g[0]["size"] * (len(g) - 1))), exact=True)
            head.setExpanded(True)
        if probable:
            head = QTreeWidgetItem(["Doublons probables — même durée, fichiers différents (à vérifier)"])
            self._bold(head)
            self.tree.addTopLevelItem(head)
            for g in probable:
                self._group(head, g, "%d fichiers de même durée" % len(g), exact=False)
            head.setExpanded(True)
        if not exact and not probable:
            self.tree.addTopLevelItem(QTreeWidgetItem(["Aucun doublon trouvé."]))
        self.tree.blockSignals(False)
        if exact:
            self.preselect()
        self.update_summary()

    def _bold(self, item):
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)

    def _group(self, parent, entries, label, exact):
        g = QTreeWidgetItem([label])
        g.setData(0, Qt.UserRole, "exact" if exact else "probable")
        parent.addChild(g)
        for e in sorted(entries, key=lambda e: e["mtime"] or 0):
            it = QTreeWidgetItem([e["path"], e["label"] or e["serial"], human(e["size"]),
                                  time.strftime("%d/%m/%Y", time.localtime(e["mtime"] or 0)),
                                  str(e["play_count"] or 0)])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked)
            it.setData(0, Qt.UserRole, e)
            g.addChild(it)
        g.setExpanded(True)

    # --- selection ------------------------------------------------------------------------
    def groups(self, kind=None):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            for j in range(head.childCount()):
                g = head.child(j)
                if kind is None or g.data(0, Qt.UserRole) == kind:
                    out.append(g)
        return out

    def preselect(self):
        """Garde la copie la plus regardee (sinon la plus ancienne) de chaque doublon exact."""
        self.tree.blockSignals(True)
        for g in self.groups("exact"):
            kids = [g.child(k) for k in range(g.childCount())]
            keep = max(kids, key=lambda it: (it.data(0, Qt.UserRole)["play_count"] or 0,
                                             -(it.data(0, Qt.UserRole)["mtime"] or 0)))
            for it in kids:
                it.setCheckState(0, Qt.Unchecked if it is keep else Qt.Checked)
        self.tree.blockSignals(False)
        self.update_summary()

    def set_all(self, on):
        self.tree.blockSignals(True)
        for g in self.groups():
            for k in range(g.childCount()):
                g.child(k).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self.update_summary()

    def on_check(self, item, col):
        g = item.parent()
        if g is None or item.data(0, Qt.UserRole) is None or not isinstance(item.data(0, Qt.UserRole), dict):
            return
        kids = [g.child(k) for k in range(g.childCount())]
        if all(k.checkState(0) == Qt.Checked for k in kids):
            self.tree.blockSignals(True)
            item.setCheckState(0, Qt.Unchecked)
            self.tree.blockSignals(False)
            self.state.setText("Impossible de cocher toutes les copies : il doit en rester au moins une.")
        self.update_summary()

    def checked(self):
        out = []
        for g in self.groups():
            for k in range(g.childCount()):
                it = g.child(k)
                if it.checkState(0) == Qt.Checked:
                    out.append((g, it))
        return out

    def update_summary(self):
        sel = self.checked()
        size = sum(it.data(0, Qt.UserRole)["size"] for _, it in sel)
        self.summary.setText("%d fichier(s) coché(s) · %s" % (len(sel), human(size)))
        self.delete_btn.setEnabled(bool(sel))

    # --- actions -----------------------------------------------------------------------------
    def current_entry(self):
        it = self.tree.currentItem()
        e = it.data(0, Qt.UserRole) if it else None
        return e if isinstance(e, dict) else None

    def reveal(self):
        e = self.current_entry()
        if e and os.path.exists(e["path"]):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(e["path"])])

    def play(self, item):
        e = item.data(0, Qt.UserRole)
        if isinstance(e, dict):
            self.main.ctrl.play(e["id"], self.main.ask_screen())

    def delete(self):
        sel = self.checked()
        if not sel:
            return
        size = sum(it.data(0, Qt.UserRole)["size"] for _, it in sel)
        probable = sum(1 for g, _ in sel if g.data(0, Qt.UserRole) == "probable")
        msg = "Déplacer %d fichier(s) (%s) dans la corbeille Phoque Al ?" % (len(sel), human(size))
        if probable:
            msg += ("\n\nAttention : %d fichier(s) viennent des doublons PROBABLES (même durée, contenu "
                    "différent). Vérifie-les en les regardant avant." % probable)
        if QMessageBox.question(self, "Doublons", msg) != QMessageBox.Yes:
            return
        self.main.ctrl.close_all()     # libere les fichiers eventuellement ouverts
        done, errors = 0, []
        verify = self.verify.isChecked()
        for g, it in sel:
            e = it.data(0, Qt.UserRole)
            try:
                if verify and g.data(0, Qt.UserRole) == "exact":
                    keep = [g.child(k).data(0, Qt.UserRole) for k in range(g.childCount())
                            if g.child(k).checkState(0) != Qt.Checked]
                    self.state.setText("Vérification complète de %s…" % os.path.basename(e["path"]))
                    self.repaint()
                    if not keep or dupes.full_hash(e["path"]) != dupes.full_hash(keep[0]["path"]):
                        errors.append("%s : contenu différent à la vérification, non déplacé" % e["path"])
                        continue
                dupes.move_to_trash(e["path"])
                dupes.forget_entry(self.conn, e["id"])
                it.setDisabled(True)
                it.setText(0, "✓ déplacé  —  " + e["path"])
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                it.setData(0, Qt.CheckStateRole, None)
                done += 1
            except OSError as ex:
                errors.append("%s : %s" % (e["path"], ex))
        self.update_summary()
        text = "%d fichier(s) déplacé(s) dans la corbeille Phoque Al." % done
        if errors:
            text += "\n\nProblèmes :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Doublons", text)
        self.main.show_home()

    def empty_trash(self):
        found = dupes.trash_folders()
        if not found:
            QMessageBox.information(self, "Corbeille Phoque Al", "La corbeille Phoque Al est vide sur les disques branchés.")
            return
        lines = ["%s : %d fichier(s), %s" % (p, n, human(s)) for p, s, n in found]
        if QMessageBox.warning(self, "Vider la corbeille Phoque Al",
                               "Supprimer DÉFINITIVEMENT :\n\n" + "\n".join(lines) +
                               "\n\nCette action est irréversible.",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        import shutil
        for p, _, _ in found:
            shutil.rmtree(p, ignore_errors=True)
        QMessageBox.information(self, "Corbeille Phoque Al", "Corbeille vidée.")

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        super().closeEvent(e)
