# -*- mode: python ; coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
subprocess.run([sys.executable, str(ROOT / 'scripts' / 'write_version.py')], check=True, cwd=ROOT)
subprocess.run(
    [sys.executable, '-m', 'babel.messages.frontend', 'compile', '-d', 'locales', '-D', 'app'],
    check=True,
    cwd=ROOT,
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('locales', 'locales'), ('icon', 'icon')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BackupTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=['icon\\icon.ico'],
)
