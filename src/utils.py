import ctypes
import hashlib
import io
import math
import os
import shutil
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Final, Iterable, Optional

from src.config import PathRule

_MTIME_TOLERANCE: Final[float] = 2.0


def sha1(path: Path, buf_size: int = io.DEFAULT_BUFFER_SIZE * 16) -> str:
    """
    Compute SHA-1 digest of a file, using file_digest if available.
    """
    try:
        with path.open('rb') as f:
            return hashlib.file_digest(f, 'sha1').hexdigest()
    except AttributeError:
        h = hashlib.sha1()
        with path.open('rb') as f:
            while chunk := f.read(buf_size):
                h.update(chunk)
        return h.hexdigest()


def same_file(src: Path, dst: Path, use_hash: bool = False) -> bool:
    """
    Returns True if the destination file exists and matches the source.
    With `use_hash`, size and SHA-1 must match; otherwise size and a close
    modification time are used.

    Hash mode avoids both timestamp rounding false positives and same-size
    content changes with similar mtimes.

    Args:
        src (Path): Source file path.
        dst (Path): Destination file path.
        use_hash (bool): Whether to compare file hashes.

    Returns:
        bool: True if files are considered identical.
    """
    if not dst.exists():
        return False
    try:
        ss = src.stat()
        ds = dst.stat()
    except OSError:
        return False
    if use_hash:
        if ss.st_size != ds.st_size:
            return False
        return sha1(src) == sha1(dst)
    if ss.st_size != ds.st_size:
        return False
    if not math.isclose(ss.st_mtime, ds.st_mtime, abs_tol=_MTIME_TOLERANCE):
        return False
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


def iter_files(rule: PathRule, debug_log: Optional[Callable[[str], None]] = None) -> Iterable[Path]:
    """
    Generate all files under rule.source, excluding any paths in rule.excludes.
    If debug_log is provided, emit short traversal notes.
    """
    return _iter_files(rule, debug_log=debug_log)


class DebugLog:
    """Simple debug logger that writes short, structured notes to a file."""

    def __init__(self, enabled: bool = False, path: Optional[str | Path] = None):
        self.enabled = enabled
        self.path: Optional[Path] = None
        self._fh: Optional[io.TextIOBase] = None
        self.error: Optional[str] = None
        if not enabled:
            return

        target = self._resolve_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._fh = target.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            # Give up, report error to caller
            self.enabled = False
            self.error = f"Cannot open debug log at {target}: {exc}"
            return

        self.path = target
        self.log(f"# Backup debug log @ {datetime.now():%Y-%m-%d %H:%M:%S}")

    def _resolve_path(self, override: Optional[str | Path]) -> Path:
        if override:
            return Path(override).expanduser().resolve()

        # Always Desktop by default (user-visible)
        desktop = Path.home() / "Desktop"
        return desktop / f"backup_debug_{datetime.now():%Y%m%d_%H%M%S}.log"

    def log(self, message: str) -> None:
        if not self.enabled or not self._fh:
            return
        self._fh.write(message + "\n")

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            finally:
                self._fh = None


def _iter_files(rule: PathRule, debug_log: Optional[Callable[[str], None]] = None) -> Iterable[Path]:
    def _emit(event: str, detail: str) -> None:
        if debug_log:
            debug_log(f"{event}: {detail}")

    def _is_link(path: Path, entry: Optional[os.DirEntry] = None) -> bool:
        if os.name != "nt":
            if entry is not None:
                try:
                    return entry.is_symlink()
                except OSError:
                    return False
            return path.is_symlink()
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs == -1:
                return False
            return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        except Exception:
            if entry is not None:
                try:
                    return entry.is_symlink()
                except OSError:
                    return False
            return path.is_symlink()

    root = Path(rule.source).expanduser().resolve()
    if not root.exists():
        _emit("RULE_MISSING", str(root))
        return

    excluded_raw = [(root / Path(e)).absolute() for e in rule.excludes]
    excluded: list[Path] = []
    for ex in excluded_raw:
        if _is_link(ex):
            continue
        excluded.append(ex)

    def _skip(p: Path) -> bool:
        return any(p.is_relative_to(ex) for ex in excluded)

    def _fmt(p: Path) -> str:
        try:
            return str(p.relative_to(root)) or "."
        except ValueError:
            return str(p)

    _emit("RULE", str(root))
    if excluded:
        _emit("EXCLUDES", ", ".join(_fmt(e) for e in excluded))
    else:
        _emit("EXCLUDES", "-")

    stack = [root]
    while stack:
        cur = stack.pop()
        if _skip(cur):
            _emit("SKIP_EXCLUDED", _fmt(cur))
            continue
        _emit("ENTER", _fmt(cur))
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    path = Path(entry.path)
                    if _is_link(path, entry):
                        _emit("SKIP_LINK", _fmt(path))
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if _skip(path):
                            _emit("SKIP_EXCLUDED", _fmt(path))
                            continue
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False) and not _skip(path):
                        yield path
        except (PermissionError, FileNotFoundError) as exc:
            _emit("SKIP_INACCESSIBLE", f"{_fmt(cur)} [{type(exc).__name__}]")


@lru_cache(maxsize=None)
def dir_size(path: str | Path) -> int:
    """
    Однократный (lru-кэш) расчёт размера файла/каталога.
    """
    p = Path(path).expanduser().resolve()
    try:
        if p.is_file():
            return p.stat().st_size
    except OSError:
        return 0

    total = 0
    stack = [p]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        try:
                            total += e.stat().st_size
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


def notify_user(title: str, message: str, icon: int = 0x00000040) -> None:
    """
    Display a simple Windows message box (falls back to stdout elsewhere).
    """
    if sys.platform != "win32":
        print(f"{title}: {message}")
        return
    MB_TOPMOST = 0x00040000
    ctypes.windll.user32.MessageBoxW(None, message, title, icon | MB_TOPMOST)


def is_admin() -> bool:
    if sys.platform != "win32":
        import os
        return os.geteuid() == 0

    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except OSError:
        return False
