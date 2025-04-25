from pathlib import Path

from PySide6 import QtWidgets, QtCore

from src.config import Settings


class ExcludeDialog(QtWidgets.QDialog):
    def __init__(self, cfg: Settings, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Исключения")
        self.resize(700, 500)

        vlay = QtWidgets.QVBoxLayout(self)

        # легенда и кнопки
        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(QtWidgets.QLabel("☐ – копируется,   ☑ – исключено"), 1)
        self.btn_expand = QtWidgets.QPushButton("Развернуть всё")
        self.btn_collapse = QtWidgets.QPushButton("Свернуть всё")
        self.btn_save = QtWidgets.QPushButton("Сохранить")
        hlay.addWidget(self.btn_expand)
        hlay.addWidget(self.btn_collapse)
        hlay.addWidget(self.btn_save)
        vlay.addLayout(hlay)

        # дерево
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        vlay.addWidget(self.tree, 1)

        # заполнение
        for rule in self.cfg.sources:
            root = QtWidgets.QTreeWidgetItem(self.tree)
            root.setText(0, Path(rule.source).name or rule.source)
            root.setData(0, QtCore.Qt.UserRole, rule.source)
            root.setCheckState(0, QtCore.Qt.Unchecked)
            self._add_items(root, Path(rule.source))

        # отмечаем уже существующие исключения
        for rule in self.cfg.sources:
            for excl in rule.excludes:
                abs_path = str(Path(rule.source) / excl)
                self._mark_path(self.tree.invisibleRootItem(), abs_path)

        # сигналы
        self.btn_expand.clicked.connect(self.tree.expandAll)
        self.btn_collapse.clicked.connect(self.tree.collapseAll)
        self.btn_save.clicked.connect(self.accept)

    def _add_items(self, parent: QtWidgets.QTreeWidgetItem, path: Path):
        try:
            for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                child = QtWidgets.QTreeWidgetItem(parent)
                child.setText(0, entry.name)
                child.setData(0, QtCore.Qt.UserRole, str(entry))
                child.setCheckState(0, QtCore.Qt.Unchecked)
                if entry.is_dir():
                    self._add_items(child, entry)
        except PermissionError:
            pass

    def _mark_path(self, item: QtWidgets.QTreeWidgetItem, target: str):
        # обходим дерево, находим по UserRole
        if item.data(0, QtCore.Qt.UserRole) == target:
            item.setCheckState(0, QtCore.Qt.Checked)
        for i in range(item.childCount()):
            self._mark_path(item.child(i), target)

    def get_excludes(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        root_count = self.tree.topLevelItemCount()
        for i in range(root_count):
            root = self.tree.topLevelItem(i)
            src = root.data(0, QtCore.Qt.UserRole)
            excludes: list[str] = []

            def recurse(it: QtWidgets.QTreeWidgetItem):
                for j in range(it.childCount()):
                    ch = it.child(j)
                    state = ch.checkState(0)
                    p = Path(ch.data(0, QtCore.Qt.UserRole))
                    if state == QtCore.Qt.Checked:
                        excludes.append(str(p.relative_to(Path(src))).replace("\\", "/"))
                    recurse(ch)

            recurse(root)
            result[src] = excludes
        return result
