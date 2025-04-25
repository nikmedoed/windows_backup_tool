import subprocess, sys
from pathlib import Path

TASK = "BackupToolTask"

def _exists() -> bool:
    return subprocess.run(
        ["schtasks", "/Query", "/TN", TASK],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0

def _schtasks(args: list[str]) -> None:
    subprocess.run(["schtasks", *args], check=True)

def _create(trigger: list[str]) -> None:
    exe = Path(sys.executable)
    _schtasks(
        ["/Create", "/TN", TASK,
         "/TR", f'"{exe}" --backup',
         "/RL", "HIGHEST", *trigger]
    )

def schedule_daily()  : _create(["/SC", "DAILY",  "/ST", "03:00", "/RI", "1440", "/IT"])
def schedule_weekly() : _create(["/SC", "WEEKLY", "/D", "MON", "/ST", "03:00", "/IT"])
def schedule_onstart(): _create(["/SC", "ONSTART"])
def schedule_onidle() : _create(["/SC", "ONIDLE"])          # «при пробуждении» ≈ при бездействии
def delete()          : _schtasks(["/Delete", "/TN", TASK, "/F"])
