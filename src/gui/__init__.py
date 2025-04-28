from PySide6 import QtWidgets

from src.i18n import install_qt
from .MainWindow import MainWindow


def open_gui():
    app = QtWidgets.QApplication([])
    install_qt(app)
    win = MainWindow()
    win.resize(840, 560)
    win.show()
    app.exec()
