import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

CONFIG_FILE = Path(os.getenv("APPDATA", ".")) / "BackupTool" / "config.json"


@dataclass
class PathRule:
    source: str
    excludes: list[str] = field(default_factory=list)


@dataclass
class Settings:
    target_dir: str
    sources: list[PathRule] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings | None":
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return cls(
                target_dir=d["target_dir"],
                sources=[PathRule(**r) for r in d.get("sources", [])],
            )
        except FileNotFoundError:
            return None

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    print(Settings.load())
