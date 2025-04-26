from PySide6 import QtWidgets

from .MainWindow import MainWindow


def open_gui():
    app = QtWidgets.QApplication([])
    win = MainWindow()
    win.resize(840, 560)
    win.show()
    app.exec()
