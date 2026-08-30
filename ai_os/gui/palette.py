"""The command palette — a Spotlight-style "ask anything" bar.

It is deliberately thin: type a request, press Enter, and the request goes
through the same supervisor, safety engine, and approval queue as everything
else. Approvals surface in the main window, which the palette raises when one
is waiting.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

try:
    import tkinter as tk
    from tkinter import ttk

    TK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the Python build
    TK_AVAILABLE = False

PALETTE_WIDTH = 620
PALETTE_HEIGHT = 96


class CommandPalette:
    """A small, always-on-top entry bar bound to a submit callback."""

    def __init__(self, master: Any, on_submit: Callable[[str], None],
                 on_voice: Callable[[], str] | None = None) -> None:
        self.on_submit = on_submit
        self.on_voice = on_voice
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.title("AI OS")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)

        frame = ttk.Frame(self.window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Ask AI OS").pack(anchor="w")

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 0))
        self.entry = ttk.Entry(row, font=("Segoe UI", 13))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda _event: self.hide())
        if on_voice is not None:
            ttk.Button(row, text="🎤", width=3, command=self._speak).pack(
                side="left", padx=(6, 0))

        self.status = tk.StringVar(value="Enter to run, Esc to close")
        ttk.Label(frame, textvariable=self.status, foreground="#666").pack(
            anchor="w", pady=(6, 0))
        self.window.bind("<FocusOut>", self._on_focus_out)

    def toggle(self) -> None:
        if self.window.state() == "normal":
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self._centre()
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.entry.focus_force()
        self.entry.select_range(0, "end")

    def hide(self) -> None:
        self.window.withdraw()

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def _centre(self) -> None:
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = (screen_width - PALETTE_WIDTH) // 2
        y = int(screen_height * 0.28)
        self.window.geometry(f"{PALETTE_WIDTH}x{PALETTE_HEIGHT}+{x}+{y}")

    def _submit(self, _event: Any = None) -> None:
        request = self.entry.get().strip()
        if not request:
            return
        self.entry.delete(0, "end")
        self.hide()
        self.on_submit(request)

    def _speak(self) -> None:
        if self.on_voice is None:
            return
        self.set_status("Listening…")

        def worker() -> None:
            text = self.on_voice()
            self.window.after(0, lambda: self._voice_done(text))

        threading.Thread(target=worker, daemon=True).start()

    def _voice_done(self, text: str) -> None:
        if text:
            self.entry.delete(0, "end")
            self.entry.insert(0, text)
            self.set_status("Enter to run, Esc to close")
        else:
            self.set_status("Did not catch that")

    def _on_focus_out(self, _event: Any) -> None:
        # Clicking away dismisses the palette, the way a launcher should.
        if self.window.state() == "normal":
            self.hide()
