from __future__ import annotations

import gettext
import locale
import os
import pathlib

LOCALES_DIR = pathlib.Path(__file__).parent.parent / "locales"


def _detect() -> str:
    for var in ("LC_ALL", "LANG"):
        if (v := os.getenv(var)):
            return "ru" if v.lower().startswith("ru") else "en"
    code = locale.getdefaultlocale()[0] or "en"
    return "ru" if code.lower().startswith("ru") else "en"


LANG = _detect()
_trans = gettext.translation(
    "app", localedir=LOCALES_DIR, languages=[LANG], fallback=True
)
_ = _trans.gettext


def install_qt(app):
    if LANG != "ru":
        return
    from PySide6.QtCore import QTranslator, QLibraryInfo
    qt_tr = QTranslator()
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    if qt_tr.load("qtbase_ru", path):
        app.installTranslator(qt_tr)
