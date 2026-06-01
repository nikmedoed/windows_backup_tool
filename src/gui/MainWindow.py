import copy
import threading
import re
from html import escape
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtWidgets import QSizePolicy

from src.app_version import VERSION
from src.config import Settings, PathRule
from src.copier import run_backup
from src.i18n import _, get_language, set_language
from src.restore import apply_restore_plan
from src.scheduler import exists, delete, schedule
from src.utils import human_readable
from src.version_store import VersionStore
from src.zip_snapshot import create_zip_snapshots
from .ExcludeDialog import ExcludeDialog
from .PatternDialog import PatternDialog
from .RestoreDialog import RestoreDialog
from .SizeWorker import SizeWorker


class _TightItemDelegate(QtWidgets.QStyledItemDelegate):
    """Render list items with minimal vertical padding."""
    def sizeHint(self, option, index):
        fm = option.fontMetrics
        return QtCore.QSize(option.rect.width(), fm.height() + 2)


class MainWindow(QtWidgets.QMainWindow):
    EXCLUSION_VALUE_ROLE = QtCore.Qt.UserRole + 11

    progressChanged = QtCore.Signal(int, int)
    logAppended = QtCore.Signal(str)
    backupFinished = QtCore.Signal(bool)
    restoreFinished = QtCore.Signal(bool)
    zipFinished = QtCore.Signal(bool)

    def __init__(self, *, debug: bool = False, debug_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle(_("Backup Tool Settings"))
        self.cfg = Settings.load() or Settings(target_dir="")
        self._debug = debug
        self._debug_path = debug_path
        self._saved_form_state: dict | None = None
        self._loading_fields = False
        self._build_ui()
        self.progressChanged.connect(self._handle_progress)
        self.logAppended.connect(self._append_log)
        self.backupFinished.connect(self._on_backup_finished)
        self.restoreFinished.connect(self._on_restore_finished)
        self.zipFinished.connect(self._on_zip_finished)
        self._append_startup_log()

    def _build_ui(self):
        old_central = self.takeCentralWidget()
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        if old_central is not None:
            old_central.deleteLater()
        self.setWindowTitle(_("Backup Tool Settings"))
        left_column_width = 460
        right_column_width = 352

        def _panel(layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
            panel = QtWidgets.QWidget()
            panel.setLayout(layout)
            panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            return panel

        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(QtWidgets.QLabel(_("Backup target:")))
        self.le_target = QtWidgets.QLineEdit()
        target_layout.addWidget(self.le_target, 1)
        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.clicked.connect(self._pick_target)
        target_layout.addWidget(btn_pick)
        self.btn_lang = QtWidgets.QPushButton(get_language().upper())
        self.btn_lang.setFixedWidth(44)
        self.btn_lang.setToolTip(_("Switch language"))
        self.btn_lang.clicked.connect(self._toggle_language)
        target_layout.addWidget(self.btn_lang)

        def _min_list_height(widget: QtWidgets.QListWidget, rows: float) -> int:
            """Return a pixel height that fits the requested number of rows (with a small hint of the next one)."""
            row_height = widget.sizeHintForRow(0)
            if row_height <= 0:
                row_height = widget.fontMetrics().lineSpacing() + 8
            margins = widget.contentsMargins()
            frame = widget.frameWidth() * 2
            return int(row_height * rows + margins.top() + margins.bottom() + frame)

        self.lst_src = QtWidgets.QListWidget()
        self.lst_src.setItemDelegate(_TightItemDelegate(self.lst_src))
        self.lst_src.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.lst_src.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.lst_src.setViewportMargins(0, 0, 0, 0)
        self.lst_src.setStyleSheet(
            "QListWidget::item { padding: 0px 0px 0px 8px; } "
            "QListWidget::item:selected { padding: 0px 0px 0px 8px; } "
            "QListWidget::indicator { left: 2px; }"
        )
        self.lst_src.setMinimumHeight(_min_list_height(self.lst_src, 3.5))
        self.lst_src.currentRowChanged.connect(self._refresh_excludes)
        src_layout = QtWidgets.QVBoxLayout()
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(6)
        src_header = QtWidgets.QHBoxLayout()
        src_header.setContentsMargins(0, 0, 0, 0)
        src_header.addWidget(QtWidgets.QLabel(_("Sources:")))
        src_header.addStretch(1)
        self.size_label = QtWidgets.QLabel()
        self.size_label.setStyleSheet("color: #8ab4f8;")
        src_header.addWidget(self.size_label)
        src_layout.addLayout(src_header)
        src_layout.addWidget(self.lst_src)
        btn_src_layout = QtWidgets.QHBoxLayout()
        btn_src_layout.setContentsMargins(0, 0, 0, 0)
        btn_src_layout.setSpacing(8)
        for text, handler in [
            (_("+ Add source"), self._add_source),
            (_("– Delete"), self._delete_source),
            (_("Clear"), self._clear_sources),
            (_("Exclusions"), self._edit_excludes),
        ]:
            btn = QtWidgets.QPushButton(text)
            btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            btn.clicked.connect(handler)
            if text == _("Exclusions"):
                btn.setStyleSheet("background-color: #204686; color: white;")
            btn_src_layout.addWidget(btn)
        btn_src_layout.addStretch(1)
        src_layout.addLayout(btn_src_layout)

        self.lbl_excl = QtWidgets.QLabel(_("Exclusions for source:"))
        self.lst_excl = QtWidgets.QListWidget()
        self.lst_excl.setItemDelegate(_TightItemDelegate(self.lst_excl))
        self.lst_excl.setSpacing(0)
        self.lst_excl.setContentsMargins(0, 0, 0, 0)
        self.lst_excl.setViewportMargins(2, 0, 0, 0)
        self.lst_excl.setStyleSheet(
            "QListView { padding: 0px; margin: 0px; } "
            "QListWidget::item { padding: 2px 6px; margin: 0px; } "
            "QListWidget::item:selected { padding: 2px 6px; margin: 0px; }"
        )

        self.lbl_patterns = QtWidgets.QLabel(_("Global exclude patterns:"))
        self.lst_patterns = QtWidgets.QListWidget()
        self.lst_patterns.setItemDelegate(_TightItemDelegate(self.lst_patterns))
        self.lst_patterns.setSpacing(0)
        self.lst_patterns.setContentsMargins(0, 0, 0, 0)
        self.lst_patterns.setViewportMargins(2, 0, 0, 0)
        self.lst_patterns.setStyleSheet(self.lst_excl.styleSheet())
        self.lst_patterns.itemDoubleClicked.connect(lambda _item: self._edit_patterns())

        excl_layout = QtWidgets.QVBoxLayout()
        excl_layout.setContentsMargins(0, 0, 0, 0)
        excl_layout.setSpacing(6)
        excl_layout.addWidget(self.lbl_excl)
        excl_layout.addWidget(self.lst_excl, 2)
        excl_layout.addWidget(self.lbl_patterns)
        excl_layout.addWidget(self.lst_patterns, 1)
        pattern_btn_layout = QtWidgets.QHBoxLayout()
        pattern_btn_layout.setContentsMargins(0, 0, 0, 0)
        pattern_btn_layout.setSpacing(6)
        for text, handler in [
            (_("+ Pattern"), self._add_pattern),
            (_("Edit patterns"), self._edit_patterns),
        ]:
            btn = QtWidgets.QPushButton(text)
            btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            btn.clicked.connect(handler)
            pattern_btn_layout.addWidget(btn)
        pattern_btn_layout.addStretch(1)
        excl_layout.addLayout(pattern_btn_layout)

        schedule_group = QtWidgets.QGroupBox(_("Schedule"))
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)
        schedule_layout.setContentsMargins(6, 2, 6, 6)
        schedule_layout.setSpacing(0)
        self.cb_day = QtWidgets.QCheckBox(_("Daily at 03:00"))
        self.cb_week = QtWidgets.QCheckBox(_("Weekly (Mon at 03:00)"))
        self.cb_logon = QtWidgets.QCheckBox(_("On logon"))
        self.cb_idle = QtWidgets.QCheckBox(_("On idle (20 min)"))
        self.cb_unlock = QtWidgets.QCheckBox(_("On unlock"))
        _cb_style = (
            "QCheckBox { margin: 2px 4px 2px 6px; padding: 0px; } "
            "QCheckBox::indicator { margin: 0px 4px 0px 0px; padding: 0px; }"
        )
        for cb in (self.cb_day, self.cb_week, self.cb_logon, self.cb_idle, self.cb_unlock):
            cb.setStyleSheet(_cb_style)
            schedule_layout.addWidget(cb)
        self.schedule_controls = {
            "daily": self.cb_day,
            "weekly": self.cb_week,
            "onlogon": self.cb_logon,
            "onidle": self.cb_idle,
            "onunlock": self.cb_unlock,
        }

        behavior_group = QtWidgets.QGroupBox(_("Background run"))
        behavior_layout = QtWidgets.QGridLayout(behavior_group)
        behavior_layout.setContentsMargins(6, 2, 6, 6)
        behavior_layout.setHorizontalSpacing(4)
        behavior_layout.setVerticalSpacing(0)
        behavior_layout.setColumnMinimumWidth(0, 26)
        behavior_layout.setColumnStretch(1, 1)
        self.chk_wait = QtWidgets.QCheckBox()
        self.chk_console = QtWidgets.QCheckBox()
        self.chk_tray = QtWidgets.QCheckBox()
        self.chk_overlay = QtWidgets.QCheckBox()
        for row, (cb, text) in enumerate([
            (self.chk_wait, _("Wait before closing console window")),
            (self.chk_console, _("Show console progress")),
            (self.chk_tray, _("Show tray icon while backing up")),
            (self.chk_overlay, _("Show floating bubble when finished")),
        ]):
            label = QtWidgets.QLabel(text)
            label.setWordWrap(True)
            label.setMargin(0)
            label.setContentsMargins(0, 0, 0, 0)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            behavior_layout.addWidget(cb, row, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
            behavior_layout.addWidget(label, row, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
            behavior_layout.setRowMinimumHeight(row, 28)

        history_group = QtWidgets.QGroupBox(_("Version history"))
        history_layout = QtWidgets.QHBoxLayout(history_group)
        history_layout.setContentsMargins(6, 4, 6, 6)
        history_layout.setSpacing(4)
        history_layout.addWidget(QtWidgets.QLabel(_("Keep:")))
        retention_layout = QtWidgets.QHBoxLayout()
        retention_layout.setContentsMargins(0, 0, 0, 0)
        retention_layout.setSpacing(4)
        self.spn_retention = QtWidgets.QSpinBox()
        self.spn_retention.setRange(0, 9999)
        self.spn_retention.setSpecialValueText(_("Unlimited"))
        self.spn_retention.setFixedWidth(144)
        retention_layout.addWidget(self.spn_retention)
        btn_unlimited = QtWidgets.QPushButton("∞")
        btn_unlimited.setToolTip(_("Unlimited"))
        btn_unlimited.setFixedWidth(34)
        btn_unlimited.clicked.connect(lambda _checked=False: self.spn_retention.setValue(0))
        retention_layout.addWidget(btn_unlimited)
        retention_presets = [15, 50, 100, 200, 500]
        for value in retention_presets:
            btn = QtWidgets.QPushButton(str(value))
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda _checked=False, v=value: self.spn_retention.setValue(v))
            retention_layout.addWidget(btn)
        history_layout.addLayout(retention_layout)
        history_layout.addStretch(1)

        options_layout = QtWidgets.QGridLayout()
        options_layout.addWidget(schedule_group, 0, 0)
        options_layout.addWidget(behavior_group, 0, 1)
        options_layout.addWidget(history_group, 1, 0, 1, 2)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setHorizontalSpacing(6)
        options_layout.setVerticalSpacing(4)
        options_layout.setColumnStretch(0, 0)
        options_layout.setColumnStretch(1, 1)
        def _status_label(width: int, alignment: QtCore.Qt.AlignmentFlag) -> QtWidgets.QLabel:
            label = QtWidgets.QLabel()
            label.setStyleSheet("color: #8fd18f;")
            label.setAlignment(alignment | QtCore.Qt.AlignmentFlag.AlignVCenter)
            label.setMaximumWidth(width)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            return label

        self.settings_status_label = _status_label(108, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.backup_status_label = _status_label(48, QtCore.Qt.AlignmentFlag.AlignLeft)

        settings_actions = QtWidgets.QHBoxLayout()
        settings_actions.setContentsMargins(0, 0, 0, 0)
        settings_actions.setSpacing(6)
        settings_actions.addWidget(QtWidgets.QLabel(_("Settings")))
        btn_save = QtWidgets.QPushButton(_("Save settings"))
        self.btn_save = btn_save
        self._save_button_base_style = btn_save.styleSheet()
        btn_save.setMinimumWidth(98)
        btn_save.clicked.connect(self._save)
        btn_reload = QtWidgets.QPushButton(_("Reload saved"))
        btn_reload.setMinimumWidth(98)
        btn_reload.clicked.connect(self._reload_saved_settings)
        btn_exit = QtWidgets.QPushButton(_("Exit"))
        btn_exit.setMinimumWidth(72)
        btn_exit.clicked.connect(self.close)
        settings_actions.addWidget(btn_save)
        settings_actions.addWidget(btn_reload)
        settings_actions.addWidget(self.settings_status_label)
        settings_actions.addStretch(1)
        settings_actions.addWidget(btn_exit)

        backup_actions = QtWidgets.QHBoxLayout()
        backup_actions.setContentsMargins(0, 0, 0, 0)
        backup_actions.setSpacing(3)
        self.btn_restore = QtWidgets.QPushButton(_("Restore version"))
        self.btn_restore.setMinimumWidth(104)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_run = QtWidgets.QPushButton(_("Run backup"))
        self.btn_run.setMinimumWidth(86)
        self.btn_run.clicked.connect(self._run)
        self.btn_zip = QtWidgets.QPushButton(_("Zip"))
        self.btn_zip.setFixedWidth(42)
        self.btn_zip.clicked.connect(self._zip_snapshot)
        self.lbl_last_success_caption = QtWidgets.QLabel(_("Last backup:"))
        self.lbl_last_success_value = QtWidgets.QLabel()
        last_success_width = self.lbl_last_success_value.fontMetrics().horizontalAdvance("0000-00-00 00:00:00") + 4
        self.lbl_last_success_value.setMinimumWidth(last_success_width)
        self.lbl_last_success_value.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        backup_actions.addWidget(self.lbl_last_success_caption)
        backup_actions.addWidget(self.lbl_last_success_value)
        backup_actions.addSpacing(0)
        backup_actions.addWidget(self.btn_restore)
        backup_actions.addWidget(self.btn_run)
        backup_actions.addWidget(self.btn_zip)
        backup_actions.addWidget(self.backup_status_label)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        self.txt_log = QtWidgets.QTextBrowser()
        self.txt_log.setReadOnly(True)
        self.txt_log.setOpenExternalLinks(True)
        self.txt_log.setMinimumHeight(130)
        self.txt_log.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
        self.txt_log.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.txt_log.setUndoRedoEnabled(False)
        self.txt_log.setStyleSheet(
            "QTextEdit, QTextBrowser {"
            "  background: #111317;"
            "  color: #d7dce5;"
            "  border: 1px solid #2b3038;"
            "  padding: 6px;"
            "  font-family: Consolas, 'Cascadia Mono', monospace;"
            "  font-size: 9pt;"
            "}"
        )

        self.lst_src.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.lst_excl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.txt_log.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        left_panel = QtWidgets.QWidget()
        left_panel.setFixedWidth(left_column_width)
        left_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(_panel(src_layout), 1)
        left_layout.addWidget(_panel(options_layout))
        left_layout.addWidget(_panel(settings_actions))
        left_layout.addWidget(_panel(backup_actions))
        left_layout.addWidget(self.progress_bar)
        left_layout.addWidget(self.txt_log, 3)

        right_panel = _panel(excl_layout)
        right_panel.setFixedWidth(right_column_width)
        right_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        body_layout = QtWidgets.QHBoxLayout()
        body_layout.addWidget(left_panel)
        body_layout.addWidget(right_panel)

        root_layout = QtWidgets.QVBoxLayout(cw)
        root_layout.addLayout(target_layout)
        root_layout.addLayout(body_layout, 1)

        self._load_fields(mark_clean=self._saved_form_state is None)
        self._connect_dirty_signals()

    def _connect_dirty_signals(self) -> None:
        self.le_target.textChanged.connect(self._update_dirty_state)
        self.spn_retention.valueChanged.connect(self._update_dirty_state)
        for cb in (
                *self.schedule_controls.values(),
                self.chk_wait,
                self.chk_console,
                self.chk_tray,
                self.chk_overlay,
        ):
            cb.toggled.connect(self._update_dirty_state)

    def _capture_form_state(self) -> dict:
        state = {
            "target_dir": self.le_target.text(),
            "schedule": {key: cb.isChecked() for key, cb in self.schedule_controls.items()},
            "wait_on_finish": self.chk_wait.isChecked(),
            "show_console": self.chk_console.isChecked(),
            "show_tray_icon": self.chk_tray.isChecked(),
            "show_overlay": self.chk_overlay.isChecked(),
            "retention": self.spn_retention.value(),
            "current_source_row": self.lst_src.currentRow(),
            "log_html": self.txt_log.toHtml(),
        }
        return state

    def _restore_form_state(self, state: dict) -> None:
        self._loading_fields = True
        self.le_target.setText(state["target_dir"])
        for key, checked in state["schedule"].items():
            if key in self.schedule_controls:
                self.schedule_controls[key].setChecked(checked)
        self.chk_wait.setChecked(state["wait_on_finish"])
        self.chk_console.setChecked(state["show_console"])
        self.chk_tray.setChecked(state["show_tray_icon"])
        self.chk_overlay.setChecked(state["show_overlay"])
        self.spn_retention.setValue(state["retention"])
        self.lst_src.setCurrentRow(state["current_source_row"])
        if state["log_html"]:
            self.txt_log.setHtml(state["log_html"])
        self._loading_fields = False
        self._update_dirty_state()

    def _toggle_language(self) -> None:
        state = self._capture_form_state()
        next_lang = "ru" if get_language() == "en" else "en"
        set_language(next_lang)
        self._build_ui()
        self._restore_form_state(state)

    def _load_fields(self, *, mark_clean: bool = False):
        self._loading_fields = True
        self.le_target.setText(self.cfg.target_dir)
        self.lst_src.clear()
        for rule in self.cfg.sources:
            self.lst_src.addItem(rule.source)
        self.lst_src.setCurrentRow(0 if self.cfg.sources else -1)
        self._refresh_excludes()
        self._refresh_patterns()
        for key, cb in self.schedule_controls.items():
            cb.setChecked(exists(key))
        self.chk_wait.setChecked(self.cfg.wait_on_finish)
        self.chk_console.setChecked(self.cfg.show_console)
        self.chk_tray.setChecked(self.cfg.show_tray_icon)
        self.chk_overlay.setChecked(self.cfg.show_overlay)
        self.spn_retention.setValue(self.cfg.retention_keep_successful_runs)
        self._update_last_success_label()
        self._update_backup_size()
        self._loading_fields = False
        if mark_clean:
            self._mark_form_clean()
        else:
            self._update_dirty_state()

    def _settings_state(self) -> dict:
        return {
            "target_dir": self.le_target.text().strip(),
            "sources": [
                {
                    "source": rule.source,
                    "excludes": list(rule.excludes),
                }
                for rule in self.cfg.sources
            ],
            "exclude_patterns": list(self.cfg.exclude_patterns),
            "schedule": {key: cb.isChecked() for key, cb in self.schedule_controls.items()},
            "wait_on_finish": self.chk_wait.isChecked(),
            "show_console": self.chk_console.isChecked(),
            "show_tray_icon": self.chk_tray.isChecked(),
            "show_overlay": self.chk_overlay.isChecked(),
            "retention": self.spn_retention.value(),
        }

    def _mark_form_clean(self) -> None:
        self._saved_form_state = copy.deepcopy(self._settings_state())
        self._update_dirty_state()

    def _update_dirty_state(self) -> None:
        if self._loading_fields or not hasattr(self, "btn_save"):
            return
        dirty = self._saved_form_state is not None and self._settings_state() != self._saved_form_state
        if dirty:
            self.btn_save.setStyleSheet(
                f"{self._save_button_base_style} "
                "QPushButton { background-color: #b66a00; color: white; font-weight: 700; }"
            )
        else:
            self.btn_save.setStyleSheet(self._save_button_base_style)

    def _pick_target(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, _("Select target directory"))
        if directory:
            self.le_target.setText(directory)
            self._update_dirty_state()
            self._update_backup_size()

    def _add_source(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, _("Add source directory"))
        if directory:
            self.cfg.sources.append(PathRule(source=directory))
            self._load_fields()

    def _delete_source(self):
        row = self.lst_src.currentRow()
        if row >= 0:
            self.cfg.sources.pop(row)
            self._load_fields()

    def _clear_sources(self):
        self.cfg.sources.clear()
        self._load_fields()

    def _refresh_excludes(self):
        self.lst_excl.clear()
        row = self.lst_src.currentRow()
        if row < 0:
            self.lbl_excl.setText(_("Exclusions for source:"))
            return
        source = self.cfg.sources[row].source
        self.lbl_excl.setText(_("Exclusions for: {source}").format(source=source))
        for excl in self.cfg.sources[row].excludes:
            item = QtWidgets.QListWidgetItem(excl)
            item.setData(self.EXCLUSION_VALUE_ROLE, excl)
            self.lst_excl.addItem(item)

    def _refresh_patterns(self):
        self.lst_patterns.clear()
        for pattern in self.cfg.exclude_patterns:
            item = QtWidgets.QListWidgetItem(pattern)
            item.setData(self.EXCLUSION_VALUE_ROLE, pattern)
            self.lst_patterns.addItem(item)

    def _edit_excludes(self):
        dialog = ExcludeDialog(self.cfg, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            new_excls = dialog.get_excludes()
            for rule in self.cfg.sources:
                rule.excludes = new_excls.get(rule.source, [])
            self._refresh_excludes()
            self._update_dirty_state()
            self._update_backup_size()

    def _add_pattern(self) -> None:
        pattern, ok = QtWidgets.QInputDialog.getText(
            self,
            _("Add pattern"),
            _("Glob pattern:"),
        )
        pattern = pattern.strip()
        if not ok or not pattern:
            return
        if pattern.casefold() not in {p.casefold() for p in self.cfg.exclude_patterns}:
            self.cfg.exclude_patterns.append(pattern)
        self._refresh_patterns()
        self._update_dirty_state()
        self._update_backup_size()

    def _edit_patterns(self) -> None:
        dialog = PatternDialog(self.cfg.exclude_patterns, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self.cfg.exclude_patterns = dialog.patterns()
        self._refresh_patterns()
        self._update_dirty_state()
        self._update_backup_size()

    def _save(self):
        target = self.le_target.text().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please specify the target directory"))
            return
        self._apply_form_to_config()
        self.cfg.save()
        target_settings_error = None
        try:
            self.cfg.save_to_target(target)
        except Exception as exc:
            target_settings_error = exc
        for key, cb in self.schedule_controls.items():
            if cb.isChecked():
                if not exists(key):
                    schedule(key)
            else:
                if exists(key):
                    delete(key)

        self.settings_status_label.setText(_("Saved") if target_settings_error is None else _("Saved locally"))
        self._mark_form_clean()
        if target_settings_error is not None:
            QtWidgets.QMessageBox.warning(
                self,
                _("Settings"),
                _("Saved local settings, but could not write settings to backup target: {exc}").format(
                    exc=target_settings_error,
                ),
            )
        self._update_backup_size()

    def _apply_form_to_config(self) -> None:
        self.cfg.target_dir = self.le_target.text().strip()
        self.cfg.wait_on_finish = self.chk_wait.isChecked()
        self.cfg.show_console = self.chk_console.isChecked()
        self.cfg.show_tray_icon = self.chk_tray.isChecked()
        self.cfg.show_overlay = self.chk_overlay.isChecked()
        self.cfg.retention_keep_successful_runs = self.spn_retention.value()

    def _reload_saved_settings(self):
        loaded = Settings.load()
        if not loaded:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("No settings found"))
            return
        self.cfg = loaded
        self._load_fields(mark_clean=True)
        self.settings_status_label.setText(_("Loaded"))

    def _restore(self):
        target = self.le_target.text().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please specify the target directory"))
            return
        self._apply_form_to_config()

        store = VersionStore(Path(target))
        try:
            runs = store.list_successful_runs()
        finally:
            store.close()
        if not runs:
            QtWidgets.QMessageBox.information(self, _("Restore"), _("No backup versions found"))
            return

        try:
            restore_cfg = Settings.load_from_target(target) or copy.deepcopy(self.cfg)
        except RuntimeError as exc:
            QtWidgets.QMessageBox.warning(self, _("Restore"), str(exc))
            return
        restore_cfg.target_dir = target

        dialog = RestoreDialog(restore_cfg, Path(target), runs, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        request = dialog.request()
        if request is None:
            return
        plan = request.plan

        if not plan.actions:
            QtWidgets.QMessageBox.information(self, _("Restore"), _("Nothing to restore for the selected version"))
            return

        if plan.mode == "original":
            message = _("Apply selected restore changes? Create: {create}, replace: {replace}, delete: {delete}.").format(
                create=plan.create_count,
                replace=plan.replace_count,
                delete=plan.delete_count,
            )
            if QtWidgets.QMessageBox.question(
                    self,
                    _("Confirm restore"),
                    message,
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        else:
            message = _("Export selected full-version files to the output folder? Files: {count}.").format(
                count=plan.export_count,
            )
            if QtWidgets.QMessageBox.question(
                    self,
                    _("Confirm restore"),
                    message,
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        self._reset_log()
        self.progress_bar.setValue(0)
        self.backup_status_label.setText("...")
        self.btn_run.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.btn_zip.setEnabled(False)

        def _job():
            worker_store = VersionStore(Path(target))
            try:
                success = apply_restore_plan(
                    plan,
                    worker_store,
                    progress_cb=self.progressChanged.emit,
                    log_cb=self.logAppended.emit,
                )
            finally:
                worker_store.close()
            self.restoreFinished.emit(success)

        threading.Thread(target=_job, daemon=True).start()

    def _update_backup_size(self):
        self.size_label.setText(_("Size: calculating…"))
        if hasattr(self, '_size_worker') and self._size_worker.isRunning():
            return
        self._size_worker = SizeWorker(self.cfg.sources, self.cfg.exclude_patterns)
        self._size_worker.sizeCalculated.connect(self._on_size_calculated)
        self._size_worker.start()

    def _on_size_calculated(self, size: int):
        self.size_label.setText(
            _("Size: {size}").format(size=human_readable(size))
        )

    def _run(self):
        target = self.le_target.text().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please specify the target directory"))
            return
        if not self.cfg.sources:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please add at least one source directory"))
            return
        self._apply_form_to_config()
        cfg = copy.deepcopy(self.cfg)
        self._reset_log()
        self.progress_bar.setValue(0)
        self.backup_status_label.setText("...")
        self.btn_run.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.btn_zip.setEnabled(False)
        def _job():
            success = run_backup(
                cfg,
                self.progressChanged.emit,
                self.logAppended.emit,
                debug=self._debug,
                debug_path=self._debug_path if isinstance(self._debug_path, str) else None,
            )
            self.backupFinished.emit(success)
        threading.Thread(target=_job, daemon=True).start()

    def _zip_snapshot(self):
        target = self.le_target.text().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please specify the target directory"))
            return
        if not self.cfg.sources:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please add at least one source directory"))
            return
        self._apply_form_to_config()
        cfg = copy.deepcopy(self.cfg)
        self._reset_log()
        self.progress_bar.setValue(0)
        self.backup_status_label.setText("...")
        self.btn_run.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.btn_zip.setEnabled(False)

        def _job():
            success = False
            try:
                result = create_zip_snapshots(
                    cfg,
                    progress_cb=self.progressChanged.emit,
                    log_cb=self.logAppended.emit,
                )
                success = not result.errors
            except Exception as exc:
                self.logAppended.emit(_("Zip snapshot failed: {exc}").format(exc=exc))
            finally:
                self.zipFinished.emit(success)
        threading.Thread(target=_job, daemon=True).start()

    def _handle_progress(self, i: int, tot: int):
        if tot <= 0:
            self.progress_bar.setRange(0, 0)
            return
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(i / tot * 100))

    def _append_log(self, message: str):
        cursor = self.txt_log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if self.txt_log.document().blockCount() > 1 or self.txt_log.toPlainText():
            cursor.insertBlock()
        cursor.insertHtml(_format_log_entry(message))
        self.txt_log.setTextCursor(cursor)
        self.txt_log.ensureCursorVisible()

    def _append_startup_log(self):
        version_line = _("Backup Tool version: {version}").format(version=VERSION)
        author_line = (
            _("Author: Muromtsev Nikita. Other useful utilities and support the author: {url}")
            .format(url="https://nikmedoed.com/")
        )
        self._append_log(f"{version_line}\n{author_line}")

    def _reset_log(self):
        self.txt_log.clear()
        self._append_startup_log()

    def _update_last_success_label(self):
        if self.cfg.last_success:
            try:
                dt = datetime.fromisoformat(self.cfg.last_success)
                pretty = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pretty = self.cfg.last_success
            self.lbl_last_success_value.setText(pretty)
        else:
            self.lbl_last_success_value.setText(_("never"))

    def _on_backup_finished(self, success: bool):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.btn_run.setEnabled(True)
        self.btn_restore.setEnabled(True)
        self.btn_zip.setEnabled(True)
        self.backup_status_label.setText(_("Done") if success else _("Error"))
        self._update_last_success_label()
        self._update_backup_size()

    def _on_restore_finished(self, success: bool):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.btn_run.setEnabled(True)
        self.btn_restore.setEnabled(True)
        self.btn_zip.setEnabled(True)
        self.backup_status_label.setText(_("Done") if success else _("Error"))

    def _on_zip_finished(self, success: bool):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.btn_run.setEnabled(True)
        self.btn_restore.setEnabled(True)
        self.btn_zip.setEnabled(True)
        self.backup_status_label.setText(_("Done") if success else _("Error"))
def _format_log_entry(message: str) -> str:
    level = _log_level(message)
    label, color, background, border = _log_style(level)
    timestamp = datetime.now().strftime("%H:%M:%S")
    body = _format_log_body(message.strip())
    return (
        f"<div style='margin:3px 0 5px 0; padding:5px 7px; "
        f"border-left:3px solid {border}; background:{background};'>"
        f"<div><span style='color:#7f8792;'>{timestamp}</span> "
        f"<span style='color:{color}; font-weight:700;'>{label}</span></div>"
        f"<div style='margin-top:3px; color:#d7dce5;'>{body}</div>"
        f"</div>"
    )


def _log_level(message: str) -> str:
    text = message.strip()
    lowered = text.casefold()
    summary_errors = _summary_error_count(text)
    if summary_errors is not None:
        return "error" if summary_errors else "success"
    if text.startswith(("⚠", "⌛")) or "warning" in lowered:
        return "warning"
    if text.startswith(("❌", "❗")) or "error" in lowered or "ошиб" in lowered:
        return "error"
    if text.startswith(("✅", "🧹")) or "done" in lowered:
        return "success"
    if text.startswith(("▶", "🔍", "📂", "🛠", "📁", "🐞", "📋", "📚", "🧾")):
        return "work"
    return "info"


def _summary_error_count(message: str) -> Optional[int]:
    match = re.search(r"(?:errors|ошибок)\s*:\s*(\d+)", message, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _log_style(level: str) -> tuple[str, str, str, str]:
    styles = {
        "error": ("ERR", "#ff8f8f", "#24191b", "#d85d5d"),
        "warning": ("WARN", "#ffd37a", "#241f16", "#d39b32"),
        "success": ("OK", "#8fd18f", "#172118", "#5faa62"),
        "work": ("RUN", "#8ab4f8", "#151c28", "#4e7fbd"),
        "info": ("INFO", "#b7c0cc", "#171a20", "#48505c"),
    }
    return styles.get(level, styles["info"])


def _format_log_body(message: str) -> str:
    if not message:
        return ""
    if "https://nikmedoed.com/" in message:
        prefix, url, suffix = message.partition("https://nikmedoed.com/")
        href = escape(url, quote=True)
        return (
            f"{_log_text(prefix)}"
            f"<a href='{href}' style='color:#8ab4f8; text-decoration:underline;'>{_log_text(url)}</a>"
            f"{_log_text(suffix)}"
        )
    if "|" in message and ":" in message:
        parts = [part.strip() for part in message.split("|") if part.strip()]
        return " ".join(_log_chip(part) for part in parts)
    if message.startswith("Safety copy ") and " -> " in message:
        source, target = message[len("Safety copy "):].split(" -> ", 1)
        return (
            f"{_log_text('Safety copy')}"
            f"<div style='margin-top:3px; color:#9ca6b3;'>from {_log_path(source)}</div>"
            f"<div style='margin-top:2px; color:#9ca6b3;'>to&nbsp;&nbsp; {_log_path(target)}</div>"
        )
    if message.startswith("Restore error for "):
        rest = message[len("Restore error for "):]
        path, sep, error = rest.partition(": ")
        if sep:
            return (
                f"{_log_text('Restore error')}"
                f"<div style='margin-top:3px;'>{_log_path(path)}</div>"
                f"<div>{_log_text(error)}</div>"
            )
    return _log_text(message)


def _log_chip(text: str) -> str:
    name, sep, value = text.partition(":")
    if not sep:
        return _log_text(text)
    return (
        "<span style='display:inline-block; margin:1px 4px 1px 0; padding:1px 5px; "
        "border:1px solid #343b46; background:#1d222b; color:#d7dce5;'>"
        f"<span style='color:#8d98a7;'>{_log_text(name.strip())}</span>"
        f"<span style='color:#f0f3f7;'> {_log_text(value.strip())}</span>"
        "</span>"
    )


def _log_text(text: str) -> str:
    return _soft_break_paths(escape(text)).replace("\n", "<br>")


def _log_path(text: str) -> str:
    return f"<span style='color:#d7dce5;'>{_soft_break_paths(escape(text))}</span>"


def _soft_break_paths(text: str) -> str:
    return (
        text
        .replace("\\", "\\&#8203;")
        .replace("/", "/&#8203;")
        .replace("_", "_&#8203;")
        .replace(" → ", " <span style='color:#7f8792;'>→</span> ")
    )
