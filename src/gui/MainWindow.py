import queue
import threading
from pathlib import Path

from PySide6 import QtWidgets, QtCore
from PySide6.QtWidgets import QSizePolicy

from src.config import Settings, PathRule
from src.copier import run_backup
from src.scheduler import (
    exists, delete,
    schedule_daily as daily,
    schedule_weekly as weekly,
    schedule_onstart as onstart,
    schedule_onidle as onidle
)

from src.utils import dir_size, human_readable
from .ExcludeDialog import ExcludeDialog


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backup Tool Settings")
        self.cfg = Settings.load() or Settings(target_dir="")
        self._build_ui()

    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)

        # Target directory
        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(QtWidgets.QLabel("Цель копии:"))
        self.le_target = QtWidgets.QLineEdit()
        target_layout.addWidget(self.le_target, 1)
        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.clicked.connect(self._pick_target)
        target_layout.addWidget(btn_pick)

        # Sources list and controls
        self.lst_src = QtWidgets.QListWidget()
        self.lst_src.currentRowChanged.connect(self._refresh_excludes)
        src_layout = QtWidgets.QVBoxLayout()
        src_layout.addWidget(QtWidgets.QLabel("Источники:"))
        src_layout.addWidget(self.lst_src)

        btn_src_layout = QtWidgets.QHBoxLayout()
        controls = [
            ("+ Add source", self._add_source),
            ("– Delete", self._delete_source),
            ("Clear", self._clear_sources),
            ("Исключать", self._edit_excludes)
        ]
        for text, handler in controls:
            btn = QtWidgets.QPushButton(text)
            btn.clicked.connect(handler)
            if text == "Исключать":
                btn.setStyleSheet("background-color: #204686; color: white;")
            btn_src_layout.addWidget(btn)
        src_layout.addLayout(btn_src_layout)

        # Exclusions list linked to selected source
        self.lbl_excl = QtWidgets.QLabel("Исключения для источника:")
        self.lst_excl = QtWidgets.QListWidget()
        excl_layout = QtWidgets.QVBoxLayout()
        excl_layout.addWidget(self.lbl_excl)
        excl_layout.addWidget(self.lst_excl)

        # Schedule options
        schedule_group = QtWidgets.QGroupBox("Периодичность")
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)
        self.cb_day = QtWidgets.QCheckBox("Ежедневно 03:00")
        self.cb_week = QtWidgets.QCheckBox("Еженедельно (Пн 03:00)")
        self.cb_start = QtWidgets.QCheckBox("При включении системы")
        self.cb_idle = QtWidgets.QCheckBox("При простое")
        for cb in (self.cb_day, self.cb_week, self.cb_start, self.cb_idle):
            schedule_layout.addWidget(cb)

        # Action buttons
        action_layout = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        btn_restore = QtWidgets.QPushButton("Восстановить")
        btn_restore.clicked.connect(self._restore)
        btn_run = QtWidgets.QPushButton("Сделать копию")
        btn_run.clicked.connect(self._run)
        btn_exit = QtWidgets.QPushButton("Выход")
        btn_exit.clicked.connect(self.close)

        for w in (btn_save, btn_restore, btn_run):
            action_layout.addWidget(w)
        action_layout.addStretch(1)
        action_layout.addWidget(btn_exit)

        # Status and size display
        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: green;")
        self.size_label = QtWidgets.QLabel()

        # Progress and log
        self.progress_bar = QtWidgets.QProgressBar()
        self.txt_log = QtWidgets.QTextEdit(readOnly=True)

        # Policies
        self.lst_src.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.lst_excl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.txt_log.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # Main grid layout
        grid = QtWidgets.QGridLayout(cw)
        grid.addLayout(target_layout, 0, 0, 1, 2)
        grid.addLayout(src_layout, 1, 0)
        grid.addLayout(excl_layout, 1, 1, 7, 1)
        grid.addWidget(schedule_group, 2, 0)
        grid.addLayout(action_layout, 3, 0)
        grid.addWidget(self.status_label, 4, 0)
        grid.addWidget(self.size_label, 5, 0)
        grid.addWidget(self.progress_bar, 6, 0)
        grid.addWidget(self.txt_log, 7, 0)

        grid.setRowStretch(1, 1)
        grid.setRowStretch(7, 1)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)

        self._load_fields()

    def _load_fields(self):
        self.le_target.setText(self.cfg.target_dir)

        # Источники и исключения
        self.lst_src.clear()
        for rule in self.cfg.sources:
            self.lst_src.addItem(rule.source)
        self.lst_src.setCurrentRow(0 if self.cfg.sources else -1)
        self._refresh_excludes()

        # Чекбоксы на основе реальных задач в планировщике
        # functions imported: exists("daily"/"weekly"/"onstart"/"onidle")
        self.cb_day.setChecked   ( exists("daily") )
        self.cb_week.setChecked  ( exists("weekly") )
        self.cb_start.setChecked ( exists("onstart") )
        self.cb_idle.setChecked  ( exists("onidle") )

        # Обновляем оценку размера
        self._update_backup_size()


    def _pick_target(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Куда копировать")
        if directory:
            self.le_target.setText(directory)
            self._update_backup_size()

    def _add_source(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Добавить источник")
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
            self.lbl_excl.setText("Исключения для источника:")
            return
        source = self.cfg.sources[row].source
        self.lbl_excl.setText(f"Исключения для: {source}")
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
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Укажите целевую директорию")
            return
        self.cfg.target_dir = target
        self.cfg.save()

        # --- Синхронизация задач ---
        # Ежедневно
        if self.cb_day.isChecked():
            if not exists("daily"):
                daily()
        else:
            if exists("daily"):
                delete("daily")

        # Еженедельно (Пн)
        if self.cb_week.isChecked():
            if not exists("weekly"):
                weekly()
        else:
            if exists("weekly"):
                delete("weekly")

        # При включении системы
        if self.cb_start.isChecked():
            if not exists("onstart"):
                onstart()
        else:
            if exists("onstart"):
                delete("onstart")

        # При пробуждении/простое
        if self.cb_idle.isChecked():
            if not exists("onidle"):
                onidle()
        else:
            if exists("onidle"):
                delete("onidle")
        # ----------------------------

        self.status_label.setText("Сохранено")
        self._update_backup_size()

    def _restore(self):
        loaded = Settings.load()
        if not loaded:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Настроек нет")
            return
        self.cfg = loaded
        self._load_fields()
        self.status_label.setText("Восстановлено")

    def _update_backup_size(self):
        self.size_label.setText("Подсчёт размера…")
        threading.Thread(target=self._calc_size_async, daemon=True).start()

    def _calc_size_async(self):
        size = self._calc_selected_size()
        text = f"Оценочный размер копии: {human_readable(size)}"
        QtCore.QMetaObject.invokeMethod(
            self.size_label, "setText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, text)
        )

    def _calc_selected_size(self) -> int:
        total = 0
        for rule in self.cfg.sources:
            root = Path(rule.source).expanduser().resolve()
            if not root.exists():
                continue
            root_size = dir_size(root)
            for ex in rule.excludes:
                path = root / ex
                if path.exists():
                    root_size -= dir_size(path)
            total += max(root_size, 0)
        return total
    def _run(self):
        # Очищаем старые логи и прогресс
        self.txt_log.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText("Копирование…")
        self.setEnabled(False)

        q = queue.Queue()
        def prog(i, tot):
            q.put(("prog", i, tot))
        def log(msg):
            q.put(("log", msg))

        # Стартуем
        threading.Thread(target=run_backup, args=(self.cfg, prog, log), daemon=True).start()
        QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))


    def _process_queue(self, q: queue.Queue):
        while not q.empty():
            typ, *data = q.get()
            if typ == "prog":
                i, tot = data
                self.progress_bar.setValue(int(i / tot * 100) if tot else 100)
            else:
                self.txt_log.append(data[0])

        if self.progress_bar.value() < 100:
            QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))
        else:
            # Завершено
            self.setEnabled(True)
            self.status_label.setText("Готово")
            self.progress_bar.setValue(100)
            self._update_backup_size()
