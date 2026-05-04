from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.config import CONFIG_FILE

RELEASE_API_URL = "https://api.github.com/repos/nikmedoed/windows_backup_tool/releases/latest"
CHECK_INTERVAL_SECONDS = 12 * 60 * 60
FAILED_CHECK_INTERVAL_SECONDS = 60 * 60
PARENT_WAIT_TIMEOUT_SECONDS = 6 * 60 * 60
REPLACE_RETRY_SECONDS = 10 * 60
LOCK_STALE_SECONDS = PARENT_WAIT_TIMEOUT_SECONDS + REPLACE_RETRY_SECONDS + 60
HELPER_NAME = "BackupToolUpdater.exe"
USER_AGENT = "windows-backup-tool-updater"

UPDATE_DIR = CONFIG_FILE.parent / "updates"
STATE_FILE = UPDATE_DIR / "state.json"
METADATA_FILE = UPDATE_DIR / "staged.json"
LOCK_FILE = UPDATE_DIR / "updater.lock"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    digest: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    asset: ReleaseAsset


@dataclass(frozen=True)
class StagedUpdate:
    version: str
    path: Path


class UpdateLock:
    def __init__(self, path: Path = LOCK_FILE) -> None:
        self.path = path
        self._owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            is_stale = self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS
        except OSError:
            is_stale = False
        if is_stale:
            try:
                self.path.unlink()
            except OSError:
                return False
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        self._owned = True
        return True

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self._owned = False

    def __enter__(self) -> "UpdateLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def start_background_updater(current_version: str) -> None:
    """
    Start a detached helper copy of the frozen executable.

    The helper performs network I/O and waits until this process exits before
    replacing the original executable, so normal backup and GUI work is not
    delayed.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    target = Path(sys.executable).resolve()
    if target.name.casefold() == HELPER_NAME.casefold() or not target.exists():
        return

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    helper = UPDATE_DIR / HELPER_NAME
    if not _prepare_helper(target, helper):
        return

    creationflags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    args = [
        str(helper),
        "--background-update",
        "--parent-pid",
        str(os.getpid()),
        "--target-exe",
        str(target),
        "--current-version",
        current_version,
    ]
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError:
        return


def run_background_update(parent_pid: int, target_exe: str, current_version: str) -> int:
    target = Path(target_exe).resolve()
    if not target.name.lower().endswith(".exe"):
        return 0

    lock = UpdateLock()
    if not lock.acquire():
        return 0

    try:
        staged = find_staged_update(current_version)
        if staged is None and should_check_for_update():
            staged = _check_and_download(current_version)
        if staged is not None:
            _wait_for_process_exit(parent_pid, PARENT_WAIT_TIMEOUT_SECONDS)
            if _replace_with_retry(target, staged.path):
                _mark_applied(staged.version)
    finally:
        lock.release()
    return 0


def should_check_for_update(now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    state = _read_json(STATE_FILE)
    last_check = float(state.get("last_check_at", 0) or 0)
    last_error = float(state.get("last_error_at", 0) or 0)
    interval = FAILED_CHECK_INTERVAL_SECONDS if last_error >= last_check else CHECK_INTERVAL_SECONDS
    return now - last_check >= interval


def fetch_latest_release(current_version: str, *, timeout: int = 6) -> Optional[ReleaseInfo]:
    request = urllib.request.Request(
        RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return release_from_payload(payload, current_version)


def release_from_payload(payload: dict[str, Any], current_version: str) -> Optional[ReleaseInfo]:
    if payload.get("draft") or payload.get("prerelease"):
        return None
    version = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not is_newer_version(version, current_version):
        return None
    asset = select_release_asset(payload.get("assets", []))
    if asset is None:
        return None
    return ReleaseInfo(
        version=version,
        page_url=str(payload.get("html_url") or ""),
        asset=asset,
    )


def select_release_asset(assets: list[dict[str, Any]]) -> Optional[ReleaseAsset]:
    candidates: list[ReleaseAsset] = []
    for raw in assets:
        name = str(raw.get("name") or "")
        url = str(raw.get("browser_download_url") or "")
        if not name.lower().endswith(".exe") or not url:
            continue
        candidates.append(
            ReleaseAsset(
                name=name,
                url=url,
                size=int(raw.get("size") or 0),
                digest=str(raw.get("digest") or ""),
            )
        )
    if not candidates:
        return None

    def score(asset: ReleaseAsset) -> tuple[int, int]:
        lowered = asset.name.casefold()
        exact = 2 if lowered == "backuptool.exe" else 0
        named = 1 if "backuptool" in lowered or "backup" in lowered else 0
        return exact, named

    return sorted(candidates, key=score, reverse=True)[0]


def is_newer_version(remote: str, current: str) -> bool:
    remote_parts = _parse_version(remote)
    current_parts = _parse_version(current)
    if not remote_parts or not current_parts:
        return False
    width = max(len(remote_parts), len(current_parts))
    remote_norm = remote_parts + (0,) * (width - len(remote_parts))
    current_norm = current_parts + (0,) * (width - len(current_parts))
    return remote_norm > current_norm


def find_staged_update(current_version: str) -> Optional[StagedUpdate]:
    metadata = _read_json(METADATA_FILE)
    version = str(metadata.get("version") or "")
    file_name = str(metadata.get("file") or "")
    if not version or not file_name or not is_newer_version(version, current_version):
        return None
    path = UPDATE_DIR / file_name
    if path.exists():
        return StagedUpdate(version=version, path=path)
    return None


def _check_and_download(current_version: str) -> Optional[StagedUpdate]:
    _write_state(last_check_at=time.time())
    try:
        release = fetch_latest_release(current_version)
        if release is None:
            _write_state(last_error_at=None)
            return None
        state = _read_json(STATE_FILE)
        if state.get("installed_version") == release.version:
            _write_state(last_error_at=None)
            return None
        staged = _download_asset(release)
        _write_state(latest_version=release.version, update_pending=True, last_error_at=None)
        return staged
    except Exception as exc:
        _write_state(last_error_at=time.time(), last_error=str(exc)[:500])
        return None


def _download_asset(release: ReleaseInfo, *, timeout: int = 12) -> StagedUpdate:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", release.version).strip("._") or "latest"
    final = UPDATE_DIR / f"BackupTool-{safe_version}.exe"
    temp = final.with_suffix(".download")
    sha256 = hashlib.sha256()
    total = 0
    first = b""

    request = urllib.request.Request(
        release.asset.url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as fh:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            if not first:
                first = chunk[:2]
            sha256.update(chunk)
            total += len(chunk)
            fh.write(chunk)

    if first != b"MZ":
        temp.unlink(missing_ok=True)
        raise RuntimeError("downloaded asset is not a Windows executable")
    if release.asset.size and total != release.asset.size:
        temp.unlink(missing_ok=True)
        raise RuntimeError("downloaded asset size does not match release metadata")
    digest = release.asset.digest
    if digest.startswith("sha256:") and digest.removeprefix("sha256:").casefold() != sha256.hexdigest().casefold():
        temp.unlink(missing_ok=True)
        raise RuntimeError("downloaded asset checksum does not match release metadata")

    os.replace(temp, final)
    METADATA_FILE.write_text(
        json.dumps(
            {
                "version": release.version,
                "file": final.name,
                "sha256": sha256.hexdigest(),
                "release_url": release.page_url,
                "downloaded_at": time.time(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return StagedUpdate(version=release.version, path=final)


def _prepare_helper(target: Path, helper: Path) -> bool:
    try:
        if helper.exists() and helper.stat().st_size == target.stat().st_size:
            return True
        temp = helper.with_suffix(".tmp")
        shutil.copy2(target, temp)
        os.replace(temp, helper)
        return True
    except OSError:
        return helper.exists()


def _replace_with_retry(target: Path, staged: Path) -> bool:
    deadline = time.time() + REPLACE_RETRY_SECONDS
    while True:
        try:
            _replace_executable(target, staged)
            return True
        except OSError as exc:
            _write_state(last_apply_error=str(exc)[:500], last_apply_error_at=time.time())
            if time.time() >= deadline:
                return False
            time.sleep(3)


def _replace_executable(target: Path, staged: Path) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    previous = UPDATE_DIR / f"{target.stem}.previous.exe"
    temp_previous = UPDATE_DIR / f"{target.stem}.previous.tmp"
    temp_previous.unlink(missing_ok=True)
    previous.unlink(missing_ok=True)
    os.replace(target, temp_previous)
    try:
        os.replace(staged, target)
    except OSError:
        os.replace(temp_previous, target)
        raise
    os.replace(temp_previous, previous)


def _mark_applied(version: str) -> None:
    METADATA_FILE.unlink(missing_ok=True)
    _write_state(
        installed_version=version,
        update_pending=False,
        last_applied_at=time.time(),
        last_apply_error=None,
        last_apply_error_at=None,
    )


def _wait_for_process_exit(pid: int, timeout: int) -> None:
    if pid <= 0:
        return
    deadline = time.time() + timeout
    while time.time() < deadline and _process_exists(pid):
        time.sleep(2)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_version(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+){0,3}", value)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(**updates: Any) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    state = _read_json(STATE_FILE)
    for key, value in updates.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
