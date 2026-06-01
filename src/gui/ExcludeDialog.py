import os
from pathlib import Path
from typing import Dict, List

from PySide6 import QtCore, QtWidgets, QtGui

from src.config import Settings
from src.exclusions import ExclusionMatcher
from src.i18n import _
from src.utils import human_readable


class ExcludeDialog(QtWidgets.QDialog):
    PATH_ROLE = QtCore.Qt.UserRole + 1
    LOADED_ROLE = QtCore.Qt.UserRole + 2
    SIZE_ROLE = QtCore.Qt.UserRole + 3

    def __init__(self, cfg: Settings, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self._matchers: dict[Path, ExclusionMatcher] = {}
        self._saved_excludes: Dict[str, List[str]] | None = None
        self._loading_checks = False

        self.setWindowTitle(_("Exclusions"))
        self.resize(0, 640)

        self._build_ui()

        self._legend_timer = QtCore.QTimer(self)
        self._legend_timer.setSingleShot(True)
        self._legend_timer.setInterval(200)
        self._legend_timer.timeout.connect(self._update_legend)

        self._populate_roots()
        self._restore_checks()
        self._mark_clean()
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
        ]
        hbtn = QtWidgets.QHBoxLayout()
        for txt, slot in btns:
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(slot)
            hbtn.addWidget(b)
        self.btn_save = QtWidgets.QPushButton(_("Save"))
        self._save_button_base_style = self.btn_save.styleSheet()
        self.btn_save.clicked.connect(self.accept)
        hbtn.addWidget(self.btn_save)
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
            self._matchers[root] = ExclusionMatcher(root, [], self._cfg.exclude_patterns)
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
            with QtCore.QSignalBlocker(self.tree):
                parent.takeChildren()
                parent_state = parent.checkState(0)
                with os.scandir(path) as it:
                    for entry in it:
                        child_path = Path(entry.path)
                        if self._is_pattern_ignored(child_path):
                            continue
                        child = self._make_item(
                            entry.name,
                            child_path,
                            entry.is_dir(follow_symlinks=False)
                        )
                        if parent_state != QtCore.Qt.PartiallyChecked:
                            child.setCheckState(0, parent_state)
                        parent.addChild(child)
        except PermissionError:
            pass
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            parent.setData(0, self.LOADED_ROLE, True)

    def _is_pattern_ignored(self, path: Path) -> bool:
        best_root: Path | None = None
        best_matcher: ExclusionMatcher | None = None
        for root, matcher in self._matchers.items():
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if best_root is None or len(root.parts) > len(best_root.parts):
                best_root = root
                best_matcher = matcher
        return best_matcher.skip(path) if best_matcher is not None else False

    def _on_expand(self, item):
        self._load_children(item)

    def _set_state(self, state):
        with QtCore.QSignalBlocker(self.tree):
            self._set_state_rec(self.tree.invisibleRootItem(), state)
        self._update_legend_async()
        self._update_dirty_state()

    def _set_state_rec(self, itm, st):
        itm.setCheckState(0, st)
        for i in range(itm.childCount()):
            self._set_state_rec(itm.child(i), st)

    def _on_item_changed(self, item):
        with QtCore.QSignalBlocker(self.tree):
            self._propagate_down(item)
            self._bubble_up(item)
        self._update_legend_async()
        self._update_dirty_state()

    def _propagate_down(self, itm):
        state = itm.checkState(0)
        if state == QtCore.Qt.PartiallyChecked:
            return
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
        self._loading_checks = True
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
        self._loading_checks = False

    def _mark_clean(self) -> None:
        self._saved_excludes = self.get_excludes()
        self._update_dirty_state()

    def _update_dirty_state(self) -> None:
        if self._loading_checks or self._saved_excludes is None:
            return
        dirty = self.get_excludes() != self._saved_excludes
        if dirty:
            self.btn_save.setStyleSheet(
                f"{self._save_button_base_style} "
                "QPushButton { background-color: #b66a00; color: white; font-weight: 700; }"
            )
        else:
            self.btn_save.setStyleSheet(self._save_button_base_style)

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
        st = itm.checkState(0)
        if st == QtCore.Qt.Unchecked:
            return 0, 0
        path = itm.data(0, ExcludeDialog.PATH_ROLE)
        if st == QtCore.Qt.Checked:
            size = itm.data(0, ExcludeDialog.SIZE_ROLE) if not path.is_dir() else 0
            return size or 0, 1

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
            root_path = Path(rule.source).expanduser().resolve()
            self._collect_checked(root_itm, sel)
            minimal: List[Path] = []
            for p in sorted(sel, key=lambda p: (len(p.parts), str(p).lower())):
                if not any(p.is_relative_to(m) for m in minimal):
                    minimal.append(p)
            res[rule.source] = [str(p.relative_to(root_path)) for p in minimal]
        return res

    def _collect_checked(self, itm, out: List[Path]) -> QtCore.Qt.CheckState:
        st = itm.checkState(0)
        p: Path = itm.data(0, self.PATH_ROLE)

        if itm.childCount() == 0:
            if st == QtCore.Qt.Checked:
                out.append(p)
            return st

        child_states = [self._collect_checked(itm.child(i), out) for i in range(itm.childCount())]

        if st == QtCore.Qt.Checked and all(cs == QtCore.Qt.Checked for cs in child_states):
            out.append(p)
            return QtCore.Qt.Checked

        if st == QtCore.Qt.Unchecked and all(cs == QtCore.Qt.Unchecked for cs in child_states):
            return QtCore.Qt.Unchecked

        return QtCore.Qt.PartiallyChecked
