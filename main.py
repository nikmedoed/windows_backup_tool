import argparse

from src.i18n import _


def main() -> None:
    p = argparse.ArgumentParser(_("Windows Backup Tool"))
    p.add_argument(
        "--backup",
        action="store_true",
        help=_("Run backup according to the saved configuration (called from the scheduler)")
    )

    args = p.parse_args()

    if args.backup:
        from src.config import Settings
        from src.copier import run_backup
        cfg = Settings.load()
        if not cfg:
            raise SystemExit(_("No saved configuration, run GUI first."))
        run_backup(cfg)
    else:
        try:
            from elevate import elevate

            elevate()
        except ImportError:
            pass
        from src.utils import _hide_console
        from src.gui import open_gui
        _hide_console()
        open_gui()


if __name__ == "__main__":
    main()
