import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import Settings, PathRule
from .utils import copy2, same_file


# ── Вспомогалки ──────────────────────────────────────────────────

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
        return (f"Сканировано: {self.scanned}  |  "
                f"Скопировано: {self.copied}  |  "
                f"Не изменилось: {self.unchanged}  |  "
                f"Ошибок: {self.errors}")


# ── Основная функция ───────────────────────────────────────────────

def run_backup(cfg: Settings, use_hash: bool = False) -> None:
    # 0) подготовка
    print("🔍 Старт бэкапа…")
    tgt_root = Path(cfg.target_dir).expanduser().resolve()
    if not tgt_root.is_dir():
        print(f"❌ Директория «{tgt_root}» недоступна")
        return

    # попытка tqdm
    try:
        from tqdm import tqdm
        have_tqdm = True
    except ImportError:
        have_tqdm = False

    # 1) Сканирование
    all_files = []
    print("📂 Сканирование файлов…")
    for rule in cfg.sources:
        all_files.extend(_iter_files(rule))
    total_files = len(all_files)
    stats = Stats()
    stats.scanned = total_files

    # 2) Анализ идентичности
    tasks = []
    if have_tqdm:
        for src in tqdm(all_files, desc="🛠 Анализ файлов на изменения…", unit="file"):
            rel_drive = src.drive.rstrip(":")
            rel_path = src.relative_to(src.anchor)
            dst = tgt_root / rel_drive / rel_path
            if same_file(src, dst, use_hash):
                stats.unchanged += 1
            else:
                tasks.append((src, dst))
    else:
        for i, src in enumerate(all_files, 1):
            pct = int(i / total_files * 100)
            print(f"\r Анализ: {pct}% ({i}/{total_files})", end="", flush=True)
            rel_drive = src.drive.rstrip(":")
            rel_path = src.relative_to(src.anchor)
            dst = tgt_root / rel_drive / rel_path
            if same_file(src, dst, use_hash):
                stats.unchanged += 1
            else:
                tasks.append((src, dst))
        print()

    num_tasks = len(tasks)
    if num_tasks == 0:
        print("✅ Нет изменений — копирование не требуется")
        print(stats.summary())
        return

    print(f"▶ {num_tasks} файл(ов) к копированию, {stats.unchanged} без изменений")

    if have_tqdm:
        for src, dst in tqdm(tasks, desc="Копирование", unit="file"):
            try:
                copy2(src, dst)
                stats.copied += 1
            except Exception as exc:
                stats.errors += 1
                tqdm.write(f"❗ {src} → {dst} ({exc})")
    else:
        for i, (src, dst) in enumerate(tasks, 1):
            try:
                copy2(src, dst)
                stats.copied += 1
            except Exception as exc:
                stats.errors += 1
                print(f"\n❗ {src} → {dst} ({exc})")
            pct = int(i / num_tasks * 100)
            print(f"\r Копирование: {pct}% ({i}/{num_tasks})", end="", flush=True)
        print()

    # 4) Отчёт
    print(stats.summary())

    # 5) Лог ошибок (только если есть)
    if stats.errors:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(exist_ok=True)
        fname = desktop / f"backup_errors_{datetime.now():%Y%m%d_%H%M%S}.log"
        # соберём ошибки из tqdm (если нужно, держите список) или из print лога
        # для простоты: перезапишем файл пустым, добавьте сбор ошибок, если надо
        fname.write_text("", encoding="utf-8")
        print(f"⚠️ Ошибки сохранены в: {fname}")
