import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.config import PathRule
from src.exclusions import is_excluded_by_rule, nested_target_excludes
from src.utils import iter_files, same_file
from src.version_store import SnapshotItem, VersionStore, mirror_relative_for_source

_MTIME_TOLERANCE = 2.0


@dataclass(frozen=True)
class RestoreAction:
    action: str
    source_path: str
    target_path: Path
    content_path: Optional[Path]
    selected_size: Optional[int]
    selected_mtime: Optional[float]
    current_mtime: Optional[float]
    conflict: bool
    current_size: Optional[int] = None


@dataclass(frozen=True)
class RestorePlan:
    run_id: int
    scope_path: Path
    mode: str
    actions: list[RestoreAction]

    @property
    def copy_count(self) -> int:
        return sum(1 for a in self.actions if a.action in {"create", "replace", "export"})

    @property
    def delete_count(self) -> int:
        return sum(1 for a in self.actions if a.action == "delete")

    @property
    def create_count(self) -> int:
        return sum(1 for a in self.actions if a.action == "create")

    @property
    def replace_count(self) -> int:
        return sum(1 for a in self.actions if a.action == "replace")

    @property
    def export_count(self) -> int:
        return sum(1 for a in self.actions if a.action == "export")

    @property
    def conflict_count(self) -> int:
        return sum(1 for a in self.actions if a.conflict)


def build_restore_plan(
        store: VersionStore,
        run_id: int,
        scope_path: Path,
        *,
        mode: str,
        export_root: Optional[Path] = None,
        compare_contents: bool = True,
) -> RestorePlan:
    if mode not in {"original", "export"}:
        raise ValueError(f"Unsupported restore mode: {mode}")
    if mode == "export" and export_root is None:
        raise ValueError("export_root is required for export restore")

    scope_input = _absolute_path(scope_path)
    scope = scope_input.resolve()
    items = store.snapshot_for_scope(run_id, scope)
    return _build_restore_plan_from_items(
        store,
        run_id,
        scope_input,
        scope,
        items,
        mode=mode,
        export_root=export_root,
        compare_contents=compare_contents,
    )


def _build_restore_plan_from_items(
        store: VersionStore,
        run_id: int,
        scope_input: Path,
        scope: Path,
        items: list[SnapshotItem],
        *,
        mode: str,
        export_root: Optional[Path],
        compare_contents: bool,
) -> RestorePlan:
    if mode not in {"original", "export"}:
        raise ValueError(f"Unsupported restore mode: {mode}")
    if mode == "export" and export_root is None:
        raise ValueError("export_root is required for export restore")

    actions: list[RestoreAction] = []
    for item in items:
        if mode == "export":
            if item.state != "present":
                continue
            target = _export_target(Path(item.source_path), export_root)
            actions.append(_copy_action(
                store,
                item,
                target,
                action="export",
                skip_equal=False,
                compare_contents=compare_contents,
            ))
            continue

        target = _restore_target_for_item(item, scope_input, scope)
        if item.state == "present":
            action = _copy_action(store, item, target, skip_equal=True, compare_contents=compare_contents)
            if action is not None:
                actions.append(action)
        elif target_stat := _target_stat(target):
            actions.append(
                RestoreAction(
                    action="delete",
                    source_path=item.source_path,
                    target_path=target,
                    content_path=None,
                    selected_size=None,
                    selected_mtime=None,
                    current_mtime=target_stat.st_mtime,
                    conflict=False,
                    current_size=target_stat.st_size,
                )
            )
    return RestorePlan(run_id=run_id, scope_path=scope, mode=mode, actions=actions)


def build_restore_plan_for_sources(
        store: VersionStore,
        run_id: int,
        sources: list[object],
        *,
        mode: str,
        export_root: Optional[Path] = None,
        compare_contents: bool = True,
        exclude_patterns: Optional[list[str]] = None,
) -> RestorePlan:
    actions: list[RestoreAction] = []
    seen: set[tuple[str, str]] = set()
    for rule in sources:
        source = getattr(rule, "source", None)
        if not source:
            continue
        scope_input = _absolute_path(Path(source))
        scope = scope_input.resolve()
        items = store.snapshot_for_source_root(run_id, scope)
        if not items:
            items = store.snapshot_for_scope(run_id, scope)
        items = _filter_ignored_snapshot_items(rule, scope, items, exclude_patterns or [])
        plan = _build_restore_plan_from_items(
            store,
            run_id,
            scope_input,
            scope,
            items,
            mode=mode,
            export_root=export_root,
            compare_contents=compare_contents,
        )
        if mode == "original":
            plan.actions.extend(
                _untracked_current_file_delete_actions(
                    rule,
                    scope_input,
                    scope,
                    store.target_root,
                    items,
                    exclude_patterns or [],
                )
            )
        for action in plan.actions:
            key = (action.action, str(action.target_path).casefold())
            if key in seen:
                continue
            seen.add(key)
            actions.append(action)
    return RestorePlan(
        run_id=run_id,
        scope_path=Path("."),
        mode=mode,
        actions=actions,
    )


def _filter_ignored_snapshot_items(
        rule: object,
        root: Path,
        items: list[SnapshotItem],
        exclude_patterns: list[str],
) -> list[SnapshotItem]:
    if not items:
        return items
    path_rule = rule if isinstance(rule, PathRule) else PathRule(
        source=str(getattr(rule, "source")),
        excludes=list(getattr(rule, "excludes", [])),
    )
    return [
        item for item in items
        if not is_excluded_by_rule(Path(item.source_path), path_rule, root=root, exclude_patterns=exclude_patterns)
    ]


def _untracked_current_file_delete_actions(
        rule: object,
        root_input: Path,
        root: Path,
        target_root: Path,
        items: list[SnapshotItem],
        exclude_patterns: list[str],
) -> list[RestoreAction]:
    path_rule = rule if isinstance(rule, PathRule) else PathRule(
        source=str(getattr(rule, "source")),
        excludes=list(getattr(rule, "excludes", [])),
    )
    excludes = nested_target_excludes(path_rule.excludes, root, target_root)
    if excludes is not None:
        path_rule = PathRule(source=path_rule.source, excludes=excludes)
    known_paths = {str(Path(item.source_path).expanduser().resolve()).casefold() for item in items}
    actions: list[RestoreAction] = []
    for current in iter_files(path_rule, exclude_patterns=exclude_patterns):
        try:
            resolved_current = current.expanduser().resolve()
            if str(resolved_current).casefold() in known_paths:
                continue
            if not resolved_current.is_relative_to(root):
                continue
            rel = resolved_current.relative_to(root)
            target = root_input if str(rel) == "." else root_input / rel
            stat = resolved_current.stat()
        except OSError:
            continue
        actions.append(
            RestoreAction(
                action="delete",
                source_path=str(target),
                target_path=target,
                content_path=None,
                selected_size=None,
                selected_mtime=None,
                current_mtime=stat.st_mtime,
                conflict=False,
                current_size=stat.st_size,
            )
        )
    return actions


def apply_restore_plan(
        plan: RestorePlan,
        store: VersionStore,
        *,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    actions = _effective_actions(plan)
    total = len(actions)
    ok = True

    def _log(message: str) -> None:
        if log_cb:
            log_cb(message)
        else:
            print(message)

    if not actions:
        if progress_cb:
            progress_cb(0, 0)
        _log("Nothing changed")
        return True

    for idx, action in enumerate(actions, start=1):
        try:
            if action.action in {"create", "replace", "export"}:
                if action.content_path is None:
                    raise RuntimeError(f"No content path for {action.source_path}")
                if plan.mode == "original":
                    _archive_current_target(store, plan.run_id, action.target_path, log_cb=_log)
                _copy2_atomic(action.content_path, action.target_path)
                _log(f"{action.action.title()} {action.target_path}")
            elif action.action == "delete":
                if plan.mode == "original":
                    _archive_current_target(store, plan.run_id, action.target_path, log_cb=_log)
                action.target_path.unlink()
                _log(f"Deleted {action.target_path}")
        except Exception as exc:
            ok = False
            _log(f"Restore error for {action.target_path}: {exc}")
        if progress_cb:
            progress_cb(idx, total)
    return ok


def summarize_restore_plan(plan: RestorePlan, *, limit: int = 30) -> str:
    lines = [
        f"Files to create: {plan.create_count}",
        f"Files to replace: {plan.replace_count}",
        f"Files to delete: {plan.delete_count}",
        f"Files to export: {plan.export_count}",
        f"Potential conflicts: {plan.conflict_count}",
        "",
    ]
    for action in plan.actions[:limit]:
        marker = "!" if action.conflict else " "
        lines.append(f"{marker} {action.action.upper()} {action.target_path}")
    if len(plan.actions) > limit:
        lines.append(f"... and {len(plan.actions) - limit} more")
    return "\n".join(lines)


def _copy_action(
        store: VersionStore,
        item: SnapshotItem,
        target: Path,
        *,
        action: Optional[str] = None,
        skip_equal: bool,
        compare_contents: bool,
) -> Optional[RestoreAction]:
    content = store.content_path_for_snapshot(item)
    if content is None:
        return None
    target_stat = _target_stat(target)
    if skip_equal and target_stat is not None:
        if compare_contents:
            if same_file(content, target, use_hash=True) and _snapshot_matches_stat(item, target_stat):
                return None
        elif _snapshot_matches_stat(item, target_stat):
            return None
    resolved_action = action or ("replace" if target_stat is not None else "create")
    current_mtime = target_stat.st_mtime if target_stat is not None else None
    current_size = target_stat.st_size if target_stat is not None else None
    conflict = (
        current_mtime is not None
        and item.mtime is not None
        and current_mtime > item.mtime + _MTIME_TOLERANCE
    )
    return RestoreAction(
        action=resolved_action,
        source_path=item.source_path,
        target_path=target,
        content_path=content,
        selected_size=item.size,
        selected_mtime=item.mtime,
        current_mtime=current_mtime,
        conflict=conflict,
        current_size=current_size,
    )


def _export_target(source_path: Path, export_root: Optional[Path]) -> Path:
    assert export_root is not None
    return export_root.expanduser().resolve() / mirror_relative_for_source(source_path)


def _restore_target_for_item(item: SnapshotItem, scope_input: Path, resolved_scope: Path) -> Path:
    source = Path(item.source_path)
    try:
        rel = source.relative_to(resolved_scope)
    except ValueError:
        return source
    if str(rel) == ".":
        return scope_input
    return scope_input / rel


def _absolute_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return Path.cwd() / expanded


def _archive_current_target(
        store: VersionStore,
        selected_run_id: int,
        target_path: Path,
        *,
        log_cb: Callable[[str], None],
) -> None:
    archived = store.archive_restore_target(target_path, selected_run_id)
    if archived is not None:
        log_cb(f"Safety copy {target_path} -> {archived}")


def _copy2_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.restore_tmp_{os.getpid()}_{time.time_ns()}"
    try:
        shutil.copy2(src, tmp, follow_symlinks=False)
        os.replace(tmp, dst)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _target_stat(path: Path) -> Optional[os.stat_result]:
    try:
        return path.stat()
    except OSError:
        return None


def _snapshot_matches_stat(item: SnapshotItem, stat: os.stat_result) -> bool:
    if item.size is not None and stat.st_size != item.size:
        return False
    if item.mtime is None:
        return False
    return abs(stat.st_mtime - item.mtime) <= _MTIME_TOLERANCE


def _action_matches_stat(action: RestoreAction, stat: os.stat_result) -> bool:
    if action.selected_size is not None and stat.st_size != action.selected_size:
        return False
    if action.selected_mtime is None:
        return False
    return abs(stat.st_mtime - action.selected_mtime) <= _MTIME_TOLERANCE


def _effective_actions(plan: RestorePlan) -> list[RestoreAction]:
    if plan.mode == "export":
        return plan.actions
    result: list[RestoreAction] = []
    for action in plan.actions:
        if action.action in {"create", "replace"}:
            if action.content_path is None:
                continue
            target_stat = _target_stat(action.target_path)
            if (
                    target_stat is not None
                    and same_file(action.content_path, action.target_path, use_hash=True)
                    and _action_matches_stat(action, target_stat)
            ):
                continue
            result.append(action)
        elif action.action == "delete" and action.target_path.exists():
            result.append(action)
    return result
