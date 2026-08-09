import json
import os
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Any

from src.exclusions import dedupe_strings, load_exclude_patterns

CONFIG_FILE = Path(os.getenv("APPDATA", ".")) / "BackupTool" / "config.json"
TARGET_SETTINGS_REL = Path(".backup_versions") / "settings.json"


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
    scheduled_zip_snapshots: bool = False
    retention_keep_successful_runs: int = 0
    exclude_patterns: List[str] = field(default_factory=list)
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
        if not isinstance(self.scheduled_zip_snapshots, bool):
            raise ValueError("Settings.scheduled_zip_snapshots must be bool")
        if (
                not isinstance(self.retention_keep_successful_runs, int)
                or self.retention_keep_successful_runs < 0
        ):
            raise ValueError("Settings.retention_keep_successful_runs must be a non-negative integer")
        if (
                not isinstance(self.exclude_patterns, list)
                or not all(isinstance(e, str) for e in self.exclude_patterns)
        ):
            raise ValueError("Settings.exclude_patterns must be List[str]")
        if self.last_success is not None and not isinstance(self.last_success, str):
            raise ValueError("Settings.last_success must be str or None")

    @classmethod
    def load(cls) -> Optional["Settings"]:
        if not CONFIG_FILE.exists():
            return None
        try:
            raw = CONFIG_FILE.read_text(encoding="utf-8")
            local = cls.from_payload(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"Failed to load config: {e}") from e

        # The local file identifies the active workspace. Its portable settings
        # are authoritative whenever the target is available (including when a
        # cloud client changed them on another machine).
        if local.target_dir:
            target_path = target_settings_file(local.target_dir)
            if target_path.is_file():
                return cls.load_from_target(local.target_dir)
        return local

    @classmethod
    def load_from_target(cls, target_dir: str | Path) -> Optional["Settings"]:
        path = target_settings_file(target_dir)
        if not path.exists():
            return cls._infer_from_target_index(target_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            settings = cls.from_payload(data)
            settings.target_dir = str(Path(target_dir).expanduser().resolve())
            # Repair settings files that were accidentally saved without
            # sources by older versions: the version index still knows them.
            if not settings.sources:
                inferred = cls._infer_from_target_index(target_dir)
                if inferred is not None:
                    settings.sources = inferred.sources
            return settings
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"Failed to load target settings: {e}") from e

    @classmethod
    def open_target(
            cls,
            target_dir: str | Path,
            current: "Settings",
    ) -> tuple["Settings", bool]:
        """
        Open a portable backup workspace.

        Existing workspace settings win. A new workspace receives a copy of the
        current settings, with only its target path changed.
        """
        loaded = cls.load_from_target(target_dir)
        if loaded is not None:
            # Normalize and heal portable metadata recovered from an old index
            # or an accidentally empty settings file.
            loaded.save_to_target(target_dir)
            return loaded, True

        seeded = cls.from_payload(asdict(current))
        seeded.target_dir = str(Path(target_dir).expanduser().resolve())
        seeded.save_to_target()
        return seeded, False

    @classmethod
    def _infer_from_target_index(cls, target_dir: str | Path) -> Optional["Settings"]:
        """Recover portable settings for workspaces created before settings.json."""
        target = Path(target_dir).expanduser().resolve()
        index_path = target / ".backup_versions" / "index.sqlite3"
        if not index_path.is_file():
            return None

        # Import lazily: version_store imports utilities which in turn import this
        # module, so a module-level import would create a cycle.
        from src.version_store import VersionStore

        store = VersionStore(target)
        try:
            roots = store.list_source_roots()
        except Exception as e:
            raise RuntimeError(f"Failed to infer target settings: {e}") from e
        finally:
            store.close()
        if not roots:
            return None
        return cls(
            target_dir=str(target),
            sources=[PathRule(source=root) for root in roots],
        )

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "Settings":
        if not isinstance(data, dict):
            raise ValueError(f"Settings payload must be an object, got {data!r}")
        sources, migrated_patterns = _load_sources(data.get("sources", []))
        return cls(
            target_dir=data["target_dir"],
            sources=sources,
            wait_on_finish=data.get("wait_on_finish", True),
            show_console=data.get("show_console", True),
            show_tray_icon=data.get("show_tray_icon", True),
            show_overlay=data.get("show_overlay", True),
            scheduled_zip_snapshots=data.get("scheduled_zip_snapshots", False),
            retention_keep_successful_runs=data.get("retention_keep_successful_runs", 0),
            exclude_patterns=dedupe_strings([
                *load_exclude_patterns(data.get("exclude_patterns", []), "Settings.exclude_patterns"),
                *migrated_patterns,
            ]),
            last_success=data.get("last_success"),
        )

    def save(self) -> None:
        _write_json_atomic(CONFIG_FILE, asdict(self))

    def save_to_target(self, target_dir: str | Path | None = None) -> Path:
        path = target_settings_file(target_dir or self.target_dir)
        _write_json_atomic(path, asdict(self))
        return path

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
        _write_json_atomic(CONFIG_FILE, payload)


def _load_sources(raw_sources: Any) -> tuple[list[PathRule], list[str]]:
    if not isinstance(raw_sources, list):
        raise ValueError(f"Settings.sources must be a list, got {raw_sources!r}")
    sources: list[PathRule] = []
    migrated_patterns: list[str] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid source rule: {raw!r}")
        sources.append(PathRule(
            source=raw["source"],
            excludes=raw.get("excludes", []),
        ))
        migrated_patterns.extend(
            load_exclude_patterns(raw.get("exclude_patterns", []), "PathRule.exclude_patterns")
        )
    return sources, migrated_patterns


def target_settings_file(target_dir: str | Path) -> Path:
    return Path(target_dir).expanduser().resolve() / TARGET_SETTINGS_REL


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a unique sibling file, then atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    print(Settings.load())
