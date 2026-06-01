from PySide6 import QtCore, QtWidgets

from src.exclusions import DEFAULT_DEV_PATTERNS, dedupe_strings
from src.i18n import _


class PatternDialog(QtWidgets.QDialog):
    def __init__(self, patterns: list[str], parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._saved_patterns = dedupe_strings([p.strip() for p in patterns if p.strip()])
        self.setWindowTitle(_("Global exclude patterns"))
        self.resize(560, 460)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        hint_layout = QtWidgets.QHBoxLayout()
        hint_layout.setSpacing(16)
        hint_left = QtWidgets.QLabel(_(
            "How to edit:\n"
            "- one pattern per line\n"
            "- delete a line to remove it\n"
            "- empty lines are ignored"
        ))
        hint_right = QtWidgets.QLabel(_(
            "Matching:\n"
            "- names without / match any file or folder name\n"
            "- patterns with / match source-relative paths\n"
            "- examples: Thumbs.db, .venv, node_modules, *.tmp"
        ))
        for hint in (hint_left, hint_right):
            hint.setWordWrap(True)
            hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
            hint_layout.addWidget(hint, 1)
        layout.addLayout(hint_layout)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setPlainText("\n".join(patterns))
        self.editor.setPlaceholderText("Thumbs.db\n.DS_Store\n.venv\nnode_modules\n*.tmp")
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setStyleSheet(
            "QPlainTextEdit {"
            "  padding: 6px;"
            "  font-family: Consolas, 'Cascadia Mono', monospace;"
            "}"
        )
        layout.addWidget(self.editor, 1)

        actions = QtWidgets.QHBoxLayout()
        btn_defaults = QtWidgets.QPushButton(_("Add typical defaults"))
        btn_defaults.clicked.connect(self._add_typical_defaults)
        actions.addWidget(btn_defaults)
        actions.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self._ok_button_base_style = self.btn_ok.styleSheet()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.editor.textChanged.connect(self._update_dirty_state)
        self._update_dirty_state()

    def patterns(self) -> list[str]:
        return dedupe_strings([
            line.strip()
            for line in self.editor.toPlainText().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ])

    def _add_typical_defaults(self) -> None:
        self.editor.setPlainText("\n".join(
            dedupe_strings([*self.patterns(), *DEFAULT_DEV_PATTERNS])
        ))

    def _update_dirty_state(self) -> None:
        dirty = self.patterns() != self._saved_patterns
        if dirty:
            self.btn_ok.setStyleSheet(
                f"{self._ok_button_base_style} "
                "QPushButton { background-color: #b66a00; color: white; font-weight: 700; }"
            )
        else:
            self.btn_ok.setStyleSheet(self._ok_button_base_style)
