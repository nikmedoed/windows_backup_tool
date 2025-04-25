from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Callable, Optional

from .config import Settings, PathRule
from .utils import log, copy2, same_file


# ---------- helpers -------------------------------------------------
def _norm(p: str | Path) -> Path:  # /dir/ → dir   ;  .\foo → foo
    p = Path(p)
    return Path(*p.parts) if p.parts and p.parts[0] == p.root else p


def _skip(rel: Path, excluded: set[Path]) -> bool:
    """
    rel - путь *относительно корня источника*.
    Играем в  «начинается ли rel с одного из excluded».
    """
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
            subdirs[:] = []  # не углубляемся
            continue

        # фильтруем папки top-down, чтобы os.walk не посещал исключённые
        subdirs[:] = [d for d in subdirs if not _skip(rel_dir / d, excluded)]

        for f in files:
            rel_file = rel_dir / f
            if _skip(rel_file, excluded):
                continue
            yield root / rel_file


# ---------- public API ----------------------------------------------
def run_backup(
        cfg: Settings,
        progress: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        use_hash: bool = False
) -> None:
    tgt_root = Path(cfg.target_dir).expanduser().resolve()
    if not tgt_root.is_dir():
        msg = f"Target directory “{tgt_root}” недоступна";
        log.error(msg)
        log_cb and log_cb(msg)
        return

    tasks: list[tuple[Path, Path]] = []
    for rule in cfg.sources:
        for src in _iter_files(rule):
            rel_drive = src.drive.rstrip(":")  # “C”
            rel_path = src.relative_to(src.anchor)  # “Users/…”
            dst = tgt_root / rel_drive / rel_path
            if not same_file(src, dst, use_hash):
                tasks.append((src, dst))

    total = len(tasks)
    log_cb and log_cb(f"{total} файл(ов) к копированию")

    for i, (src, dst) in enumerate(tasks, 1):
        try:
            copy2(src, dst)
        except Exception as exc:
            log.error("copy %s → %s : %s", src, dst, exc)
            log_cb and log_cb(f"ERROR: {src} → {dst} ({exc})")
        if progress:
            progress(i, total)

    log_cb and log_cb("Готово")
