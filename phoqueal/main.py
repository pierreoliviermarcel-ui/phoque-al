"""Point d'entree de Phoque Al."""
import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Phoque Al")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
