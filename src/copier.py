import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from src.i18n import _
from .config import Settings, PathRule
from .utils import copy2, same_file


def _norm(p: str | Path) -> Path:
    p = Path(p)
    return Path(*p.parts) if p.parts and p.parts[0] == p.root else p


def _skip(rel: Path, excluded: set[Path]) -> bool:
    for e in excluded:
        if rel == e or rel.is_relative_to(e):
            return True
    return False


def _iter_files(rule: PathRule) -> Iterable[Path]:
    root = Path(rule.source).expanduser().resolve()
    excluded = {_norm(e) for e in rule.excludes}
    for cur, subdirs, files in os.walk(root, topdown=True):
        rel_dir = Path(cur).relative_to(root)
        if _skip(rel_dir, excluded):
            subdirs[:] = []
            continue
        subdirs[:] = [d for d in subdirs if not _skip(rel_dir / d, excluded)]
        for f in files:
            rel_file = rel_dir / f
            if _skip(rel_file, excluded):
                continue
            yield root / rel_file


@dataclass
class Stats:
    scanned: int = 0
    copied: int = 0
    unchanged: int = 0
    errors: int = 0

    def summary(self) -> str:
        return _("Scanned: {scanned} | Copied: {copied} | Unchanged: {unchanged} | Errors: {errors}").format(
            scanned=self.scanned, copied=self.copied, unchanged=self.unchanged, errors=self.errors
        )


Stage = Literal["scan", "analysis", "copy"]


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

    def _prog(i: int, total: int) -> None:
        if progress_cb:
            progress_cb(i, total)
        elif not have_tqdm:
            pct = int(i / total * 100)
            print(f"\r{_('Copying')}: {pct}% ({i}/{total})", end="", flush=True)

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

    tasks: list[tuple[Path, Path]] = []
    iterable = tqdm(all_files, desc=_("🛠 Analyzing files on changes…"), unit="file") if have_tqdm else all_files
    for src in iterable:
        rel_drive = src.drive.rstrip(":")
        rel_path = src.relative_to(src.anchor)
        dst = tgt_root / rel_drive / rel_path
        if same_file(src, dst, use_hash):
            stats.unchanged += 1
        else:
            tasks.append((src, dst))

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

    iterator = tqdm(tasks, desc=_("Copying files"), unit="file") if have_tqdm else enumerate(tasks, 1)

    if have_tqdm:
        for src, dst in iterator:
            try:
                copy2(src, dst)
                stats.copied += 1
            except Exception as exc:
                stats.errors += 1
                _log(_("❗ Error copying {src} → {dst} ({exc})")
                     .format(src=src, dst=dst, exc=exc))
    else:
        for i, (src, dst) in iterator:
            try:
                copy2(src, dst)
                stats.copied += 1
            except Exception as exc:
                stats.errors += 1
                _log(_("\n❗ Error copying {src} → {dst} ({exc})")
                     .format(src=src, dst=dst, exc=exc))
            _prog(i, num_tasks)
        if progress_cb is None:
            print()

    _log(stats.summary())

    if progress_cb:
        progress_cb(num_tasks, num_tasks)

    if stats.errors:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
        fname.write_text("", encoding="utf-8")
        _log(_("⚠️ Errors logged in: {0}").format(fname))
