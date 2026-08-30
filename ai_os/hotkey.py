"""A global hotkey, so the command palette is one keystroke away.

On Windows this uses RegisterHotKey on a dedicated thread with its own message
loop — no extra dependency, and it works whatever has focus. Elsewhere it
reports that it is unavailable instead of pretending to have registered.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

# Windows modifier bits for RegisterHotKey.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN,
}

# Virtual-key codes for the keys a hotkey is likely to end in.
_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 13)})


class HotkeyError(RuntimeError):
    """The hotkey could not be parsed or registered."""


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    key_code: int
    text: str


def parse_hotkey(text: str) -> Hotkey:
    """Parse "ctrl+alt+space" into the modifier mask and virtual-key code."""
    parts = [part.strip().lower() for part in text.replace("-", "+").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise HotkeyError("Empty hotkey.")
    *modifier_names, key_name = parts

    modifiers = MOD_NOREPEAT
    for name in modifier_names:
        if name not in _MODIFIERS:
            raise HotkeyError(f"Unknown modifier {name!r} in {text!r}.")
        modifiers |= _MODIFIERS[name]
    if not modifier_names:
        raise HotkeyError(
            f"{text!r} has no modifier; a global hotkey needs at least one "
            "(for example ctrl+alt+space).")

    if key_name in _KEYS:
        key_code = _KEYS[key_name]
    elif len(key_name) == 1 and key_name.isalnum():
        key_code = ord(key_name.upper())
    else:
        raise HotkeyError(f"Unknown key {key_name!r} in {text!r}.")
    return Hotkey(modifiers, key_code, text)


def is_supported() -> bool:
    import sys

    return sys.platform == "win32"


class HotkeyListener:
    """Runs a Windows message loop on its own thread and fires a callback."""

    HOTKEY_ID = 1

    def __init__(self, hotkey: str, on_pressed: Callable[[], None]) -> None:
        self.hotkey = parse_hotkey(hotkey)
        self.on_pressed = on_pressed
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: str = ""
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> str:
        return self._error

    def start(self, timeout: float = 3.0) -> bool:
        """Start listening. Returns False and sets .error if it could not."""
        if not is_supported():
            self._error = "Global hotkeys are only available on Windows."
            return False
        if self._running:
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ai-os-hotkey")
        self._thread.start()
        self._ready.wait(timeout)
        return self._running

    def stop(self) -> None:
        """Ask the message loop to exit."""
        if not self._running or self._thread_id is None:
            return
        import ctypes

        WM_QUIT = 0x0012
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._running = False

    def _loop(self) -> None:  # pragma: no cover - needs a Windows message pump
        import ctypes
        from ctypes import wintypes

        WM_HOTKEY = 0x0312
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        if not user32.RegisterHotKey(None, self.HOTKEY_ID, self.hotkey.modifiers,
                                     self.hotkey.key_code):
            self._error = (f"Windows refused the hotkey {self.hotkey.text!r}; "
                           "another program probably owns it.")
            self._ready.set()
            return

        self._running = True
        self._ready.set()
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    try:
                        self.on_pressed()
                    except Exception as exc:
                        self._error = f"Hotkey handler failed: {exc}"
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self._running = False
