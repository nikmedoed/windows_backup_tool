import argparse

from src.app_version import VERSION
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
    p.add_argument(
        "--debug",
        nargs="?",
        const=True,
        default=False,
        metavar="[LOG_PATH]",
        help=_("Enable debug traversal log (optional path)")
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--background-update", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--parent-pid", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--target-exe", default="", help=argparse.SUPPRESS)
    p.add_argument("--current-version", default=VERSION, help=argparse.SUPPRESS)

    args = p.parse_args()

    if args.background_update:
        from src.updater import run_background_update

        raise SystemExit(run_background_update(args.parent_pid, args.target_exe, args.current_version))

    if args.backup:
        from src.updater import start_background_updater
        from src.config import Settings
        from src.copier import run_backup

        start_background_updater(VERSION)
        cfg = Settings.load()
        if not cfg:
            raise SystemExit(_("No saved configuration, run GUI first."))
        if not cfg.show_console:
            _hide_console()
        success = False
        if cfg.show_tray_icon:
            try:
                from src.tray import run_with_tray
                success = run_with_tray(cfg, debug=args.debug)
            except Exception as exc:
                print(_("Tray icon mode failed ({exc}). Falling back to console output.")
                      .format(exc=exc))
                success = run_backup(cfg, debug=bool(args.debug), debug_path=args.debug if isinstance(args.debug, str) else None)
        else:
            success = run_backup(cfg, debug=bool(args.debug), debug_path=args.debug if isinstance(args.debug, str) else None)
        raise SystemExit(0 if success else 1)
    else:
        try:
            if not is_admin():
                from elevate import elevate

                elevate(show_console=args.dev)
        except ImportError:
            pass
        from src.updater import start_background_updater
        from src.gui import open_gui

        start_background_updater(VERSION)
        _hide_console()
        open_gui(debug=bool(args.debug), debug_path=args.debug if isinstance(args.debug, str) else None)


if __name__ == "__main__":
    main()
