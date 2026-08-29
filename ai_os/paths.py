"""Filesystem locations used by AI OS.

Everything AI OS stores (config, audit database, undo trash) lives under a
single home directory so it can be inspected or deleted by the user.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "AI_OS_HOME"


def app_home() -> Path:
    """Return the AI OS data directory, creating it if needed."""
    override = os.environ.get(ENV_HOME)
    if override:
        home = Path(override).expanduser()
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        home = Path(base) / "AiOS"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        home = Path(base) / "ai-os"
    home.mkdir(parents=True, exist_ok=True)
    return home


def config_path() -> Path:
    return app_home() / "config.json"


def database_path() -> Path:
    return app_home() / "ai_os.sqlite3"


def trash_dir() -> Path:
    """Fallback trash used when no OS Recycle Bin is available."""
    path = app_home() / "trash"
    path.mkdir(parents=True, exist_ok=True)
    return path
