import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.i18n import _
from .config import PathRule, Settings
from .exclusions import nested_target_excludes
from .utils import iter_files


@dataclass(frozen=True)
class ZipArchiveResult:
    source: Path
    archive_path: Path
    file_count: int
    byte_count: int


@dataclass(frozen=True)
class ZipSnapshotResult:
    snapshot_dir: Path
    archives: list[ZipArchiveResult]
    errors: list[str]


def create_zip_snapshots(
        cfg: Settings,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
) -> ZipSnapshotResult:
    def _log(message: str) -> None:
        if log_cb:
            log_cb(message)
        else:
            print(message)

    def _progress(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total)

    target_root = Path(cfg.target_dir).expanduser().resolve()
    snapshot_dir = _unique_snapshot_dir(target_root / "zip_snapshots", datetime.now())
    errors: list[str] = []
    archives: list[ZipArchiveResult] = []

    _log(_("Starting zip snapshot..."))
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
    except Exception as exc:
        message = _("Could not create zip snapshot folder \"{path}\": {exc}").format(
            path=snapshot_dir,
            exc=exc,
        )
        _log(message)
        return ZipSnapshotResult(snapshot_dir=snapshot_dir, archives=[], errors=[message])
    try:
        cfg.save_to_target(target_root)
    except Exception as exc:
        message = _("Could not write backup settings to target \"{path}\": {exc}").format(
            path=target_root,
            exc=exc,
        )
        _log(message)
        return ZipSnapshotResult(snapshot_dir=snapshot_dir, archives=[], errors=[message])

    total_done = 0
    _progress(0, 0)
    used_archive_names: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="backup_zip_snapshot_") as staging_root_raw:
        staging_root = Path(staging_root_raw)
        for rule in cfg.sources:
            source_root = Path(rule.source).expanduser().resolve()
            if not source_root.exists():
                message = _("Source missing: {source}").format(source=source_root)
                errors.append(message)
                _log(message)
                continue
            if not source_root.is_dir():
                message = _("Source is not a directory: {source}").format(source=source_root)
                errors.append(message)
                _log(message)
                continue
            effective_rule = _rule_without_target(rule, source_root, target_root)
            archive_path = snapshot_dir / _archive_name_for_source(source_root, used_archive_names)
            staged_archive_path = staging_root / archive_path.name
            file_count = 0
            byte_count = 0
            _log(_("Creating archive: {archive}").format(archive=archive_path))
            try:
                with zipfile.ZipFile(
                        staged_archive_path,
                        "w",
                        compression=zipfile.ZIP_DEFLATED,
                        allowZip64=True,
                ) as zf:
                    for file_path in iter_files(effective_rule, exclude_patterns=cfg.exclude_patterns):
                        try:
                            rel = file_path.relative_to(source_root).as_posix()
                            zf.write(file_path, rel)
                            file_count += 1
                            byte_count += file_path.stat().st_size
                        except Exception as exc:
                            message = _("Could not add {path} to zip ({exc})").format(path=file_path, exc=exc)
                            errors.append(message)
                            _log(message)
                        total_done += 1
                        _progress(total_done, 0)
                _publish_staged_archive(staged_archive_path, archive_path)
            except Exception as exc:
                try:
                    if staged_archive_path.exists():
                        staged_archive_path.unlink()
                except OSError:
                    pass
                message = _("Could not create archive for {source} ({exc})").format(source=rule.source, exc=exc)
                errors.append(message)
                _log(message)
                continue
            archives.append(ZipArchiveResult(
                source=source_root,
                archive_path=archive_path,
                file_count=file_count,
                byte_count=byte_count,
            ))
            _log(_("Archive complete: {files} files -> {archive}").format(
                files=file_count,
                archive=archive_path,
            ))

    try:
        _write_manifest(snapshot_dir, archives, errors, cfg.exclude_patterns)
    except Exception as exc:
        message = _("Could not write zip manifest \"{path}\": {exc}").format(
            path=snapshot_dir / "manifest.json",
            exc=exc,
        )
        errors.append(message)
        _log(message)
    _progress(1, 1)
    if errors:
        _log(_("Zip snapshot finished with errors: {count}").format(count=len(errors)))
    else:
        _log(_("Zip snapshot completed: {count} archives in {folder}").format(
            count=len(archives),
            folder=snapshot_dir,
        ))
    return ZipSnapshotResult(snapshot_dir=snapshot_dir, archives=archives, errors=errors)


def _unique_snapshot_dir(root: Path, now: datetime) -> Path:
    base = root / now.strftime("%Y%m%d_%H%M%S")
    if not base.exists():
        return base
    idx = 2
    while True:
        candidate = root / f"{base.name}_{idx}"
        if not candidate.exists():
            return candidate
        idx += 1


def _archive_name_for_source(source_root: Path, used_names: set[str]) -> str:
    name = _safe_name(source_root.name or "source")
    candidate = f"{name}.zip"
    if candidate.casefold() not in used_names:
        used_names.add(candidate.casefold())
        return candidate
    idx = 2
    while True:
        candidate = f"{name}_{idx}.zip"
        if candidate.casefold() not in used_names:
            used_names.add(candidate.casefold())
            return candidate
        idx += 1


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return cleaned or "source"


def _publish_staged_archive(staged_archive_path: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_archive_path = archive_path.with_name(f".{archive_path.name}.tmp")
    try:
        shutil.copy2(staged_archive_path, tmp_archive_path)
        os.replace(tmp_archive_path, archive_path)
    finally:
        try:
            if tmp_archive_path.exists():
                tmp_archive_path.unlink()
        except OSError:
            pass


def _rule_without_target(rule: PathRule, source_root: Path, target_root: Path) -> PathRule:
    excludes = nested_target_excludes(rule.excludes, source_root, target_root)
    if excludes is None:
        return rule
    return PathRule(source=rule.source, excludes=excludes)


def _write_manifest(
        snapshot_dir: Path,
        archives: list[ZipArchiveResult],
        errors: list[str],
        exclude_patterns: list[str],
) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "exclude_patterns": exclude_patterns,
        "archives": [
            {
                "source": str(item.source),
                "archive": item.archive_path.name,
                "files": item.file_count,
                "bytes": item.byte_count,
            }
            for item in archives
        ],
        "errors": errors,
    }
    manifest_path = snapshot_dir / "manifest.json"
    tmp_manifest_path = snapshot_dir / ".manifest.json.tmp"
    tmp_manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_manifest_path.replace(manifest_path)
