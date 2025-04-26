import subprocess
import sys
from pathlib import Path

# Папка в планировщике, в которой будут лежать все наши задачи
TASK_FOLDER = r"\BackupTool"

# Имена задач внутри папки
TASK_NAMES = {
    "daily": "Backup_Daily",
    "weekly": "Backup_Weekly",
    "onstart": "Backup_OnStart",
    "onidle": "Backup_OnIdle",
}


def task_full_name(key: str) -> str:
    return f"{TASK_FOLDER}\\{TASK_NAMES[key]}"


def _schtasks(args: list[str]) -> None:
    subprocess.run(["schtasks", *args], check=True)


def exists(key: str) -> bool:
    """Проверить, есть ли задача с данным ключом"""
    return subprocess.run(
        ["schtasks", "/Query", "/TN", task_full_name(key)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def delete(key: str) -> None:
    """Удалить задачу по ключу"""
    _schtasks(["/Delete", "/TN", task_full_name(key), "/F"])


def _create(key: str, trigger: list[str]) -> None:
    exe = Path(sys.executable)
    _schtasks([
        "/Create",
        "/TN", task_full_name(key),
        "/TR", f'"{exe}" --backup',
        "/RL", "HIGHEST",
        *trigger
    ])


def schedule_daily() -> None: _create("daily", ["/SC", "DAILY", "/ST", "03:00", "/IT"])


def schedule_weekly() -> None: _create("weekly", ["/SC", "WEEKLY", "/D", "MON", "/ST", "03:00", "/IT"])


def schedule_onstart() -> None: _create("onstart", ["/SC", "ONSTART"])


def schedule_onidle() -> None:
    # запускать при простое >1 минуты в интерактивном режиме
    _create("onidle", ["/SC", "ONIDLE", "/I", "20", "/IT"])
