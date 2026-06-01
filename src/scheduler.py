import subprocess
import sys
import csv
import io
from pathlib import Path

TASK_FOLDER = r"\BackupTool"
_NO_WINDOW_FLAGS = 0x08000000 if sys.platform == "win32" else 0

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
    Return scheduled BackupTool task keys with a single schtasks query.
    """
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", f"{TASK_FOLDER}\\*", "/FO", "CSV", "/NH"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_NO_WINDOW_FLAGS,
    )
    if result.returncode != 0:
        return set()

    names = set()
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            names.add(row[0].strip().casefold())

    return {
        key
        for key in TASKS
        if _full_name(key).casefold() in names
    }


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


def schedule(key: str, *, allow_on_battery: bool = True) -> None:
    """
    Creates or recreates a task, and optionally updates power settings
    to allow running on battery.
    """
    if exists(key):
        delete(key)

    exe = Path(sys.executable)
    script = Path(__file__).parent.parent / "main.py"
    if script.exists():
        action = f'"{exe}" "{script}" --backup'
    else:
        action = f'"{exe}" --backup'

    name, trigger = TASKS[key]
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
