import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List

CONFIG_FILE = Path(os.getenv("APPDATA", ".")) / "BackupTool" / "config.json"


@dataclass
class PathRule:
    source: str
    excludes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.source, str) or not self.source:
            raise ValueError(f"Invalid PathRule.source: {self.source!r}")
        if not isinstance(self.excludes, list) or not all(isinstance(e, str) for e in self.excludes):
            raise ValueError(f"Invalid PathRule.excludes: {self.excludes!r}")


@dataclass
class Settings:
    target_dir: str
    sources: List[PathRule] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.target_dir, str):
            raise ValueError(f"Settings.target_dir must be a string, got {type(self.target_dir).__name__}")
        if not isinstance(self.sources, list) or not all(isinstance(s, PathRule) for s in self.sources):
            raise ValueError(f"Settings.sources must be List[PathRule], got {self.sources!r}")

    @classmethod
    def load(cls) -> Optional["Settings"]:
        if not CONFIG_FILE.exists():
            return None
        try:
            raw = CONFIG_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
            return cls(
                target_dir=data["target_dir"],
                sources=[PathRule(**r) for r in data.get("sources", [])],
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"Failed to load config: {e}") from e

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    print(Settings.load())
