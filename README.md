# Windows Backup Tool

A compact backup utility to mirror selected folders (e.g., `%AppData%`) into a target directory on Windows. It performs
incremental copies, supports exclusions, scheduling, and offers a simple GUI.

I created this tool to ensure that all my game and application save / config files on my GPD Win are backed up to an SD card, excluding unnecessary program folders so that my progress is always safe and easily restorable. 

Other utilities often copy everything blindly or create massive snapshots. You can always reinstall applications, and save files and settings take up only a few gigabytes, so why take snapshots? Moreover, %AppData% has become cluttered with junk from Electron apps. This tool lets you configure exclusions to copy only new and useful files.

---

## Features

- **Absolute-path mirroring**  
  Preserves original folder structure under the target (e.g. `C:\Users\Foo\AppData\…` → `Backup\C\Users\Foo\AppData\…`).
- **Incremental copies**  
  Skips unchanged files (by size & timestamp, with optional SHA‑1 checksum).
- **Exclusion dialog**  
  Checkbox‑tree UI to include/exclude files and folders.
- **Scheduler integration**  
  Create Windows Task Scheduler triggers: Daily, Weekly, On Logon, On Idle, On Unlock.
- **Multi‑threaded**  
  Concurrent file copying for speed.
- **Progress & logging**  
  Real‑time progress bar and detailed logs (without interrupting the run).
- **Zero‑install**  
  Run the `.exe` or Python script directly—no installer required.

---

## Requirements

- **Windows:** 7 or later
- **Python:** 3.7+
- **Dependencies:** listed in `requirements.txt`

---

## Quick Start

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
2. **Launch GUI**
   ```bash
   python main.py
   ```
    - Choose **Backup Target** and **Source** folders.
    - Configure **Exclusions** via the tree view.
    - Select **Schedule** triggers and click **Save**.

Settings are saved to `%AppData%\BackupTool\config.json`.

---

## CLI Mode

Run a backup using saved settings (for Task Scheduler or scripts):

```bash
python main.py --backup
```

---

## Scheduling

When you click **Save**, scheduled tasks are created/removed in Task Scheduler under the `BackupTool` folder:

- **Daily** @ 03:00
- **Weekly** (Mon @ 03:00)
- **On Logon**
- **On Idle** (20 min)
- **On Unlock**

Toggle these options in the GUI at any time.

---

## Localization

Translations are managed with Babel. To update:

```powershell
./update_translations.ps1
```

- `locales/app.pot`: template
- `locales/<lang>/LC_MESSAGES/*.po/.mo`: language files

---

## Building Executable

Generate a standalone `.exe`:

```bash
pyinstaller --onefile --uac-admin --name BackupTool --add-data "locales;locales" --add-data "icon;icon" --icon icon/icon.ico main.py
```

Find the result in `dist/BackupTool.exe`.

---

## Logs

- **No persistent log file**; monitor progress and messages in the GUI log window or console output
- **backup_errors_YYYYMMDD_HHMMSS.log** on Desktop if errors occur

---

## Development

To modify or extend the application:

- **Configuration storage** is in `%APPDATA%\BackupTool\config.json`. Models and serialization logic are defined in
  `src/config.py`.
- **Backup logic** is implemented in `src/copier.py`. To add new behaviors (e.g., checksum algorithms, custom filters),
  update the `run_backup()` function.
- **GUI components** reside in `src/gui/`:
    - `MainWindow.py` manages the main settings window and triggers.
    - `ExcludeDialog.py` handles exclusion tree and size calculation.
    - `SizeWorker.py` computes backup size in background.
- **Scheduling** lives in `src/scheduler.py`. Extend the `TASKS` dict and add corresponding checkboxes in
  `MainWindow._build_ui()` to support new triggers.
- **Localization** uses Babel and gettext. Wrap strings with `_()`. Update translations via `./update_translations.ps1`
  and edit `.po` files under `locales/`.
- **Executable build** relies on PyInstaller. The spec command is shown above.
- **Dependencies** are maintained in `requirements.txt`. Install with `pip install -r requirements.txt`.

Contributions, bug reports and feature requests are welcome via GitHub issues or pull requests.