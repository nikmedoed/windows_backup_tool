import atexit
import math
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

from src.i18n import _
from .config import PathRule, Settings
from .exclusions import is_excluded_by_rule, nested_target_excludes
from .utils import DebugLog, iter_files, notify_user, sha1
from .version_store import FileRecord, VersionStore, mirror_relative_for_source, source_roots_for_rules

_PROGRESS_LOG_INTERVAL_SECONDS = 5.0
_MTIME_TOLERANCE_SECONDS = 2.0


@dataclass
class Stats:
    scanned: int = 0
    copied: int = 0
    deleted: int = 0
    metadata_updated: int = 0
    unchanged: int = 0
    errors: int = 0
    _lock: Lock = field(default_factory=Lock)

    def inc(self, field: str):
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)

    def summary(self) -> str:
        return _("Scanned: {scanned} | Copied: {copied} | Deleted: {deleted} | "
                 "Metadata: {metadata} | Unchanged: {unchanged} | Errors: {errors}").format(
            scanned=self.scanned,
            copied=self.copied,
            deleted=self.deleted,
            metadata=self.metadata_updated,
            unchanged=self.unchanged,
            errors=self.errors
        )


def run_backup(
        cfg: Settings,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        use_hash: bool = False,
        debug: bool = False,
        debug_path: Optional[str] = None,
) -> bool:
    stats = Stats()

    error_messages: list[str] = []
    store: Optional[VersionStore] = None
    run_id: Optional[int] = None

    def _log(msg: str, *, is_error: bool = False) -> None:
        if is_error:
            error_messages.append(msg)
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    def _prog(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total)
        else:
            pct = int(done / total * 100) if total else 100
            print(f"\r{_('Progress')}: {pct}% ({done}/{total})", end="", flush=True)

    def _mark_success() -> None:
        ts = datetime.now().isoformat()
        cfg.last_success = ts
        Settings.patch(last_success=ts)

    def _write_error_report() -> Optional[Path]:
        desktop = Path.home() / "Desktop"
        try:
            desktop.mkdir(exist_ok=True)
            fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
            content = "\n".join(error_messages) if error_messages else _("No error details captured.")
            fname.write_text(content, encoding="utf-8")
            return fname
        except Exception as exc:
            _log(_("❗ Could not write error report: {exc}").format(exc=exc), is_error=True)
            return None

    def _apply_retention() -> None:
        keep = getattr(cfg, "retention_keep_successful_runs", 0)
        if not keep or store is None:
            return
        try:
            result = store.prune_successful_runs(keep)
        except Exception as exc:
            _log(_("⚠️ Could not prune old backup versions: {exc}").format(exc=exc), is_error=True)
            return
        if result.deleted_run_ids:
            _log(
                _("🧹 Removed old backup versions: {runs}; freed {bytes} bytes in {files} files").format(
                    runs=", ".join(str(run_id) for run_id in result.deleted_run_ids),
                    bytes=result.freed_bytes,
                    files=result.deleted_file_count,
                )
            )

    def _finalize(success: bool, message: Optional[str] = None) -> bool:
        if store is not None and run_id is not None:
            try:
                store.finish_run(run_id, success)
            except Exception as exc:
                _log(_("⚠️ Could not update version index: {exc}").format(exc=exc), is_error=True)
                success = False
                if message is None:
                    fname = _write_error_report()
                    message = (
                        _("Backup finished with errors. See {0}").format(fname)
                        if fname else _("Backup finished with errors.")
                    )
        if success:
            _apply_retention()
            _mark_success()
            return True
        if message:
            notify_user(_("Backup error"), message, icon=0x00000010)
        return False

    def _path_available(path: Path) -> bool:
        """
        Returns True if the path or its anchor exists (helps with removable drives).
        """
        anchor = Path(path.anchor) if path.anchor else path
        try:
            return path.exists() or anchor.exists()
        except OSError:
            return False

    def _wait_for_target(path: Path, timeout: float = 30.0) -> tuple[bool, bool]:
        """
        Wait for the target location to become available, up to timeout seconds.
        Returns (available, waited).
        """
        start = time.monotonic()
        notified = False
        while time.monotonic() - start <= timeout:
            if _path_available(path):
                return True, notified
            if not notified:
                _log(_("⌛ Waiting for target location to become available (up to {sec}s)…")
                     .format(sec=int(timeout)))
                notified = True
            time.sleep(1.5)
        return _path_available(path), notified

    dbg = DebugLog(enabled=debug, path=debug_path)
    if dbg.error:
        _log(_("⚠️ Debug log disabled: {err}").format(err=dbg.error))
    elif dbg.enabled and dbg.path:
        _log(_("🐞 Debug log: {path}").format(path=dbg.path))

    try:
        _log(_("🔍 Starting backup…"))
        tgt_root = Path(cfg.target_dir).expanduser().resolve()
        target_ready, was_waiting = _wait_for_target(tgt_root)
        if not target_ready:
            msg = _("Target \"{0}\" is not available").format(tgt_root)
            _log(_("❌ {msg}").format(msg=msg), is_error=True)
            return _finalize(False, msg)
        if was_waiting:
            _log(_("✅ Target is now available: {0}").format(tgt_root))

        if tgt_root.exists():
            if not tgt_root.is_dir():
                _log(_("❌ Target path \"{0}\" exists but is not a directory").format(tgt_root), is_error=True)
                return _finalize(False, _("Target path \"{0}\" is not a directory").format(tgt_root))
        else:
            try:
                tgt_root.mkdir(parents=True, exist_ok=True)
                _log(_("📁 Created target directory {0}").format(tgt_root))
            except Exception as e:
                _log(_("❌ Could not create target directory \"{0}\": {1}").format(tgt_root, e), is_error=True)
                return _finalize(False, _("Could not create target directory \"{0}\"").format(tgt_root))

        try:
            cfg.save_to_target(tgt_root)
        except Exception as exc:
            msg = _("Could not write backup settings to target \"{0}\": {1}").format(tgt_root, exc)
            _log(_("❌ {msg}").format(msg=msg), is_error=True)
            return _finalize(False, msg)

        store = VersionStore(tgt_root)

        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None

        use_tqdm = (tqdm is not None and progress_cb is None and log_cb is None)

        _log(_("📂 Scanning files…"))
        all_items: list[tuple[Path, Path]] = []
        scanned_keys: set[str] = set()
        duplicate_count = 0
        last_scan_log = time.monotonic()
        for rule in cfg.sources:
            source_root = Path(rule.source).expanduser().resolve()
            effective_rule = _rule_without_backup_target(rule, source_root, tgt_root, dbg=dbg)
            for file_path in iter_files(
                    effective_rule,
                    debug_log=dbg.log if dbg.enabled else None,
                    exclude_patterns=cfg.exclude_patterns,
            ):
                key = _path_key(file_path)
                if key in scanned_keys:
                    duplicate_count += 1
                    if dbg.enabled:
                        dbg.log(f"DUPLICATE_SOURCE_FILE: {file_path}")
                    continue
                scanned_keys.add(key)
                all_items.append((file_path, source_root))
                now = time.monotonic()
                if now - last_scan_log >= _PROGRESS_LOG_INTERVAL_SECONDS:
                    _log(_("📂 Scanned {count} files so far…").format(count=len(all_items)))
                    last_scan_log = now
        stats.scanned = len(all_items)
        scanned_sources = {_path_key(src) for src, _ in all_items}
        indexed_files = {_path_key(Path(record.source_path)): record for record in store.list_files()}
        successful_run_ids = store.successful_run_ids()
        _log(_("📋 Scan complete: {files} files found, {duplicates} duplicates skipped")
             .format(files=stats.scanned, duplicates=duplicate_count))
        _log(_("📚 Version index: {files} tracked files, {runs} successful runs")
             .format(files=len(indexed_files), runs=len(successful_run_ids)))

        def _pause_console():
            if stats.errors:
                input(_("\n⚠️ Backup finished with errors. Press Enter to exit…"))
            elif cfg.wait_on_finish:
                print(_("\n✅ Backup completed successfully. Window will close in 10 seconds…"))
                time.sleep(10)

        if progress_cb is None and log_cb is None and cfg.wait_on_finish:
            atexit.register(_pause_console)
        unchanged_items: list[tuple[Path, Path, Path, Path, Optional[str], bool]] = []
        index_update_items: list[tuple[Path, Path, Path, Path, Optional[str], bool]] = []
        metadata_update_items: list[tuple[Path, Path, Path, Path, str]] = []
        tasks: list[tuple[Path, Path, Path, Path]] = []
        delete_tasks: list[tuple[FileRecord, Path]] = []

        _log(_("🛠 Analyzing files on changes…"))
        iterator = (tqdm(all_items, desc=_("Analyzing…"), unit="file")
                    if use_tqdm else all_items)
        last_analysis_log = time.monotonic()

        for idx, (src, source_root) in enumerate(iterator, start=1):
            mirror_rel = mirror_relative_for_source(src)
            dst = tgt_root / mirror_rel
            record = indexed_files.get(_path_key(src))
            is_same, content_hash = _same_indexed_file(src, dst, record, use_hash)
            if is_same:
                force_version = _needs_successful_version(record, successful_run_ids)
                item = (src, source_root, mirror_rel, dst, content_hash, force_version)
                metadata_update_needed = (
                        record is not None
                        and record.state == "present"
                        and content_hash is not None
                        and _metadata_changed(src, record)
                )
                if metadata_update_needed:
                    metadata_update_items.append((src, source_root, mirror_rel, dst, content_hash))
                else:
                    stats.inc("unchanged")
                    unchanged_items.append(item)
                if not metadata_update_needed and (
                        record is None
                        or record.state != "present"
                        or record.current_hash is None
                        or force_version
                ):
                    index_update_items.append(item)
            else:
                tasks.append((src, source_root, mirror_rel, dst))
            if not use_tqdm:
                _prog(idx, stats.scanned)
            now = time.monotonic()
            if now - last_analysis_log >= _PROGRESS_LOG_INTERVAL_SECONDS:
                _log(_("🛠 Analyzed {done}/{total}: copy {copy}, unchanged {unchanged}")
                     .format(done=idx, total=stats.scanned, copy=len(tasks), unchanged=stats.unchanged))
                last_analysis_log = now
        if not use_tqdm and not progress_cb:
            print()

        configured_roots = source_roots_for_rules(cfg.sources)
        for record in store.list_present_files():
            source = Path(record.source_path)
            if _path_key(source) in scanned_sources:
                continue
            if not _belongs_to_roots(source, configured_roots):
                continue
            if _source_ignored_by_current_rules(source, cfg.sources, cfg.exclude_patterns):
                continue
            if _source_still_in_active_scope(source, cfg.sources, cfg.exclude_patterns):
                continue
            delete_tasks.append((record, tgt_root / Path(record.mirror_rel)))

        if not tasks and not delete_tasks and not index_update_items and not metadata_update_items:
            _log(_("✅ No changes detected. Backup not required."))
            _log(stats.summary())
            if progress_cb:
                progress_cb(0, 0)
            _log(_("✅ Backup completed successfully."))
            return _finalize(True)

        run_id = store.begin_run()
        if index_update_items:
            _log(_("🧾 Version index updates needed for {count} unchanged files")
                 .format(count=len(index_update_items)))
        for src, source_root, mirror_rel, dst, content_hash, force_version in index_update_items:
            try:
                store.record_seen(
                    src,
                    source_root,
                    mirror_rel,
                    dst,
                    run_id,
                    content_hash=content_hash,
                    force_version=force_version,
                )
            except Exception as exc:
                stats.inc("errors")
                _log(_("❗ Error updating version index for {src} ({exc})").format(
                    src=src, exc=exc), is_error=True)
        _log(_("▶ {tasks} files to copy, {deleted} files to delete, {unchanged} unchanged")
                 .format(tasks=len(tasks), deleted=len(delete_tasks), unchanged=stats.unchanged))

        done = 0
        total_work = len(tasks) + len(delete_tasks) + len(metadata_update_items)
        for src, source_root, mirror_rel, dst, content_hash in metadata_update_items:
            try:
                _record_metadata_update(store, run_id, src, source_root, mirror_rel, dst, content_hash)
                stats.inc("metadata_updated")
            except Exception as exc:
                stats.inc("errors")
                _log(_("❗ Error updating metadata for {src} ({exc})").format(
                    src=src, exc=exc), is_error=True)
            done += 1
            if not use_tqdm:
                _prog(done, total_work)

        max_workers = min(8, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _copy_versioned,
                    store,
                    run_id,
                    src,
                    source_root,
                    mirror_rel,
                    dst,
                ): (src, dst)
                for src, source_root, mirror_rel, dst in tasks
            }

            if use_tqdm:
                copy_iter = tqdm(
                    as_completed(futures),
                    total=len(tasks),
                    desc=_("Copying…"),
                    unit="file"
                )
            else:
                copy_iter = as_completed(futures)

            for future in copy_iter:
                src, dst = futures[future]
                try:
                    future.result()
                    stats.inc("copied")
                except Exception as exc:
                    stats.inc("errors")
                    _log(_("❗ Error copying {src} → {dst} ({exc})").format(
                        src=src, dst=dst, exc=exc), is_error=True)
                done += 1
                if not use_tqdm:
                    _prog(done, total_work)

        for record, dst in delete_tasks:
            try:
                archived_rel = store.prepare_archive_current(record.source_path, dst, run_id)
                if dst.exists():
                    dst.unlink()
                store.record_deleted(record, run_id, archived_rel)
                stats.inc("deleted")
            except Exception as exc:
                stats.inc("errors")
                _log(_("❗ Error deleting {dst} ({exc})").format(dst=dst, exc=exc), is_error=True)
            done += 1
            if not use_tqdm:
                _prog(done, total_work)

        _log(stats.summary())
        if progress_cb:
            progress_cb(total_work, total_work)

        if stats.errors:
            fname = _write_error_report()
            if fname:
                _log(_("⚠️ Errors logged in: {0}").format(fname))
                return _finalize(False, _("Backup finished with errors. See {0}").format(fname))
            return _finalize(False, _("Backup finished with errors."))

        _log(_("✅ Backup completed successfully."))
        return _finalize(True)
    finally:
        if store is not None:
            store.close()
        dbg.close()


def _copy_versioned(
        store: VersionStore,
        run_id: int,
        src: Path,
        source_root: Path,
        mirror_rel: Path,
        dst: Path,
) -> None:
    source_path = str(src.expanduser().resolve())
    archived_rel = store.prepare_archive_current(source_path, dst, run_id) if dst.exists() else None
    content_hash = sha1(src)
    _copy2_atomic(src, dst)
    store.record_copied(src, source_root, mirror_rel, dst, run_id, archived_rel, content_hash=content_hash)


def _record_metadata_update(
        store: VersionStore,
        run_id: int,
        src: Path,
        source_root: Path,
        mirror_rel: Path,
        dst: Path,
        content_hash: str,
) -> None:
    source_path = str(src.expanduser().resolve())
    archived_rel = store.prepare_archive_current(source_path, dst, run_id) if dst.exists() else None
    shutil.copystat(src, dst, follow_symlinks=False)
    store.record_copied(src, source_root, mirror_rel, dst, run_id, archived_rel, content_hash=content_hash)


def _copy2_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.backup_tmp_{os.getpid()}_{time.time_ns()}"
    try:
        shutil.copy2(src, tmp, follow_symlinks=False)
        os.replace(tmp, dst)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _same_indexed_file(
        src: Path,
        dst: Path,
        record: Optional[FileRecord],
        use_hash: bool,
) -> tuple[bool, Optional[str]]:
    if record is None or record.state != "present" or not record.current_hash:
        if not dst.exists():
            return False, None
        try:
            src_stat = src.stat()
            dst_stat = dst.stat()
        except OSError:
            return False, None
        if src_stat.st_size != dst_stat.st_size:
            return False, None
        if not use_hash and _mtime_close(src_stat.st_mtime, dst_stat.st_mtime):
            return True, None
        content_hash = sha1(src)
        return content_hash == sha1(dst), content_hash
    if not dst.exists():
        return False, None
    try:
        src_stat = src.stat()
        dst_stat = dst.stat()
    except OSError:
        return False, None
    if src_stat.st_size != record.current_size or dst_stat.st_size != record.current_size:
        return False, None
    if not use_hash and _mtime_close(src_stat.st_mtime, record.current_mtime):
        if _mtime_close(dst_stat.st_mtime, record.current_mtime):
            return True, record.current_hash
    content_hash = sha1(src)
    return content_hash == record.current_hash, content_hash


def _mtime_close(left: Optional[float], right: Optional[float]) -> bool:
    if left is None or right is None:
        return False
    return math.isclose(left, right, abs_tol=_MTIME_TOLERANCE_SECONDS)


def _metadata_changed(src: Path, record: FileRecord) -> bool:
    try:
        src_stat = src.stat()
    except OSError:
        return False
    return not _mtime_close(src_stat.st_mtime, record.current_mtime)


def _needs_successful_version(record: Optional[FileRecord], successful_run_ids: set[int]) -> bool:
    if record is None or record.state != "present":
        return False
    if record.last_seen_run_id is None:
        return True
    return int(record.last_seen_run_id) not in successful_run_ids


def _rule_without_backup_target(rule: PathRule, source_root: Path, target_root: Path, *, dbg: DebugLog) -> PathRule:
    excludes = nested_target_excludes(rule.excludes, source_root, target_root)
    if excludes is None:
        return rule
    if dbg.enabled:
        dbg.log(f"SKIP_BACKUP_TARGET_UNDER_SOURCE: {target_root}")
    return PathRule(source=rule.source, excludes=excludes)


def _belongs_to_roots(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path
    for root in roots:
        try:
            if resolved == root or resolved.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _source_still_in_active_scope(path: Path, sources: list[object], exclude_patterns: list[str]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    if not resolved.is_file():
        return False
    for rule in sources:
        source = getattr(rule, "source", None)
        if not source:
            continue
        try:
            root = Path(source).expanduser().resolve()
        except OSError:
            continue
        if not _same_or_child(resolved, root):
            continue
        if not is_excluded_by_rule(resolved, rule, root=root, exclude_patterns=exclude_patterns):
            return True
    return False


def _source_ignored_by_current_rules(path: Path, sources: list[object], exclude_patterns: list[str]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    for rule in sources:
        source = getattr(rule, "source", None)
        if not source:
            continue
        try:
            root = Path(source).expanduser().resolve()
        except OSError:
            continue
        if not _same_or_child(resolved, root):
            continue
        if is_excluded_by_rule(resolved, rule, root=root, exclude_patterns=exclude_patterns):
            return True
    return False


def _same_or_child(path: Path, root: Path) -> bool:
    path_s = str(path).rstrip("\\/").casefold()
    root_s = str(root).rstrip("\\/").casefold()
    return path_s == root_s or path_s.startswith(root_s + "\\") or path_s.startswith(root_s + "/")
