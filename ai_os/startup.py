"""Start-on-boot toggle.

On Windows this writes the HKCU Run key, which needs no elevation. Elsewhere it
reports that the toggle is unavailable rather than pretending to have worked.
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AiOS"


def _command() -> str:
    launcher = Path(sys.executable)
    # pythonw.exe keeps the console window from flashing on login.
    windowless = launcher.with_name("pythonw.exe")
    if windowless.exists():
        launcher = windowless
    return f'"{launcher}" -m ai_os gui'


def is_supported() -> bool:
    return sys.platform == "win32"


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg  # type: ignore import-not-found

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Returns (changed, message) so callers can report what actually happened."""
    if not is_supported():
        return False, "Start-on-boot is only available on Windows."
    import winreg  # type: ignore import-not-found

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
                return True, "AI OS will start when you sign in."
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                return False, "AI OS was not set to start on boot."
            return True, "AI OS will no longer start on boot."
    except OSError as exc:
        return False, f"Could not update the startup entry: {exc}"
