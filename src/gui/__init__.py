from pathlib import Path

from PySide6 import QtWidgets, QtGui

from typing import Optional

from src.i18n import install_qt
from .MainWindow import MainWindow


def open_gui(*, debug: bool = False, debug_path: Optional[str] = None):
    app = QtWidgets.QApplication([])
    install_qt(app)
    win = MainWindow(debug=debug, debug_path=debug_path)
    win.resize(840, 560)

    icon_path = Path(__file__).parent.parent.parent / "icon" / "icon.png"
    if icon_path.exists():
        win.setWindowIcon(QtGui.QIcon(str(icon_path)))

    win.show()
    app.exec()
