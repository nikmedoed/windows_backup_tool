import argparse

from src.i18n import _
from src.utils import is_admin


def main() -> None:
    p = argparse.ArgumentParser(_("Windows Backup Tool"))
    p.add_argument(
        "--backup",
        action="store_true",
        help=_("Run backup according to the saved configuration (called from the scheduler)")
    )
    p.add_argument(
        "--dev",
        action="store_true",
        help=_("Run in development mode (if elevating, show console window)")
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
            if not is_admin():
                from elevate import elevate

                elevate(show_console=args.dev)
        except ImportError:
            pass
        from src.utils import _hide_console
        from src.gui import open_gui
        _hide_console()
        open_gui()


if __name__ == "__main__":
    main()
