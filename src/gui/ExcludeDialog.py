import os
from pathlib import Path
from typing import Dict, List

from PySide6 import QtCore, QtWidgets, QtGui

from src.config import Settings
from src.i18n import _
from src.utils import human_readable


class ExcludeDialog(QtWidgets.QDialog):
    PATH_ROLE = QtCore.Qt.UserRole + 1
    LOADED_ROLE = QtCore.Qt.UserRole + 2
    SIZE_ROLE = QtCore.Qt.UserRole + 3

    def __init__(self, cfg: Settings, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._cfg = cfg

        self.setWindowTitle(_("Exclusions"))
        self.resize(0, 640)

        self._build_ui()

        self._legend_timer = QtCore.QTimer(self)
        self._legend_timer.setSingleShot(True)
        self._legend_timer.setInterval(200)
        self._legend_timer.timeout.connect(self._update_legend)

        self._populate_roots()
        self._restore_checks()
        self._update_legend_async()

        btn_layout = self.layout().itemAt(0).layout()
        btn_width = btn_layout.sizeHint().width()
        margins = self.layout().contentsMargins()
        total_width = btn_width + margins.left() + margins.right()
        self.resize(total_width, self.height())

    def _build_ui(self):
        vbox = QtWidgets.QVBoxLayout(self)
        btns = [
            (_("Expand All"), self._expand_all),
            (_("Collapse All"), self._collapse_all),
            (_("Expand Current"), self._expand_cur),
            (_("Collapse Current"), self._collapse_cur),
            (_("Select All"), lambda: self._set_state(QtCore.Qt.Checked)),
            (_("Deselect All"), lambda: self._set_state(QtCore.Qt.Unchecked)),
            (_("Full Height"), self._stretch_h),
            (_("Save"), self.accept),
        ]
        hbtn = QtWidgets.QHBoxLayout()
        for txt, slot in btns:
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(slot)
            hbtn.addWidget(b)
        hbtn.addStretch(1)
        vbox.addLayout(hbtn)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels([_("File / Folder"), _("Size")])
        self.tree.setColumnWidth(0, 520)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemDoubleClicked.connect(self._open_path)
        vbox.addWidget(self.tree, 1)

        self.lbl_legend = QtWidgets.QLabel()
        vbox.addWidget(self.lbl_legend)

    def _open_path(self, item: QtWidgets.QTreeWidgetItem, column: int):
        path: Path = item.data(0, self.PATH_ROLE)
        if not path or not path.exists():
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(path)))

    def _populate_roots(self):
        self.tree.clear()
        for rule in self._cfg.sources:
            root = Path(rule.source).expanduser().resolve()
            itm = self._make_item(root.name, root, is_dir=True)
            self.tree.addTopLevelItem(itm)

    def _make_item(self, name: str, path: Path, is_dir: bool):
        size_text = "" if is_dir else human_readable(path.stat().st_size)
        itm = QtWidgets.QTreeWidgetItem([name, size_text])
        itm.setFlags(itm.flags() | QtCore.Qt.ItemIsUserCheckable)
        itm.setCheckState(0, QtCore.Qt.Unchecked)
        itm.setData(0, self.PATH_ROLE, path)
        itm.setData(0, self.LOADED_ROLE, False)
        if is_dir:
            itm.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ShowIndicator)
        else:
            itm.setData(0, self.SIZE_ROLE, path.stat().st_size)
        return itm

    def _load_children(self, parent):
        if parent.data(0, self.LOADED_ROLE):
            return
        path = parent.data(0, self.PATH_ROLE)
        if not path.is_dir():
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            parent.takeChildren()
            with os.scandir(path) as it:
                for entry in it:
                    child = self._make_item(
                        entry.name,
                        Path(entry.path),
                        entry.is_dir(follow_symlinks=False)
                    )
                    parent.addChild(child)
        except PermissionError:
            pass
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            parent.setData(0, self.LOADED_ROLE, True)

    def _on_expand(self, item):
        self._load_children(item)

    def _set_state(self, state):
        self._set_state_rec(self.tree.invisibleRootItem(), state)

    def _set_state_rec(self, itm, st):
        itm.setCheckState(0, st)
        for i in range(itm.childCount()):
            self._set_state_rec(itm.child(i), st)

    def _on_item_changed(self, item):
        with QtCore.QSignalBlocker(self.tree):
            self._propagate_down(item)
            self._bubble_up(item)
        self._update_legend_async()

    def _propagate_down(self, itm):
        state = itm.checkState(0)
        if state == QtCore.Qt.PartiallyChecked:
            return
        self._load_children(itm)
        for i in range(itm.childCount()):
            ch = itm.child(i)
            ch.setCheckState(0, state)
            self._propagate_down(ch)

    def _bubble_up(self, itm):
        pr = itm.parent()
        if pr is None:
            return
        states = {pr.child(i).checkState(0) for i in range(pr.childCount())}
        pr.setCheckState(
            0,
            QtCore.Qt.Checked if states == {QtCore.Qt.Checked} else
            QtCore.Qt.Unchecked if states == {QtCore.Qt.Unchecked} else
            QtCore.Qt.PartiallyChecked
        )
        self._bubble_up(pr)

    def _restore_checks(self):
        for idx, rule in enumerate(self._cfg.sources):
            root_itm = self.tree.topLevelItem(idx)
            root_path = Path(rule.source).expanduser().resolve()

            for ex in rule.excludes:
                abs_path = root_path / ex
                parts = abs_path.relative_to(root_path).parts
                cur_item = root_itm
                cur_path = root_path

                for p in parts:
                    self._load_children(cur_item)
                    cur_path = cur_path / p

                    for i in range(cur_item.childCount()):
                        ch = cur_item.child(i)
                        if ch.data(0, self.PATH_ROLE) == cur_path:
                            cur_item = ch
                            break
                    else:
                        cur_item = None
                        break

                if cur_item is not None:
                    cur_item.setCheckState(0, QtCore.Qt.Checked)

    def _update_legend_async(self):
        self._legend_timer.start()

    def _update_legend(self):
        total_size = 0
        total_count = 0
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            sz, cnt = ExcludeDialog._accumulate_static(root.child(i))
            total_size += sz
            total_count += cnt
        text = _("Selected: {count} • Size: {size}").format(
            count=total_count, size=human_readable(total_size))
        QtCore.QMetaObject.invokeMethod(
            self.lbl_legend, "setText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, text)
        )

    @staticmethod
    def _accumulate_static(itm):
        from src.utils import dir_size
        st = itm.checkState(0)
        if st == QtCore.Qt.Unchecked:
            return 0, 0
        path = itm.data(0, ExcludeDialog.PATH_ROLE)
        if st == QtCore.Qt.Checked:
            size = dir_size(path) if path.is_dir() else itm.data(0, ExcludeDialog.SIZE_ROLE)
            return size, 1
        total_sz = total_cnt = 0
        for i in range(itm.childCount()):
            sz, cnt = ExcludeDialog._accumulate_static(itm.child(i))
            total_sz += sz
            total_cnt += cnt
        return total_sz, total_cnt

    def _expand_all(self):
        self.tree.expandAll()

    def _collapse_all(self):
        self.tree.collapseAll()

    def _expand_cur(self):
        self._set_expanded_recursive(self.tree.currentItem(), True)

    def _collapse_cur(self):
        self._set_expanded_recursive(self.tree.currentItem(), False)

    def _set_expanded_recursive(self, itm, expand):
        if itm is None:
            return
        self._load_children(itm)
        itm.setExpanded(expand)
        for i in range(itm.childCount()):
            self._set_expanded_recursive(itm.child(i), expand)

    def _stretch_h(self):
        g = self.geometry()
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.setGeometry(g.x(), 0, g.width(), scr.height())

    def get_excludes(self) -> Dict[str, List[str]]:
        res: Dict[str, List[str]] = {}
        for idx, rule in enumerate(self._cfg.sources):
            root_itm = self.tree.topLevelItem(idx)
            sel: List[Path] = []
            self._collect(root_itm, sel)
            minimal: List[Path] = []
            for p in sorted(sel):
                if not any(p.is_relative_to(m) for m in minimal):
                    minimal.append(p)
            res[rule.source] = [str(p.relative_to(rule.source)) for p in minimal]
        return res

    def _collect(self, itm, out: List[Path]):
        st = itm.checkState(0)
        if st == QtCore.Qt.Unchecked:
            return
        p = itm.data(0, self.PATH_ROLE)
        if st == QtCore.Qt.Checked:
            out.append(p)
            return
        for i in range(itm.childCount()):
            self._collect(itm.child(i), out)
