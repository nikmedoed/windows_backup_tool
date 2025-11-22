import threading
from datetime import datetime

from PySide6 import QtWidgets, QtCore
from PySide6.QtWidgets import QSizePolicy

from src.config import Settings, PathRule
from src.copier import run_backup
from src.i18n import _
from src.scheduler import exists, delete, schedule
from src.utils import human_readable
from .ExcludeDialog import ExcludeDialog
from .SizeWorker import SizeWorker


class MainWindow(QtWidgets.QMainWindow):
    progressChanged = QtCore.Signal(int, int)
    logAppended = QtCore.Signal(str)
    backupFinished = QtCore.Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(_("Backup Tool Settings"))
        self.cfg = Settings.load() or Settings(target_dir="")
        self._build_ui()
        self.progressChanged.connect(self._handle_progress)
        self.logAppended.connect(self.txt_log.append)
        self.backupFinished.connect(self._on_backup_finished)

    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)

        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(QtWidgets.QLabel(_("Backup target:")))
        self.le_target = QtWidgets.QLineEdit()
        target_layout.addWidget(self.le_target, 1)
        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.clicked.connect(self._pick_target)
        target_layout.addWidget(btn_pick)

        self.lst_src = QtWidgets.QListWidget()
        self.lst_src.currentRowChanged.connect(self._refresh_excludes)
        src_layout = QtWidgets.QVBoxLayout()
        src_layout.addWidget(QtWidgets.QLabel(_("Sources:")))
        src_layout.addWidget(self.lst_src)
        btn_src_layout = QtWidgets.QHBoxLayout()
        for text, handler in [
            (_("+ Add source"), self._add_source),
            (_("– Delete"), self._delete_source),
            (_("Clear"), self._clear_sources),
            (_("Exclusions"), self._edit_excludes),
        ]:
            btn = QtWidgets.QPushButton(text)
            btn.clicked.connect(handler)
            if text == _("Exclusions"):
                btn.setStyleSheet("background-color: #204686; color: white;")
            btn_src_layout.addWidget(btn)
        src_layout.addLayout(btn_src_layout)

        self.lbl_excl = QtWidgets.QLabel(_("Exclusions for source:"))
        self.lst_excl = QtWidgets.QListWidget()
        excl_layout = QtWidgets.QVBoxLayout()
        excl_layout.addWidget(self.lbl_excl)
        excl_layout.addWidget(self.lst_excl)

        schedule_group = QtWidgets.QGroupBox(_("Schedule"))
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)
        self.cb_day = QtWidgets.QCheckBox(_("Daily at 03:00"))
        self.cb_week = QtWidgets.QCheckBox(_("Weekly (Mon at 03:00)"))
        self.cb_logon = QtWidgets.QCheckBox(_("On logon"))
        self.cb_idle = QtWidgets.QCheckBox(_("On idle (20 min)"))
        self.cb_unlock = QtWidgets.QCheckBox(_("On unlock"))
        for cb in (self.cb_day, self.cb_week, self.cb_logon, self.cb_idle, self.cb_unlock):
            schedule_layout.addWidget(cb)
        self.schedule_controls = {
            "daily": self.cb_day,
            "weekly": self.cb_week,
            "onlogon": self.cb_logon,
            "onidle": self.cb_idle,
            "onunlock": self.cb_unlock,
        }

        behavior_group = QtWidgets.QGroupBox(_("Background run"))
        behavior_layout = QtWidgets.QVBoxLayout(behavior_group)
        self.chk_wait = QtWidgets.QCheckBox(_("Wait before closing console window"))
        self.chk_console = QtWidgets.QCheckBox(_("Show console progress"))
        self.chk_tray = QtWidgets.QCheckBox(_("Show tray icon while backing up"))
        self.chk_overlay = QtWidgets.QCheckBox(_("Show floating bubble when finished"))
        behavior_layout.addWidget(self.chk_wait)
        behavior_layout.addWidget(self.chk_console)
        behavior_layout.addWidget(self.chk_tray)
        behavior_layout.addWidget(self.chk_overlay)
        self.lbl_last_success = QtWidgets.QLabel()

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: green;")

        action_layout = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton(_("Save"))
        btn_save.clicked.connect(self._save)
        btn_restore = QtWidgets.QPushButton(_("Restore"))
        btn_restore.clicked.connect(self._restore)
        self.btn_run = QtWidgets.QPushButton(_("Run backup"))
        self.btn_run.clicked.connect(self._run)
        btn_exit = QtWidgets.QPushButton(_("Exit"))
        btn_exit.clicked.connect(self.close)
        action_layout.addWidget(btn_save)
        action_layout.addWidget(btn_restore)
        action_layout.addWidget(self.btn_run)
        action_layout.addWidget(self.status_label)
        action_layout.addStretch(1)
        action_layout.addWidget(btn_exit)

        self.size_label = QtWidgets.QLabel()
        self.progress_bar = QtWidgets.QProgressBar()
        self.txt_log = QtWidgets.QTextEdit(readOnly=True)
        self.txt_log.setMinimumHeight(130)

        self.lst_src.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.lst_excl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.txt_log.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        grid = QtWidgets.QGridLayout(cw)
        grid.addLayout(target_layout, 0, 0, 1, 2)
        grid.addLayout(src_layout, 1, 0)
        grid.addLayout(excl_layout, 1, 1, 9, 1)
        grid.addWidget(schedule_group, 2, 0)
        grid.addWidget(behavior_group, 3, 0)
        grid.addLayout(action_layout, 4, 0)
        grid.addWidget(self.size_label, 5, 0)
        grid.addWidget(self.lbl_last_success, 6, 0)
        grid.addWidget(self.progress_bar, 7, 0)
        grid.addWidget(self.txt_log, 8, 0)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(7, 1)
        grid.setRowStretch(8, 3)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)

        self._load_fields()

    def _load_fields(self):
        self.le_target.setText(self.cfg.target_dir)
        self.lst_src.clear()
        for rule in self.cfg.sources:
            self.lst_src.addItem(rule.source)
        self.lst_src.setCurrentRow(0 if self.cfg.sources else -1)
        self._refresh_excludes()
        for key, cb in self.schedule_controls.items():
            cb.setChecked(exists(key))
        self.chk_wait.setChecked(self.cfg.wait_on_finish)
        self.chk_console.setChecked(self.cfg.show_console)
        self.chk_tray.setChecked(self.cfg.show_tray_icon)
        self.chk_overlay.setChecked(self.cfg.show_overlay)
        self._update_last_success_label()
        self._update_backup_size()

    def _pick_target(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, _("Select target directory"))
        if directory:
            self.le_target.setText(directory)
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
            self.lst_excl.addItem(excl)

    def _edit_excludes(self):
        dialog = ExcludeDialog(self.cfg, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            new_excls = dialog.get_excludes()
            for rule in self.cfg.sources:
                rule.excludes = new_excls.get(rule.source, [])
            self._refresh_excludes()
            self._update_backup_size()

    def _save(self):
        target = self.le_target.text().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Please specify the target directory"))
            return
        self.cfg.target_dir = target
        self.cfg.wait_on_finish = self.chk_wait.isChecked()
        self.cfg.show_console = self.chk_console.isChecked()
        self.cfg.show_tray_icon = self.chk_tray.isChecked()
        self.cfg.show_overlay = self.chk_overlay.isChecked()
        self.cfg.save()
        for key, cb in self.schedule_controls.items():
            if cb.isChecked():
                if not exists(key):
                    schedule(key)
            else:
                if exists(key):
                    delete(key)

        self.status_label.setText(_("Saved"))
        self._update_backup_size()

    def _restore(self):
        loaded = Settings.load()
        if not loaded:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("No settings found"))
            return
        self.cfg = loaded
        self._load_fields()
        self.status_label.setText(_("Restored"))

    def _update_backup_size(self):
        self.size_label.setText(_("Calculating size…"))
        if hasattr(self, '_size_worker') and self._size_worker.isRunning():
            return
        self._size_worker = SizeWorker(self.cfg.sources)
        self._size_worker.sizeCalculated.connect(self._on_size_calculated)
        self._size_worker.start()

    def _on_size_calculated(self, size: int):
        self.size_label.setText(
            _("Estimated backup size: {size}").format(size=human_readable(size))
        )

    def _run(self):
        self.txt_log.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText(_("Backing up…"))
        self.btn_run.setEnabled(False)
        def _job():
            success = run_backup(self.cfg, self.progressChanged.emit, self.logAppended.emit)
            self.backupFinished.emit(success)
        threading.Thread(target=_job, daemon=True).start()

    def _handle_progress(self, i: int, tot: int):
        self.progress_bar.setValue(int(i / tot * 100) if tot else 100)

    def _update_last_success_label(self):
        if self.cfg.last_success:
            try:
                dt = datetime.fromisoformat(self.cfg.last_success)
                pretty = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pretty = self.cfg.last_success
            text = _("Last successful backup: {ts}").format(ts=pretty)
        else:
            text = _("Last successful backup: never")
        self.lbl_last_success.setText(text)

    def _on_backup_finished(self, success: bool):
        self.btn_run.setEnabled(True)
        self.status_label.setText(_("Done") if success else _("Finished with errors"))
        self._update_last_success_label()
        self._update_backup_size()
