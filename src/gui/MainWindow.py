import queue
import threading
from pathlib import Path

from PySide6 import QtWidgets, QtCore
from PySide6.QtWidgets import QSizePolicy

from src.config import Settings, PathRule
from src.copier import run_backup
from src.scheduler import _exists, delete, schedule_daily as daily, schedule_weekly as weekly, \
    schedule_onstart as onstart, schedule_onidle as onidle
from src.utils import dir_size, human_readable
from .ExcludeDialog import ExcludeDialog


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backup Tool Settings")
        self.cfg = Settings.load() or Settings(target_dir="")
        self._build_ui()

    # ------------------------- UI -------------------------
    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)

        # --- Подготовка всех блоков UI заранее ---
        # 1) Target dir
        hlay1 = QtWidgets.QHBoxLayout()
        hlay1.addWidget(QtWidgets.QLabel("Цель копии:"))
        self.le_target = QtWidgets.QLineEdit()
        hlay1.addWidget(self.le_target, 1)
        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.clicked.connect(self._pick_target)
        hlay1.addWidget(btn_pick)

        # 2) Источники
        src_box = QtWidgets.QVBoxLayout()
        src_box.addWidget(QtWidgets.QLabel("Источники:"))
        self.lst_src = QtWidgets.QListWidget()
        self.lst_src.currentRowChanged.connect(self._refresh_excl)
        src_box.addWidget(self.lst_src)
        btns = QtWidgets.QHBoxLayout()
        for txt, cmd in [("+ Add source", self._add_src), ("– Delete", self._del_src), ("Clear", self._clr_src)]:
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(cmd)
            btns.addWidget(b)
        src_box.addLayout(btns)

        # 3) Исключения
        excl_box = QtWidgets.QVBoxLayout()
        excl_box.addWidget(QtWidgets.QLabel("Исключения в выбранной директории:"))
        self.lst_excl = QtWidgets.QListWidget()
        excl_box.addWidget(self.lst_excl, 1)  # растягиваем по высоте
        btn_excl = QtWidgets.QPushButton("Исключать")
        btn_excl.clicked.connect(self._edit_excl)
        excl_box.addWidget(btn_excl)

        # 4) Периодичность
        gb = QtWidgets.QGroupBox("Периодичность")
        gl = QtWidgets.QVBoxLayout(gb)
        self.cb_day = QtWidgets.QCheckBox("Ежедневно 03:00")
        self.cb_week = QtWidgets.QCheckBox("Еженедельно (Пн 03:00)")
        self.cb_start = QtWidgets.QCheckBox("При включении системы")
        self.cb_idle = QtWidgets.QCheckBox("При пробуждении/простое")
        for cb in (self.cb_day, self.cb_week, self.cb_start, self.cb_idle):
            gl.addWidget(cb)

        # 5) Кнопки управления
        hlay3 = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("Сохранить");
        btn_save.clicked.connect(self._save)
        btn_restore = QtWidgets.QPushButton("Восстановить");
        btn_restore.clicked.connect(self._restore)
        btn_run = QtWidgets.QPushButton("Сделать копию");
        btn_run.clicked.connect(self._run)
        btn_exit = QtWidgets.QPushButton("Выход");
        btn_exit.clicked.connect(self.close)
        for w in (btn_save, btn_restore, btn_run):
            hlay3.addWidget(w)
        hlay3.addStretch(1)
        hlay3.addWidget(btn_exit)

        # 6) Статус и размер
        self.status_label = QtWidgets.QLabel("");
        self.status_label.setStyleSheet("color: green;")
        self.size_label = QtWidgets.QLabel("")

        # 7) Прогресс и лог
        self.progressBar = QtWidgets.QProgressBar()
        self.txt_log = QtWidgets.QTextEdit(readOnly=True)

        self.lst_src.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.lst_excl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.txt_log.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        # --- Основная сетка: две колонки ---
        grid = QtWidgets.QGridLayout(cw)
        # Row 0: цель копии на всю ширину
        grid.addLayout(hlay1, 0, 0, 1, 2)

        # Row 1: источники (col 0) и исключения (col 1) – исключения растягиваем на 7 строк
        grid.addLayout(src_box, 1, 0)
        grid.addLayout(excl_box, 1, 1, 7, 1)

        # Далее в первой колонке (col 0):
        grid.addWidget(gb, 2, 0)  # периодичность
        grid.addLayout(hlay3, 3, 0)  # кнопки
        grid.addWidget(self.status_label, 4, 0)
        grid.addWidget(self.size_label, 5, 0)
        grid.addWidget(self.progressBar, 6, 0)
        grid.addWidget(self.txt_log, 7, 0)

        grid.setRowStretch(1, 1)  # источники/исключения
        grid.setRowStretch(7, 1)  # лог

        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)

        # Загружаем сохранённые значения
        self._load_fields()

    # ------------------------- Helpers -------------------------
    def _load_fields(self):
        self.le_target.setText(self.cfg.target_dir)
        self.lst_src.clear()
        for r in self.cfg.sources:
            self.lst_src.addItem(r.source)
        if self.cfg.sources:
            self.lst_src.setCurrentRow(0)
        self._refresh_excl()
        for cb in (self.cb_day, self.cb_week, self.cb_start, self.cb_idle):
            cb.setChecked(False)
        self._update_backup_size()

    # ---------- Picking paths ----------
    def _pick_target(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Куда копировать")
        if d:
            self.le_target.setText(d)
            self._update_backup_size()

    # ---------- Source CRUD ----------
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

    # ---------- Excludes ----------
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
            self._update_backup_size()  # 🔄 Обновляем сразу после изменения исключений

    # ------------------------- Save / Restore -------------------------
    def _save(self):
        tgt = self.le_target.text().strip()
        if not tgt:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Укажите целевую директорию")
            return
        self.cfg.target_dir = tgt
        self.cfg.save()
        if _exists():
            delete()
        if self.cb_day.isChecked():
            daily()
        if self.cb_week.isChecked():
            weekly()
        if self.cb_start.isChecked():
            onstart()
        if self.cb_idle.isChecked():
            onidle()
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

    # ------------------------- Size calc -------------------------
    def _update_backup_size(self):
        self.size_label.setText("Подсчёт размера…")
        threading.Thread(target=self._calc_size_async, daemon=True).start()

    def _calc_size_async(self):
        size = self._calc_selected_size()
        txt = f"Оценочный размер копии: {human_readable(size)}"
        QtCore.QMetaObject.invokeMethod(
            self.size_label, "setText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, txt)
        )

    def _calc_selected_size(self) -> int:
        total = 0
        for rule in self.cfg.sources:
            root = Path(rule.source).expanduser().resolve()
            if not root.exists():
                continue
            root_size = dir_size(root)
            for ex in rule.excludes:
                p = root / ex
                if p.exists():
                    root_size -= dir_size(p)
            total += max(root_size, 0)
        return total

    # ------------------------- Run backup -------------------------
    def _run(self):
        self._save()
        self.status_label.setText("Копирование…")
        self.setEnabled(False)
        q = queue.Queue()

        def prog(i, tot):
            q.put(("prog", i, tot))

        def logm(m):
            q.put(("log", m))

        threading.Thread(target=run_backup, args=(self.cfg, prog, logm), daemon=True).start()
        QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))

    def _process_queue(self, q: queue.Queue):
        while not q.empty():
            typ, *data = q.get()
            if typ == "prog":
                i, tot = data
                if tot:
                    self.progressBar.setValue(int(i / tot * 100))
            else:
                self.txt_log.append(data[0])
        if self.progressBar.value() < 100:
            QtCore.QTimer.singleShot(100, lambda: self._process_queue(q))
        else:
            self.setEnabled(True)
            self.status_label.setText("Готово")
            self._update_backup_size()  # 🔄 Обновляем после завершения бэкапа
