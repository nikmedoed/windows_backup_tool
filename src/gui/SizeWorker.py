from pathlib import Path

from PySide6 import QtCore

from src.config import PathRule
from src.utils import dir_size


class SizeWorker(QtCore.QThread):
    sizeCalculated = QtCore.Signal(object)

    def __init__(self, sources: list[PathRule], cache: dict[Path, int]):
        super().__init__()
        self.sources = sources
        self.cache = cache

    def run(self):
        total = 0
        for rule in self.sources:
            root = Path(rule.source).expanduser().resolve()
            if not root.exists():
                continue
            if root not in self.cache:
                self.cache[root] = dir_size(root)
            root_size = self.cache[root]
            for ex in rule.excludes:
                ex_path = root / ex
                if ex_path.exists():
                    if ex_path not in self.cache:
                        self.cache[ex_path] = dir_size(ex_path)
                    root_size -= self.cache[ex_path]
            total += max(root_size, 0)
        self.sizeCalculated.emit(total)
