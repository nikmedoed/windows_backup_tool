import subprocess, sys
from pathlib import Path

# --- Константы -----------------------------------------------------------------
TASK_FOLDER = r"\BackupTool"

# ключ → (имя задачи, параметры триггера для schtasks /Create)
# для разблокировки используем Event-триггер на Security EventID=4801
TASKS: dict[str, tuple[str, list[str]]] = {
    "daily": (
        "Backup_Daily",
        ["/SC", "DAILY", "/ST", "03:00", "/IT"]
    ),
    "weekly": (
        "Backup_Weekly",
        ["/SC", "WEEKLY", "/D", "MON", "/ST", "03:00", "/IT"]
    ),
    "onlogon": (
        "Backup_OnLogon",
        ["/SC", "ONLOGON"]
    ),
    "onidle": (
        "Backup_OnIdle",
        ["/SC", "ONIDLE", "/I", "20", "/IT"]  # 20 мин простоя
    ),
    "onunlock": (
        "Backup_OnUnlock",
        [
            "/SC", "ONEVENT",
            "/EC", "Security",
            "/MO", "*[System[EventID=4801]]",
            "/IT"
        ]
    ),
}

def _full_name(key: str) -> str:
    return f"{TASK_FOLDER}\\{TASKS[key][0]}"

def _run_schtasks(args: list[str]) -> None:
    subprocess.run(["schtasks", *args], check=True)

def exists(key: str) -> bool:
    return subprocess.run(
        ["schtasks", "/Query", "/TN", _full_name(key)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0

def delete(key: str) -> None:
    _run_schtasks([
        "/Delete", "/TN", _full_name(key), "/F"
    ])

def _create(key: str) -> None:
    exe = Path(sys.executable)
    name, trigger = TASKS[key]
    _run_schtasks([
        "/Create",
        "/TN", _full_name(key),
        "/TR", f'"{exe}" --backup"',
        "/RL", "HIGHEST",
        *trigger
    ])

def schedule(key: str) -> None:
    """Создать задачу по ключу."""
    _create(key)
