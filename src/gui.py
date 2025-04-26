import queue
import threading
from pathlib import Path

from PySide6 import QtWidgets, QtCore

from src.config import Settings, PathRule
from src.copier import run_backup
from src.scheduler import (
    schedule_daily as daily,
    schedule_weekly as weekly,
    schedule_onstart as onstart,
    schedule_onidle as onidle,
    delete, _exists
)
from .ExcludeDialog import ExcludeDialog
from .utils import human_readable, dir_size


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backup Tool Settings")
        self.cfg = Settings.load() or Settings(target_dir="")
        self._build_ui()

    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        vlay = QtWidgets.QVBoxLayout(cw)

        # Цель
        hlay1 = QtWidgets.QHBoxLayout()
        hlay1.addWidget(QtWidgets.QLabel("Цель копии:"))
        self.le_target = QtWidgets.QLineEdit()
        hlay1.addWidget(self.le_target, 1)
        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.clicked.connect(self._pick_target)
        hlay1.addWidget(btn_pick)
        vlay.addLayout(hlay1)

        # Источники + кнопки
        hlay2 = QtWidgets.QHBoxLayout()
        src_box = QtWidgets.QVBoxLayout()
        src_box.addWidget(QtWidgets.QLabel("Источники:"))
        self.lst_src = QtWidgets.QListWidget()
        self.lst_src.currentRowChanged.connect(self._refresh_excl)
        src_box.addWidget(self.lst_src, 1)
        btns = QtWidgets.QHBoxLayout()
        for txt, cmd in [("+", self._add_src), ("–", self._del_src), ("Clr", self._clr_src)]:
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(cmd)
            btns.addWidget(b)
        src_box.addLayout(btns)
        hlay2.addLayout(src_box, 1)

        # Исключения
        excl_box = QtWidgets.QVBoxLayout()
        excl_box.addWidget(QtWidgets.QLabel("Исключения:"))
        self.lst_excl = QtWidgets.QListWidget()
        excl_box.addWidget(self.lst_excl, 1)
        btn_excl = QtWidgets.QPushButton("Исключать")
        btn_excl.clicked.connect(self._edit_excl)
        excl_box.addWidget(btn_excl)
        hlay2.addLayout(excl_box, 1)

        vlay.addLayout(hlay2, 1)

        # Периодичность
        gb = QtWidgets.QGroupBox("Периодичность")
        gl = QtWidgets.QVBoxLayout(gb)
        self.cb_day = QtWidgets.QCheckBox("Ежедневно 03:00")
        self.cb_week = QtWidgets.QCheckBox("Еженедельно (Пн 03:00)")
        self.cb_start = QtWidgets.QCheckBox("При включении системы")
        self.cb_idle = QtWidgets.QCheckBox("При пробуждении/простое")
        for cb in (self.cb_day, self.cb_week, self.cb_start, self.cb_idle):
            gl.addWidget(cb)
        vlay.addWidget(gb)

        # Кнопки управления
        hlay3 = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("Сохранить")
        btn_restore = QtWidgets.QPushButton("Восстановить")
        btn_run = QtWidgets.QPushButton("Сделать копию")
        btn_exit = QtWidgets.QPushButton("Выход")
        btn_save.clicked.connect(self._save)
        btn_restore.clicked.connect(self._restore)
        btn_run.clicked.connect(self._run)
        btn_exit.clicked.connect(self.close)
        for w in (btn_save, btn_restore, btn_run):
            hlay3.addWidget(w)
        hlay3.addStretch(1)
        hlay3.addWidget(btn_exit)
        vlay.addLayout(hlay3)

        # Статус и размер
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("color: green;")
        vlay.addWidget(self.status_label)
        self.size_label = QtWidgets.QLabel("")
        vlay.addWidget(self.size_label)

        # Прогресс и лог
        self.progressBar = QtWidgets.QProgressBar()
        vlay.addWidget(self.progressBar)
        self.txt_log = QtWidgets.QTextEdit(readOnly=True)
        self.txt_log.setFixedHeight(100)
        vlay.addWidget(self.txt_log)

        self._load_fields()

    def _load_fields(self):
        self.le_target.setText(self.cfg.target_dir)
        self.lst_src.clear()
        for r in self.cfg.sources:
            self.lst_src.addItem(r.source)
        if self.cfg.sources:
            self.lst_src.setCurrentRow(0)  # сразу показываем первый источник
        self._refresh_excl()
        self.cb_day.setChecked(False)
        self.cb_week.setChecked(False)
        self.cb_start.setChecked(False)
        self.cb_idle.setChecked(False)
        self._update_backup_size()

    def _pick_target(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Куда копировать")
        if d:
            self.le_target.setText(d)
            self._update_backup_size()

    def _add_src(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Добавить источник")
        if d:
            self.cfg.sources.append(PathRule(source=d))
            self._load_fields()

    def _del_src(self):
        row = self.lst_src.currentRow()
        if row >= 0:
            self.cfg.sources.pop(row)
            self._load_fields()

    def _clr_src(self):
        self.cfg.sources.clear()
        self._load_fields()

    def _refresh_excl(self):
        self.lst_excl.clear()
        row = self.lst_src.currentRow()
        if row < 0:
            return
        for e in self.cfg.sources[row].excludes:
            self.lst_excl.addItem(e)

    def _edit_excl(self):
        dlg = ExcludeDialog(self.cfg, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            new_excls = dlg.get_excludes()
            for rule in self.cfg.sources:
                rule.excludes = new_excls.get(rule.source, [])
            self._refresh_excl()

    def _save(self):
        tgt = self.le_target.text().strip()
        if not tgt:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Укажите целевую директорию")
            return
        self.cfg.target_dir = tgt
        self.cfg.save()
        if _exists(): delete()
        if self.cb_day.isChecked():   daily()
        if self.cb_week.isChecked():  weekly()
        if self.cb_start.isChecked(): onstart()
        if self.cb_idle.isChecked():  onidle()
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
        td = Path(self.cfg.target_dir)
        if td.is_dir():
            size = dir_size(td)
            self.size_label.setText(f"Текущий размер бэкапа: {human_readable(size)}")
        else:
            self.size_label.setText("")

    def _run(self):
        self._save()
        self.status_label.setText("Копирование…")
        self.setEnabled(False)
        q = queue.Queue()

        def prog(i, tot): q.put(("prog", i, tot))

        def logm(m):     q.put(("log", m))

        threading.Thread(target=run_backup, args=(self.cfg, prog, logm), daemon=True).start()
        QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))

    def _process_queue(self, q: queue.Queue):
        while not q.empty():
            typ, *data = q.get()
            if typ == "prog":
                i, tot = data
                self.progressBar.setValue(int(i / tot * 100))
            else:
                self.txt_log.append(data[0])
        if self.progressBar.value() < 100:
            QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))
        else:
            self.setEnabled(True)
            self.status_label.setText("Готово")


def open_gui():
    app = QtWidgets.QApplication([])
    win = MainWindow()
    win.resize(840, 560)
    win.show()
    app.exec()
