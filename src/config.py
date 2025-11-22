import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Any

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
    wait_on_finish: bool = True
    show_console: bool = True
    show_tray_icon: bool = True
    show_overlay: bool = True
    last_success: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.target_dir, str):
            raise ValueError(f"Settings.target_dir must be a string, got {type(self.target_dir).__name__}")
        if not isinstance(self.sources, list) or not all(isinstance(s, PathRule) for s in self.sources):
            raise ValueError(f"Settings.sources must be List[PathRule], got {self.sources!r}")
        if not isinstance(self.wait_on_finish, bool):
            raise ValueError("Settings.wait_on_finish must be bool")
        if not isinstance(self.show_console, bool):
            raise ValueError("Settings.show_console must be bool")
        if not isinstance(self.show_tray_icon, bool):
            raise ValueError("Settings.show_tray_icon must be bool")
        if not isinstance(self.show_overlay, bool):
            raise ValueError("Settings.show_overlay must be bool")
        if self.last_success is not None and not isinstance(self.last_success, str):
            raise ValueError("Settings.last_success must be str or None")

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
                wait_on_finish=data.get("wait_on_finish", True),
                show_console=data.get("show_console", True),
                show_tray_icon=data.get("show_tray_icon", True),
                show_overlay=data.get("show_overlay", True),
                last_success=data.get("last_success"),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"Failed to load config: {e}") from e

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @staticmethod
    def _read_payload() -> Optional[dict[str, Any]]:
        if not CONFIG_FILE.exists():
            return None
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    @classmethod
    def patch(cls, **updates: Any) -> None:
        payload = cls._read_payload()
        if payload is None:
            return
        payload.update({k: v for k, v in updates.items() if v is not None})
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    print(Settings.load())
