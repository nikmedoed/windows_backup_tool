from __future__ import annotations

import gettext
import locale
import os
import pathlib
from typing import Optional

LOCALES_DIR = pathlib.Path(__file__).parent.parent / "locales"
SUPPORTED_LANGUAGES = ("en", "ru")


def _normalize(code: Optional[str]) -> str:
    if not code:
        return "en"
    lowered = code.lower()
    return "ru" if lowered.startswith("ru") else "en"


def _detect() -> str:
    for var in ("BACKUP_TOOL_LANG", "LC_ALL", "LANG"):
        if (v := os.getenv(var)):
            return _normalize(v)
    code = locale.getdefaultlocale()[0] or "en"
    return _normalize(code)


def _load(lang: str) -> gettext.NullTranslations:
    return gettext.translation(
        "app", localedir=LOCALES_DIR, languages=[lang], fallback=True
    )


LANG = _detect()
_trans = _load(LANG)


def _(message: str) -> str:
    return _trans.gettext(message)


def get_language() -> str:
    return LANG


def set_language(lang: str) -> str:
    global LANG, _trans
    normalized = _normalize(lang)
    LANG = normalized
    os.environ["BACKUP_TOOL_LANG"] = normalized
    _trans = _load(normalized)
    return normalized


def install_qt(app):
    if LANG != "ru":
        return
    from PySide6.QtCore import QTranslator, QLibraryInfo
    qt_tr = QTranslator()
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    if qt_tr.load("qtbase_ru", path):
        app.installTranslator(qt_tr)
