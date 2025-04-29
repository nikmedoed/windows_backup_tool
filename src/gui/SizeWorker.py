from pathlib import Path

from PySide6 import QtCore

from src.config import PathRule
from src.utils import dir_size


class SizeWorker(QtCore.QThread):
    sizeCalculated = QtCore.Signal(object)

    def __init__(self, sources: list[PathRule]):
        super().__init__()
        self.sources = sources

    def run(self):
        total = 0
        for rule in self.sources:
            root = Path(rule.source).expanduser().resolve()
            if not root.exists():
                continue
            root_sz = dir_size(root)
            for ex in rule.excludes:
                ex_path = root / ex
                if ex_path.exists():
                    root_sz -= dir_size(ex_path)
            total += max(root_sz, 0)
        self.sizeCalculated.emit(total)
