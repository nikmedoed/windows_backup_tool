import ctypes
import fnmatch
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_DEV_PATTERNS = [
    # OS metadata and thumbnail caches
    ".DS_Store",
    "Thumbs.db",
    "Desktop.ini",
    "ehthumbs.db",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".TemporaryItems",
    ".Trashes",

    # Editor and IDE project/cache folders
    ".idea",
    ".vscode",
    ".vs",
    "*.swp",
    "*.swo",
    "*~",

    # Python environments and caches
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".coverage",
    "htmlcov",
    "*.pyc",
    "*.pyo",
    "*.pyd",

    # JavaScript and web tooling
    "node_modules",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".vite",
    ".turbo",
    ".parcel-cache",

    # Common build output and package caches
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    ".cache",
    ".gradle",
    ".m2",
    "obj",

    # Temporary files
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.old",
]


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def load_exclude_patterns(raw: Any, field_name: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{field_name} must be List[str]")
    return list(raw)


def nested_target_excludes(excludes: list[str], source_root: Path, target_root: Path) -> Optional[list[str]]:
    try:
        rel = target_root.relative_to(source_root)
    except ValueError:
        return None
    rel_s = str(rel) if str(rel) else "."
    updated = list(excludes)
    if rel_s not in updated:
        updated.append(rel_s)
    return updated


def is_excluded_by_rule(
        path: Path,
        rule: object,
        *,
        root: Optional[Path] = None,
        exclude_patterns: Optional[list[str]] = None,
) -> bool:
    source = getattr(rule, "source", None)
    excludes = list(getattr(rule, "excludes", []))
    if root is None:
        if not source:
            return False
        root = Path(source).expanduser().resolve()
    matcher = ExclusionMatcher(root, excludes, exclude_patterns or [])
    return matcher.skip(path)


class ExclusionMatcher:
    def __init__(self, root: Path, excludes: list[str], patterns: list[str]):
        self.root = root
        self.excluded_paths = self._resolve_excluded_paths(excludes)
        self.patterns = [_normalize_pattern(p) for p in patterns if p.strip()]

    def skip(self, path: Path) -> bool:
        if any(path.is_relative_to(ex) for ex in self.excluded_paths):
            return True
        if not self.patterns:
            return False
        rel = _relative_posix(path, self.root)
        parts = rel.split("/") if rel else []
        for pattern in self.patterns:
            if "/" not in pattern:
                if any(fnmatch.fnmatchcase(part.casefold(), pattern) for part in parts):
                    return True
                continue
            if _match_path_pattern(rel, pattern):
                return True
        return False

    def describe_excludes(self) -> list[Path]:
        return self.excluded_paths

    def _resolve_excluded_paths(self, excludes: list[str]) -> list[Path]:
        resolved: list[Path] = []
        for ex in excludes:
            path = (self.root / Path(ex)).absolute()
            if is_reparse_or_symlink(path):
                continue
            resolved.append(path)
        return resolved


def is_reparse_or_symlink(path: Path, entry: Optional[os.DirEntry] = None) -> bool:
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


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/").strip("/").casefold()


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _match_path_pattern(rel: str, pattern: str) -> bool:
    return _match_parts(rel.casefold().split("/"), pattern.split("/"))


def _match_parts(path_parts: list[str], pattern_parts: list[str]) -> bool:
    if not pattern_parts:
        return not path_parts

    pattern = pattern_parts[0]
    if pattern == "**":
        return (
                _match_parts(path_parts, pattern_parts[1:])
                or (bool(path_parts) and _match_parts(path_parts[1:], pattern_parts))
        )

    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], pattern):
        return False
    return _match_parts(path_parts[1:], pattern_parts[1:])
