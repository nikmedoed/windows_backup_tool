import argparse

from src.i18n import _
from src.utils import is_admin, _hide_console


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
        if not cfg.show_console:
            _hide_console()
        success = False
        if cfg.show_tray_icon:
            try:
                from src.tray import run_with_tray
                success = run_with_tray(cfg)
            except Exception as exc:
                print(_("Tray icon mode failed ({exc}). Falling back to console output.")
                      .format(exc=exc))
                success = run_backup(cfg)
        else:
            success = run_backup(cfg)
        raise SystemExit(0 if success else 1)
    else:
        try:
            if not is_admin():
                from elevate import elevate

                elevate(show_console=args.dev)
        except ImportError:
            pass
        from src.gui import open_gui
        _hide_console()
        open_gui()


if __name__ == "__main__":
    main()
