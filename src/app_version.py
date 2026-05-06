from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_VERSION = "0.0.0"
_NO_WINDOW_FLAGS = 0x08000000 if sys.platform == "win32" else 0


def _from_generated() -> str | None:
    try:
        from src._version_generated import VERSION as generated_version
    except ImportError:
        return None
    return str(generated_version).strip() or None


def _from_git() -> str | None:
    root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            creationflags=_NO_WINDOW_FLAGS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


VERSION = _from_generated() or _from_git() or DEFAULT_VERSION
