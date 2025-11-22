import atexit
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

from src.i18n import _
from .config import Settings
from .utils import copy2, same_file, iter_files, notify_user


@dataclass
class Stats:
    scanned: int = 0
    copied: int = 0
    unchanged: int = 0
    errors: int = 0
    _lock: Lock = Lock()

    def inc(self, field: str):
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)

    def summary(self) -> str:
        return _("Scanned: {scanned} | Copied: {copied} | "
                 "Unchanged: {unchanged} | Errors: {errors}").format(
            scanned=self.scanned,
            copied=self.copied,
            unchanged=self.unchanged,
            errors=self.errors
        )


def run_backup(
        cfg: Settings,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        use_hash: bool = False,
) -> bool:
    stats = Stats()

    error_messages: list[str] = []

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

    def _finalize(success: bool, message: Optional[str] = None) -> bool:
        if success:
            _mark_success()
            return True
        if message:
            notify_user(_("Backup error"), message, icon=0x00000010)
        return False

    _log(_("🔍 Starting backup…"))
    tgt_root = Path(cfg.target_dir).expanduser().resolve()
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
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    use_tqdm = (tqdm is not None and progress_cb is None and log_cb is None)

    _log(_("📂 Scanning files…"))
    all_files = [f for rule in cfg.sources for f in iter_files(rule)]
    stats.scanned = len(all_files)

    def _pause_console():
        if stats.errors:
            input(_("\n⚠️ Backup finished with errors. Press Enter to exit…"))
        elif cfg.wait_on_finish:
            print(_("\n✅ Backup completed successfully. Window will close in 10 seconds…"))
            time.sleep(10)

    if progress_cb is None and log_cb is None and cfg.wait_on_finish:
        atexit.register(_pause_console)
    tasks: list[tuple[Path, Path]] = []

    _log(_("🛠 Analyzing files on changes…"))
    iterator = (tqdm(all_files, desc=_("Analyzing…"), unit="file")
                if use_tqdm else all_files)

    for idx, src in enumerate(iterator, start=1):
        dst = tgt_root / src.drive.rstrip(":") / src.relative_to(src.anchor)
        if same_file(src, dst, use_hash):
            stats.inc("unchanged")
        else:
            tasks.append((src, dst))
        if not use_tqdm:
            _prog(idx, stats.scanned)
    if not use_tqdm and not progress_cb:
        print()

    if not tasks:
        _log(_("✅ No changes detected. Backup not required."))
        _log(stats.summary())
        if progress_cb:
            progress_cb(0, 0)
        return _finalize(True)
    _log(_("▶ {tasks} files to copy, {unchanged} unchanged")
         .format(tasks=len(tasks), unchanged=stats.unchanged))

    done = 0
    max_workers = min(8, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(copy2, src, dst): (src, dst) for src, dst in tasks}

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
                _prog(done, len(tasks))

    _log(stats.summary())
    if progress_cb:
        progress_cb(len(tasks), len(tasks))

    if stats.errors:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
        if progress_cb is None and log_cb is None:
            content = "\n".join(error_messages) if error_messages else _("No error details captured.")
            fname.write_text(content, encoding="utf-8")
            _log(_("⚠️ Errors logged in: {0}").format(fname), is_error=True)
            return _finalize(False, _("Backup finished with errors. See {0}").format(fname))
        return _finalize(False, _("Backup finished with errors."))

    return _finalize(True)
