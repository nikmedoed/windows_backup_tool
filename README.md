# Windows Backup Tool

A lightweight Windows backup tool for selectively copying important settings and save files from `%AppData%`.

## Features

- Mirrors full file paths (`C:\Users\...\AppData\...` → `Target\C\Users\...`)
- Copies only new and modified files, skipping unchanged ones (based on size, modification time, optional SHA-1 hashing)
- Interactive GUI with checkbox tree for easy exclusion management
- Built-in scheduler supporting daily, weekly, on logon, idle, and unlock triggers
- Displays backup progress with a clear progress bar
- Comprehensive logging for backup results and errors without interrupting the backup
- Fast multi-threaded file copying

## Requirements

- Windows 7 or later
- No installation needed; simply run the provided executable (`BackupTool.exe`) or the script (`main.py`).

## Usage

### GUI Mode
Use the GUI for initial setup, selecting backup sources, specifying target folders, setting exclusions via an intuitive checkbox tree, and configuring Windows scheduled tasks:

```cmd
BackupTool.exe
```
or
```cmd
python main.py
```

The GUI is designed for initial setup and occasional manual backups. The primary method of regular backups is through the Windows Task Scheduler.

### Scheduled/Silent Mode
Once configured via the GUI, backups run automatically using Windows Task Scheduler:

```cmd
BackupTool.exe --backup
```
or
```cmd
python main.py --backup
```

The tool automatically creates scheduled tasks based on your settings. These tasks execute periodically according to your specified schedule.

## Localization
To update or manage translations, run the provided script:

```powershell
update_translations.ps1
```

## Building Executable

Use PyInstaller to create a standalone executable with administrative privileges:
```bash
pyinstaller --onefile --console --uac-admin --name BackupTool main.py
```

The resulting executable will be located in the `dist` folder.

## Logging
Backup logs and errors are stored in `backup_app.log`. Errors during copying are also logged separately to the Desktop with timestamps.

---

This tool is ready-to-use, fully functional, and optimized for performance and reliability.

