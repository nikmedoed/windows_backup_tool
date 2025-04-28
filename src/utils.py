import ctypes
import hashlib
import io
import os
import shutil
from pathlib import Path


def sha1(path: Path, buf_size: int = io.DEFAULT_BUFFER_SIZE * 16) -> str:
    """
    Compute SHA-1 digest of a file, using file_digest if available.
    """
    try:
        # Python 3.11+
        return hashlib.file_digest(path, 'sha1', buf_size).hex()
    except AttributeError:
        # Fallback for older versions
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


def dir_size(path: Path) -> int:
    """
    Calculate total size of a file or directory. Uses os.walk for reliability.
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path, topdown=True):
        for f in files:
            try:
                fp = Path(root) / f
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def _hide_console() -> None:
    """
    Hide the console window on Windows.
    """
    whnd = ctypes.windll.kernel32.GetConsoleWindow()
    ctypes.windll.user32.ShowWindow(whnd, 0)
