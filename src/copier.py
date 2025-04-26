from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Callable, Optional

from .config import Settings, PathRule
from .utils import log, copy2, same_file


# ---------- helpers -------------------------------------------------
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


# ---------- статистика ----------------------------------------------
@dataclass
class Stats:
    copied: int = 0
    unchanged: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (f"Скопировано: {self.copied}  |  "
                f"Не изменилось: {self.unchanged}  |  "
                f"Ошибок: {self.errors}")


# ---------- public API ----------------------------------------------
def run_backup(
        cfg: Settings,
        progress: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        use_hash: bool = False
) -> None:
    # 0. старт — собираем список
    log_cb and log_cb("🔍 Собираем список файлов...")
    tgt_root = Path(cfg.target_dir).expanduser().resolve()
    if not tgt_root.is_dir():
        msg = f"Target directory “{tgt_root}” недоступна"
        log.error(msg)
        log_cb and log_cb(f"❌ {msg}")
        return

    stats = Stats()
    tasks: list[tuple[Path, Path]] = []

    # 1. сканируем все файлы и сразу считаем, что уже есть
    for rule in cfg.sources:
        for src in _iter_files(rule):
            rel_drive = src.drive.rstrip(":")
            rel_path = src.relative_to(src.anchor)
            dst = tgt_root / rel_drive / rel_path
            if same_file(src, dst, use_hash):
                stats.unchanged += 1
            else:
                tasks.append((src, dst))

    total = len(tasks)

    # 2. анализ идентичности завершён
    log_cb and log_cb("🛠 Анализируем идентичность файлов...")
    if total == 0:
        log_cb and log_cb("✅ Нет новых или изменённых файлов — копирование не требуется")
        progress and progress(1, 1)
        return

    log_cb and log_cb(f"▶ {total} файл(ов) к копированию, "
                      f"{stats.unchanged} без изменений")

    # 3. автопрогресс для консоли
    if progress is None and sys.stdout.isatty():
        try:
            from tqdm import tqdm
            bar = tqdm(total=total, unit="file")
            def _p(*_): bar.update()
            progress = _p
        except ModuleNotFoundError:
            pass

    # 4. копирование
    errors: list[str] = []
    for i, (src, dst) in enumerate(tasks, 1):
        try:
            copy2(src, dst)
            stats.copied += 1
        except Exception as exc:
            msg = f"❗ERROR: {src} → {dst} ({exc})"
            log.error(msg)
            log_cb and log_cb(msg)
            errors.append(msg)
            stats.errors += 1
        if progress:
            progress(i, total)

    # 5. финал
    log_cb and log_cb("🔔 " + stats.summary())

    # 6. ошибки — в отдельный файл на Desktop
    if errors:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
        fname.write_text("\n".join(errors), encoding="utf-8")
        log_cb and log_cb(f"📄 Подробности ошибок сохранены: {fname}")
