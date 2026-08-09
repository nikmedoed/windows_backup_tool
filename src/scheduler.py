import subprocess
import sys
import re
import xml.etree.ElementTree as ET
from pathlib import Path

TASK_FOLDER = r"\BackupTool"
_NO_WINDOW_FLAGS = 0x08000000 if sys.platform == "win32" else 0
WEEKDAYS = {
    "Monday": "MON", "Tuesday": "TUE", "Wednesday": "WED",
    "Thursday": "THU", "Friday": "FRI", "Saturday": "SAT", "Sunday": "SUN",
}

TASKS: dict[str, tuple[str, list[str]]] = {
    "daily": (
        "Backup_Daily",
        ["/SC", "DAILY", "/ST", "03:00"]
    ),
    "weekly": (
        "Backup_Weekly",
        ["/SC", "WEEKLY", "/D", "MON", "/ST", "03:00"]
    ),
    "onlogon": (
        "Backup_OnLogon",
        ["/SC", "ONLOGON"]
    ),
    "onidle": (
        "Backup_OnIdle",
        ["/SC", "ONIDLE", "/I", "20"]
    ),
    "onunlock": (
        "Backup_OnUnlock",
        ["/SC", "ONEVENT", "/EC", "Security", "/MO", "*[System[EventID=4801]]"]
    ),
}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, creationflags=_NO_WINDOW_FLAGS)


def _full_name(key: str) -> str:
    return f"{TASK_FOLDER}\\{TASKS[key][0]}"


def exists(key: str) -> bool:
    return subprocess.run(
        ["schtasks", "/Query", "/TN", _full_name(key)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW_FLAGS,
    ).returncode == 0


def existing_keys() -> set[str]:
    """
    Return the actual scheduled BackupTool task keys from Windows.

    Query every known task directly. A wildcard folder query is not reliable
    across Windows/schtasks versions and can make the UI report no tasks even
    though they exist.
    """
    return {key for key in TASKS if exists(key)}


def _trigger_xml(key: str) -> ET.Element | None:
    if key not in {"daily", "weekly"}:
        return None
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", _full_name(key), "/XML"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW_FLAGS,
    )
    if result.returncode != 0:
        return None
    try:
        payload = result.stdout.lstrip("\ufeff") if isinstance(result.stdout, str) else result.stdout
        return ET.fromstring(payload)
    except ET.ParseError:
        return None


def _trigger_details(key: str) -> dict[str, str]:
    root = _trigger_xml(key)
    if root is None:
        return {}
    details: dict[str, str] = {}
    for node in root.iter():
        local_name = node.tag.rsplit("}", 1)[-1]
        if local_name == "StartBoundary" and node.text:
            match = re.search(r"T(\d{2}):(\d{2})", node.text)
            if match:
                details["time"] = f"{match.group(1)}:{match.group(2)}"
        elif local_name in WEEKDAYS:
            details["weekday"] = WEEKDAYS[local_name]
    return details


def trigger_time(key: str) -> str | None:
    """Read a daily/weekly task's actual start time from Task Scheduler XML."""
    return _trigger_details(key).get("time")


def schedule_status() -> dict[str, dict[str, str]]:
    """Return actual tasks and editable trigger details from Windows."""
    status: dict[str, dict[str, str]] = {}
    for key in TASKS:
        if exists(key):
            status[key] = _trigger_details(key)
    return status


def delete(key: str) -> None:
    _run(["schtasks", "/Delete", "/TN", _full_name(key), "/F"])


def _apply_power_settings(key: str) -> None:
    """
    Disables 'Start only if on AC power' and 'Stop if on battery',
    and enables 'Start when available'.
    """
    task_name = TASKS[key][0]
    ps = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "$s = New-ScheduledTaskSettingsSet "
        "-AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries "
        "-StartWhenAvailable; "
        f"Set-ScheduledTask -TaskName '{task_name}' -TaskPath '{TASK_FOLDER}' -Settings $s"
    ]
    _run(ps)


def schedule(
        key: str,
        *,
        start_time: str | None = None,
        weekday: str | None = None,
        allow_on_battery: bool = True,
) -> None:
    """
    Creates or recreates a task, and optionally updates power settings
    to allow running on battery.
    """
    if key not in TASKS:
        raise ValueError(f"Unknown task key: {key!r}")

    name, default_trigger = TASKS[key]
    trigger = list(default_trigger)
    if start_time is not None and key not in {"daily", "weekly"}:
        raise ValueError(f"Task {key!r} does not support a start time")
    if weekday is not None and key != "weekly":
        raise ValueError(f"Task {key!r} does not support a weekday")
    if start_time is not None:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
            raise ValueError(f"Invalid task start time: {start_time!r}")
        trigger[trigger.index("/ST") + 1] = start_time
    if weekday is not None:
        weekday = weekday.upper()
        if weekday not in WEEKDAYS.values():
            raise ValueError(f"Invalid task weekday: {weekday!r}")
        trigger[trigger.index("/D") + 1] = weekday

    exe = Path(sys.executable)
    script = Path(__file__).parent.parent / "main.py"
    if script.exists():
        action = f'"{exe}" "{script}" --backup'
    else:
        action = f'"{exe}" --backup'

    _run([
        "schtasks", "/Create",
        "/TN", _full_name(key),
        "/TR", action,
        "/RL", "HIGHEST",
        *trigger,
        "/F"
    ])

    if allow_on_battery:
        _apply_power_settings(key)
