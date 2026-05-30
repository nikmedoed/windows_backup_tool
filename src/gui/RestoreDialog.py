from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import threading

from PySide6 import QtCore, QtWidgets

from src.config import Settings
from src.i18n import _
from src.restore import RestoreAction, RestorePlan, build_restore_plan_for_sources
from src.utils import human_readable
from src.version_store import RunInfo, VersionStore


@dataclass(frozen=True)
class RestoreRequest:
    plan: RestorePlan


class RestoreDialog(QtWidgets.QDialog):
    _plan_ready = QtCore.Signal(int, object, str)
    _MAX_RENDERED_ACTIONS = 2000

    def __init__(
            self,
            cfg: Settings,
            target_root: Path,
            runs: list[RunInfo],
            parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(_("Restore backup"))
        self._cfg = cfg
        self._target_root = target_root
        self._runs = runs
        self._plan: RestorePlan | None = None
        self._request: RestoreRequest | None = None
        self._item_actions: dict[int, RestoreAction] = {}
        self._hidden_action_count = 0
        self._bulk_checked = True
        self._syncing_checks = False
        self._refresh_token = 0
        self._plan_ready.connect(self._on_plan_ready)
        self._build_ui()
        self._refresh_plan()

    def request(self) -> RestoreRequest | None:
        return self._request

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.run_combo = QtWidgets.QComboBox()
        for run in self._runs:
            label = _format_run_label(run)
            self.run_combo.addItem(label, run.id)
        self.run_combo.currentIndexChanged.connect(self._refresh_plan)
        form.addRow(_("Version:"), self.run_combo)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem(_("Original folders: apply patch only"), "original")
        self.mode_combo.addItem(_("Selected folder: restore full version"), "export")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        form.addRow(_("Restore to:"), self.mode_combo)

        export_row = QtWidgets.QHBoxLayout()
        self.export_path = QtWidgets.QLineEdit()
        self.export_path.editingFinished.connect(self._refresh_plan)
        export_row.addWidget(self.export_path, 1)
        self.btn_export = QtWidgets.QPushButton(_("Folder..."))
        self.btn_export.clicked.connect(self._browse_export)
        export_row.addWidget(self.btn_export)
        form.addRow(_("Output folder:"), export_row)

        layout.addLayout(form)

        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table = QtWidgets.QTreeWidget()
        self.table.setColumnCount(8)
        self.table.setHeaderLabels([
            _("Apply"),
            _("Issue"),
            _("Action"),
            _("File"),
            _("Current size"),
            _("Current modified"),
            _("Selected size"),
            _("Selected modified"),
        ])
        self.table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.header().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.header().setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setRootIsDecorated(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTreeWidget::item { padding: 2px 4px; } "
            "QTreeWidget::indicator { width: 18px; height: 18px; } "
        )
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton(_("Select all"))
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QtWidgets.QPushButton(_("Select none"))
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        btn_refresh = QtWidgets.QPushButton(_("Refresh diff"))
        btn_refresh.clicked.connect(self._refresh_plan)
        self.btn_delete_run = QtWidgets.QPushButton(_("Delete version"))
        self.btn_delete_run.clicked.connect(self._delete_selected_run)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(self.btn_delete_run)
        btn_row.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Apply selected"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        self._update_mode_controls()
        self.resize(1050, 620)

    def accept(self) -> None:
        if self._plan is None:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("No restore plan is available"))
            return
        selected = self._selected_actions()
        if not selected:
            QtWidgets.QMessageBox.warning(self, _("Error"), _("Select at least one change to apply"))
            return
        self._request = RestoreRequest(
            plan=RestorePlan(
                run_id=self._plan.run_id,
                scope_path=self._plan.scope_path,
                mode=self._plan.mode,
                actions=selected,
            )
        )
        super().accept()

    def _refresh_plan(self) -> None:
        run_id = self.run_combo.currentData()
        mode = self.mode_combo.currentData()
        if run_id is None:
            self._set_empty(_("Select version to see restore preview"))
            return
        export_root = None
        if mode == "export":
            export_text = self.export_path.text().strip()
            if not export_text:
                self._set_empty(_("Select output folder to preview the full selected version"))
                return
            export_root = Path(export_text)

        self._refresh_token += 1
        token = self._refresh_token
        self._set_loading()

        def _job() -> None:
            store = VersionStore(self._target_root)
            try:
                plan = build_restore_plan_for_sources(
                    store,
                    int(run_id),
                    list(self._cfg.sources),
                    mode=str(mode),
                    export_root=export_root,
                    compare_contents=False,
                    exclude_patterns=list(self._cfg.exclude_patterns),
                )
                self._plan_ready.emit(token, plan, "")
            except Exception as exc:
                self._plan_ready.emit(token, None, str(exc))
            finally:
                store.close()

        threading.Thread(target=_job, daemon=True).start()

    def _on_plan_ready(self, token: int, plan: object, error: str) -> None:
        if token != self._refresh_token:
            return
        if error:
            self._plan = None
            self._set_empty(_("Could not build restore diff: {exc}").format(exc=error))
            return
        self._plan = plan if isinstance(plan, RestorePlan) else None
        if self._plan is None:
            self._set_empty(_("Could not build restore diff: {exc}").format(exc="unknown error"))
            return
        self._fill_table()

    def _delete_selected_run(self) -> None:
        run_id = self.run_combo.currentData()
        if run_id is None:
            return
        deleted_index = self.run_combo.currentIndex()
        if QtWidgets.QMessageBox.question(
                self,
                _("Delete version"),
                _(
                    "Delete selected backup version? The latest mirror stays untouched; "
                    "only this restore point and unused archived files are removed."
                ),
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        store = VersionStore(self._target_root)
        try:
            store.delete_successful_run(int(run_id))
            self._runs = store.list_successful_runs()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                _("Delete version"),
                _("Could not delete backup version: {exc}").format(exc=exc),
            )
            return
        finally:
            store.close()

        if not self._runs:
            QtWidgets.QMessageBox.information(self, _("Delete version"), _("No backup versions left"))
            self.reject()
            return
        self._reload_runs_combo(fallback_index=deleted_index)
        self._refresh_plan()

    def _reload_runs_combo(self, *, fallback_index: int) -> None:
        previous = self.run_combo.currentData()
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        selected_index = min(max(fallback_index, 0), len(self._runs) - 1)
        for idx, run in enumerate(self._runs):
            self.run_combo.addItem(_format_run_label(run), run.id)
            if previous == run.id:
                selected_index = idx
        self.run_combo.setCurrentIndex(selected_index)
        self.run_combo.blockSignals(False)

    def _fill_table(self) -> None:
        assert self._plan is not None
        actions = self._plan.actions
        self.table.clear()
        self._item_actions.clear()
        self._hidden_action_count = max(0, len(actions) - self._MAX_RENDERED_ACTIONS)
        self._bulk_checked = True
        self._syncing_checks = True
        rendered_actions = actions[:self._MAX_RENDERED_ACTIONS]
        for folder, folder_actions in _group_actions_by_folder(rendered_actions).items():
            parent = self._make_folder_item(folder, folder_actions)
            self.table.addTopLevelItem(parent)
            for action in folder_actions:
                child = self._make_file_item(action)
                parent.addChild(child)
                self._item_actions[id(child)] = action
            parent.setExpanded(True)
            self._refresh_parent_check_state(parent)
        self._syncing_checks = False

        summary = _summary_text(self._plan)
        if self._hidden_action_count:
            summary = (
                f"{summary}\n"
                f"{_('Showing first {shown} files; hidden files remain selected by default.').format(
                    shown=len(rendered_actions)
                )}"
            )
        self.summary.setText(summary)
        self.ok_button.setEnabled(bool(actions))

    def _set_empty(self, message: str) -> None:
        self._plan = None
        self.table.clear()
        self._item_actions.clear()
        self._hidden_action_count = 0
        self.summary.setText(message)
        if hasattr(self, "ok_button"):
            self.ok_button.setEnabled(False)

    def _set_loading(self) -> None:
        self._plan = None
        self.table.clear()
        self._item_actions.clear()
        self._hidden_action_count = 0
        self.summary.setText(_("Building restore preview..."))
        if hasattr(self, "ok_button"):
            self.ok_button.setEnabled(False)

    def _selected_actions(self) -> list[RestoreAction]:
        if self._plan is None:
            return []
        if self._hidden_action_count:
            selected_rendered: list[RestoreAction] = []
            unchecked_rendered: set[int] = set()
            for row in range(self.table.topLevelItemCount()):
                parent = self.table.topLevelItem(row)
                for child_idx in range(parent.childCount()):
                    child = parent.child(child_idx)
                    action = self._item_actions.get(id(child))
                    if not action:
                        continue
                    if child.checkState(0) == QtCore.Qt.CheckState.Checked:
                        selected_rendered.append(action)
                    else:
                        unchecked_rendered.add(id(action))
            if not self._bulk_checked:
                return selected_rendered
            return [
                action
                for action in self._plan.actions
                if id(action) not in unchecked_rendered
            ]
        selected: list[RestoreAction] = []
        for row in range(self.table.topLevelItemCount()):
            parent = self.table.topLevelItem(row)
            for child_idx in range(parent.childCount()):
                child = parent.child(child_idx)
                action = self._item_actions.get(id(child))
                if action and child.checkState(0) == QtCore.Qt.CheckState.Checked:
                    selected.append(action)
        return selected

    def _set_all_checked(self, checked: bool) -> None:
        self._bulk_checked = checked
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        self._syncing_checks = True
        for row in range(self.table.topLevelItemCount()):
            parent = self.table.topLevelItem(row)
            if parent:
                parent.setCheckState(0, state)
                for child_idx in range(parent.childCount()):
                    parent.child(child_idx).setCheckState(0, state)
        self._syncing_checks = False

    def _mode_changed(self, _index: int = -1) -> None:
        self._update_mode_controls()
        self._refresh_plan()

    def _update_mode_controls(self) -> None:
        is_export = self.mode_combo.currentData() == "export"
        self.export_path.setEnabled(is_export)
        self.btn_export.setEnabled(is_export)

    def _browse_export(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, _("Select export folder"))
        if path:
            self.export_path.setText(path)
            self._refresh_plan()

    def _make_folder_item(self, folder: Path, actions: list[RestoreAction]) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([
            "",
            _folder_issue_label(actions),
            _folder_action_summary(actions),
            str(folder),
            "",
            "",
            "",
            "",
        ])
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, QtCore.Qt.CheckState.Checked)
        font = item.font(3)
        font.setBold(True)
        item.setFont(3, font)
        if any(a.conflict for a in actions):
            item.setForeground(1, QtCore.Qt.GlobalColor.darkYellow)
        for col in range(self.table.columnCount()):
            item.setToolTip(col, str(folder))
        return item

    def _make_file_item(self, action: RestoreAction) -> QtWidgets.QTreeWidgetItem:
        selected_size, selected_modified = _selected_version_fields(action)
        item = QtWidgets.QTreeWidgetItem([
            "",
            _issue_label(action),
            _plain_action_label(action),
            action.target_path.name,
            _current_size(action),
            _current_modified(action),
            selected_size,
            selected_modified,
        ])
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, QtCore.Qt.CheckState.Checked)
        if action.conflict:
            item.setForeground(1, QtCore.Qt.GlobalColor.darkYellow)
        for col in range(self.table.columnCount()):
            tooltip = str(action.target_path) if col == 3 else item.text(col)
            if action.conflict:
                tooltip = _("Current file is newer than the selected version") + "\n" + tooltip
            item.setToolTip(col, tooltip)
        return item

    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if self._syncing_checks or column != 0:
            return
        self._bulk_checked = False
        self._syncing_checks = True
        try:
            if item.parent() is None:
                state = item.checkState(0)
                for child_idx in range(item.childCount()):
                    item.child(child_idx).setCheckState(0, state)
            else:
                self._refresh_parent_check_state(item.parent())
        finally:
            self._syncing_checks = False

    def _refresh_parent_check_state(self, parent: QtWidgets.QTreeWidgetItem) -> None:
        checked = 0
        unchecked = 0
        for child_idx in range(parent.childCount()):
            if parent.child(child_idx).checkState(0) == QtCore.Qt.CheckState.Checked:
                checked += 1
            else:
                unchecked += 1
        if checked and unchecked:
            parent.setCheckState(0, QtCore.Qt.CheckState.PartiallyChecked)
        elif checked:
            parent.setCheckState(0, QtCore.Qt.CheckState.Checked)
        else:
            parent.setCheckState(0, QtCore.Qt.CheckState.Unchecked)



def _format_run_label(run: RunInfo) -> str:
    raw = run.finished_at or run.started_at
    try:
        dt = datetime.fromisoformat(raw)
        label = dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        label = raw
    return f"{label} (#{run.id})"


def _summary_text(plan: RestorePlan) -> str:
    if not plan.actions:
        if plan.mode == "export":
            return _("No files in this selected version and scope")
        return _("No patch is needed for this version and scope")
    if plan.mode == "export":
        return _("Full version export from all configured sources: {export} files").format(export=plan.export_count)
    return _(
        "Patch original files from all configured sources: create {create}, replace {replace}, delete {delete}, conflicts {conflicts}"
    ).format(
        create=plan.create_count,
        replace=plan.replace_count,
        delete=plan.delete_count,
        conflicts=plan.conflict_count,
    )


def _plain_action_label(action: RestoreAction) -> str:
    labels = {
        "create": _("Create"),
        "replace": _("Replace"),
        "delete": _("Delete"),
        "export": _("Export"),
    }
    return labels.get(action.action, action.action)


def _issue_label(action: RestoreAction) -> str:
    return _("Conflict") if action.conflict else ""


def _current_size(action: RestoreAction) -> str:
    if action.action == "export":
        return "-"
    if action.current_mtime is None:
        return _("Missing")
    if action.current_size is None:
        return _("Existing")
    return human_readable(action.current_size)


def _current_modified(action: RestoreAction) -> str:
    if action.action == "export":
        return _("Output file will be written")
    if action.current_mtime is None:
        return "-"
    return _format_mtime(action.current_mtime)


def _selected_version_fields(action: RestoreAction) -> tuple[str, str]:
    if action.action == "delete":
        return "-", _("Absent")
    size = human_readable(action.selected_size) if action.selected_size is not None else _("Selected file")
    modified = "-"
    if action.selected_mtime is not None:
        modified = _format_mtime(action.selected_mtime)
    return size, modified


def _format_mtime(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _group_actions_by_folder(actions: list[RestoreAction]) -> dict[Path, list[RestoreAction]]:
    grouped: dict[Path, list[RestoreAction]] = {}
    for action in actions:
        grouped.setdefault(action.target_path.parent, []).append(action)
    return dict(sorted(grouped.items(), key=lambda item: str(item[0]).casefold()))


def _folder_issue_label(actions: list[RestoreAction]) -> str:
    conflicts = sum(1 for a in actions if a.conflict)
    return _("Conflicts: {count}").format(count=conflicts) if conflicts else ""


def _folder_action_summary(actions: list[RestoreAction]) -> str:
    counts: dict[str, int] = {}
    for action in actions:
        label = _plain_action_label(action)
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{label} {count}" for label, count in counts.items())
