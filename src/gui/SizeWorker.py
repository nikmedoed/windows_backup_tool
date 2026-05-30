import os
from pathlib import Path

from PySide6 import QtCore

from src.config import PathRule
from src.utils import ExclusionMatcher


class SizeWorker(QtCore.QThread):
    sizeCalculated = QtCore.Signal(object)

    def __init__(self, sources: list[PathRule], exclude_patterns: list[str]):
        super().__init__()
        self.sources = sources
        self.exclude_patterns = exclude_patterns

    def run(self):
        total = 0
        for rule in self.sources:
            root = Path(rule.source).expanduser().resolve()
            if not root.exists():
                continue
            total += _rule_size(root, rule, self.exclude_patterns)
        self.sizeCalculated.emit(total)


def _rule_size(root: Path, rule: PathRule, exclude_patterns: list[str]) -> int:
    matcher = ExclusionMatcher(root, rule.excludes, exclude_patterns)
    total = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        if matcher.skip(cur):
            continue
        try:
            with os.scandir(cur) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if matcher.skip(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat().st_size
                        except OSError:
                            pass
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass
    return total
