import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.utils import same_file
from src.version_store import SnapshotItem, VersionStore, mirror_relative_for_source

_MTIME_TOLERANCE = 2.0


@dataclass(frozen=True)
class RestoreAction:
    action: str
    source_path: str
    target_path: Path
    content_path: Optional[Path]
    selected_mtime: Optional[float]
    current_mtime: Optional[float]
    conflict: bool


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
) -> RestorePlan:
    if mode not in {"original", "export"}:
        raise ValueError(f"Unsupported restore mode: {mode}")
    if mode == "export" and export_root is None:
        raise ValueError("export_root is required for export restore")

    scope = scope_path.expanduser().resolve()
    items = store.snapshot_for_scope(run_id, scope)
    actions: list[RestoreAction] = []
    for item in items:
        if mode == "export":
            if item.state != "present":
                continue
            target = _export_target(Path(item.source_path), export_root)
            actions.append(_copy_action(store, item, target, action="export", skip_equal=False))
            continue

        target = Path(item.source_path)
        if item.state == "present":
            action = _copy_action(store, item, target, skip_equal=True)
            if action is not None:
                actions.append(action)
        elif target.exists():
            actions.append(
                RestoreAction(
                    action="delete",
                    source_path=item.source_path,
                    target_path=target,
                    content_path=None,
                    selected_mtime=None,
                    current_mtime=_mtime(target),
                    conflict=True,
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
) -> RestorePlan:
    actions: list[RestoreAction] = []
    seen: set[tuple[str, str]] = set()
    for rule in sources:
        source = getattr(rule, "source", None)
        if not source:
            continue
        plan = build_restore_plan(
            store,
            run_id,
            Path(source),
            mode=mode,
            export_root=export_root,
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
) -> Optional[RestoreAction]:
    content = store.content_path_for_snapshot(item)
    if content is None:
        return None
    if skip_equal and target.exists() and same_file(content, target, use_hash=True):
        return None
    resolved_action = action or ("replace" if target.exists() else "create")
    current_mtime = _mtime(target)
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
        selected_mtime=item.mtime,
        current_mtime=current_mtime,
        conflict=conflict,
    )


def _export_target(source_path: Path, export_root: Optional[Path]) -> Path:
    assert export_root is not None
    return export_root.expanduser().resolve() / mirror_relative_for_source(source_path)


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


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _effective_actions(plan: RestorePlan) -> list[RestoreAction]:
    if plan.mode == "export":
        return plan.actions
    result: list[RestoreAction] = []
    for action in plan.actions:
        if action.action in {"create", "replace"}:
            if action.content_path is None:
                continue
            if action.target_path.exists() and same_file(action.content_path, action.target_path, use_hash=True):
                continue
            result.append(action)
        elif action.action == "delete" and action.target_path.exists():
            result.append(action)
    return result
