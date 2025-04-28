import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, Optional

from src.i18n import _
from .config import Settings, PathRule
from .utils import copy2, same_file


@dataclass
class Stats:
    scanned: int = 0
    copied: int = 0
    unchanged: int = 0
    errors: int = 0
    _lock: Lock = Lock()

    def inc_scanned(self):
        with self._lock:
            self.scanned += 1

    def inc_copied(self):
        with self._lock:
            self.copied += 1

    def inc_unchanged(self):
        with self._lock:
            self.unchanged += 1

    def inc_errors(self):
        with self._lock:
            self.errors += 1

    def summary(self) -> str:
        return _(
            "Scanned: {scanned} | Copied: {copied} | Unchanged: {unchanged} | Errors: {errors}"
        ).format(
            scanned=self.scanned,
            copied=self.copied,
            unchanged=self.unchanged,
            errors=self.errors
        )


def _iter_files(rule: PathRule) -> Iterable[Path]:
    root = Path(rule.source).expanduser().resolve()
    excluded = {Path(e) for e in rule.excludes}

    def _walk(directory: Path, rel_dir: Path):
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    rel = rel_dir / entry.name
                    if any(rel == ex or ex in rel.parents for ex in excluded):
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        yield from _walk(path, rel)
                    elif entry.is_file(follow_symlinks=False):
                        yield path
        except PermissionError:
            return

    yield from _walk(root, Path())


def run_backup(
        cfg: Settings,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        use_hash: bool = False,
) -> None:
    def _log(msg: str) -> None:
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

    _log(_("🔍 Starting backup…"))
    tgt_root = Path(cfg.target_dir).expanduser().resolve()
    if not tgt_root.is_dir():
        _log(_("❌ Target directory \"{0}\" is not accessible").format(tgt_root))
        return

    try:
        from tqdm import tqdm
        have_tqdm = progress_cb is None and log_cb is None
    except ImportError:
        have_tqdm = False

    _log(_("📂 Scanning files…"))
    all_files: list[Path] = []
    for rule in cfg.sources:
        all_files.extend(_iter_files(rule))
    stats = Stats(scanned=len(all_files))

    _log(_("🛠 Analyzing files on changes…"))
    tasks: list[tuple[Path, Path]] = []
    if have_tqdm:
        iterable = tqdm(all_files, desc=_("🛠 Analyzing files on changes…"), unit="file")
        for src in iterable:
            rel_drive = src.drive.rstrip(":")
            rel_path = src.relative_to(src.anchor)
            dst = tgt_root / rel_drive / rel_path
            if same_file(src, dst, use_hash):
                stats.inc_unchanged()
            else:
                tasks.append((src, dst))
    else:
        for i, src in enumerate(all_files, 1):
            _prog(i, stats.scanned)
            rel_drive = src.drive.rstrip(":")
            rel_path = src.relative_to(src.anchor)
            dst = tgt_root / rel_drive / rel_path
            if same_file(src, dst, use_hash):
                stats.inc_unchanged()
            else:
                tasks.append((src, dst))
        if not progress_cb:
            print()

    num_tasks = len(tasks)
    if num_tasks == 0:
        _log(_("✅ No changes detected. Backup not required."))
        _log(stats.summary())
        if progress_cb:
            progress_cb(0, 0)
        return

    _log(_("▶ {tasks} files to copy, {unchanged} unchanged")
         .format(tasks=num_tasks, unchanged=stats.unchanged))

    _prog(0, num_tasks)
    done = 0
    max_workers = min(8, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(copy2, src, dst): (src, dst) for src, dst in tasks}
        for future in as_completed(futures):
            src, dst = futures[future]
            try:
                future.result()
                stats.inc_copied()
            except Exception as exc:
                stats.inc_errors()
                _log(_("❗ Error copying {src} → {dst} ({exc})")
                     .format(src=src, dst=dst, exc=exc))
            done += 1
            _prog(done, num_tasks)

    _log(stats.summary())
    if progress_cb:
        progress_cb(num_tasks, num_tasks)

    if stats.errors:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
        fname.write_text("", encoding="utf-8")
        _log(_("⚠️ Errors logged in: {0}").format(fname))
