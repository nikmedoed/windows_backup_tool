import argparse

from src.config import Settings
from src.copier import run_backup
from src.gui import open_gui


def main() -> None:
    p = argparse.ArgumentParser("Windows Backup Tool")
    p.add_argument(
        "--backup",
        action="store_true",
        help="Выполнить бэкап по конфигу (вызывается из задач планировщика)"
    )
    args = p.parse_args()

    if args.backup:
        cfg = Settings.load()
        if not cfg:
            raise SystemExit("No saved configuration, run GUI first.")
        run_backup(cfg)
    else:
        try:
            from elevate import elevate

            elevate()
        except ImportError:
            pass

        open_gui()


if __name__ == "__main__":
    main()
