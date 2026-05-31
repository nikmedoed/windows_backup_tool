from PySide6 import QtCore

from src.config import PathRule
from src.utils import iter_files


class SizeWorker(QtCore.QThread):
    sizeCalculated = QtCore.Signal(object)

    def __init__(self, sources: list[PathRule], exclude_patterns: list[str]):
        super().__init__()
        self.sources = sources
        self.exclude_patterns = exclude_patterns

    def run(self):
        total = 0
        for rule in self.sources:
            for file_path in iter_files(rule, exclude_patterns=self.exclude_patterns):
                try:
                    total += file_path.stat().st_size
                except OSError:
                    pass
        self.sizeCalculated.emit(total)
