import ctypes
import hashlib
import io
import os
import shutil
from pathlib import Path
from typing import Iterable

from src.config import PathRule


def sha1(path: Path, buf_size: int = io.DEFAULT_BUFFER_SIZE * 16) -> str:
    """
    Compute SHA-1 digest of a file, using file_digest if available.
    """
    try:
        return hashlib.file_digest(path, 'sha1', buf_size).hex()
    except AttributeError:
        h = hashlib.sha1()
        with path.open('rb') as f:
            while chunk := f.read(buf_size):
                h.update(chunk)
        return h.hexdigest()


def same_file(src: Path, dst: Path, use_hash: bool = False) -> bool:
    """
    Quick check by size and mtime; optional SHA-1 if use_hash=True.
    """
    if not dst.exists():
        return False
    try:
        ss = src.stat()
        ds = dst.stat()
    except OSError:
        return False
    if ss.st_size != ds.st_size or int(ss.st_mtime) != int(ds.st_mtime):
        return False
    if use_hash:
        return sha1(src) == sha1(dst)
    return True


def copy2(src: Path, dst: Path) -> None:
    """
    Copy file metadata and content, creating parent dirs.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)


def human_readable(size: int) -> str:
    """
    Convert byte count to human-readable string.
    """
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def iter_files(rule: PathRule) -> Iterable[Path]:
    """
    Generate all files under rule.source, excluding any paths in rule.excludes.
    """
    root = Path(rule.source).expanduser().resolve()
    excludes = {root / Path(e) for e in rule.excludes}

    for cur, dirs, files in os.walk(root, topdown=True):
        rel_cur = Path(cur).relative_to(root)
        dirs[:] = [
            d for d in dirs
            if not any((rel_cur / d).is_relative_to(ex.relative_to(root)) for ex in excludes)
        ]
        for f in files:
            rel_f = rel_cur / f
            if any(rel_f.is_relative_to(ex.relative_to(root)) for ex in excludes):
                continue
            yield Path(cur) / f


def dir_size(path: Path) -> int:
    """
    Calculate total size of a file or directory, excluding inaccessible entries.
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat().st_size
                        except OSError:
                            pass
        except (PermissionError, FileNotFoundError):
            pass
    return total


def _hide_console() -> None:
    """
    Hide the console window on Windows.
    """
    whnd = ctypes.windll.kernel32.GetConsoleWindow()
    ctypes.windll.user32.ShowWindow(whnd, 0)
