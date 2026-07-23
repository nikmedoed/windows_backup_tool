import ctypes
import fnmatch
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _GitignoreRule:
    base: Path
    pattern: str
    negated: bool
    directory_only: bool
    has_slash: bool
    anchored: bool


def gitignore_excludes(root: Path, preexcluded_patterns: Optional[list[str]] = None) -> list[str]:
    """Return existing paths ignored by all .gitignore files below *root*.

    The result is a one-time, minimal snapshot suitable for ``PathRule.excludes``:
    ignored directories are included once and are not traversed further.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []

    ignored: list[str] = []
    preexcluded = ExclusionMatcher(root, [], preexcluded_patterns or [])

    def visit(directory: Path, inherited: list[_GitignoreRule]) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = list(scanner)
        except (OSError, PermissionError):
            return

        gitignore_path = next(
            (Path(entry.path) for entry in entries if entry.name.casefold() == ".gitignore"),
            None,
        )
        local_rules = _read_gitignore(gitignore_path, root) if gitignore_path is not None else []
        rules = [*inherited, *local_rules] if local_rules else inherited

        for entry in entries:
            path = Path(entry.path)
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and (entry.name.casefold() == ".git" or preexcluded.skip(path)):
                continue
            if _gitignore_ignored(path, is_dir, root, rules):
                ignored.append(path.relative_to(root).as_posix())
                continue
            if is_dir and not is_reparse_or_symlink(path, entry):
                visit(path, rules)

    visit(root, [])
    return sorted(ignored, key=str.casefold)


def _read_gitignore(path: Path, root: Path) -> list[_GitignoreRule]:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []

    rules: list[_GitignoreRule] = []
    base = path.parent.relative_to(root)
    for raw in lines:
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(r"\#"):
            line = line[1:]
        negated = line.startswith("!") and not line.startswith(r"\!")
        if negated:
            line = line[1:]
        elif line.startswith(r"\!"):
            line = line[1:]
        directory_only = line.endswith("/")
        anchored = line.startswith("/")
        line = line.rstrip("/").lstrip("/")
        if not line:
            continue
        line = line.replace("\\ ", " ").replace("\\#", "#").replace("\\!", "!")
        rules.append(_GitignoreRule(
            base=base,
            pattern=line,
            negated=negated,
            directory_only=directory_only,
            has_slash="/" in line,
            anchored=anchored,
        ))
    return rules


def _gitignore_ignored(path: Path, is_dir: bool, root: Path, rules: list[_GitignoreRule]) -> bool:
    root_rel = path.relative_to(root)
    ignored = False
    for rule in rules:
        try:
            rel = root_rel.relative_to(rule.base).as_posix()
        except ValueError:
            continue
        if rule.directory_only and not is_dir:
            continue
        if rule.has_slash or rule.anchored:
            matched = _match_path_pattern(rel, rule.pattern.casefold())
        else:
            matched = fnmatch.fnmatchcase(path.name.casefold(), rule.pattern.casefold())
        if matched:
            ignored = not rule.negated
    return ignored


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
