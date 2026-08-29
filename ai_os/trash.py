"""Deleting to the Recycle Bin, with a reversible fallback elsewhere.

On Windows the real Recycle Bin is used (via send2trash if installed, otherwise
the shell API through ctypes). Anywhere else — and on Windows if both routes
fail — files move into the AI OS trash folder, which keeps deletes undoable
during development and in tests.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import trash_dir


@dataclass
class TrashResult:
    method: str            # "recycle-bin" | "ai-os-trash"
    restore_path: str | None   # where the copy lives, when we can restore it
    note: str = ""

    @property
    def restorable(self) -> bool:
        return self.restore_path is not None


def send_to_trash(path: Path) -> TrashResult:
    """Move ``path`` out of the way in the most reversible way available."""
    path = Path(path)
    if os.name == "nt":
        result = _windows_recycle(path)
        if result is not None:
            return result
    return _fallback_trash(path)


def _windows_recycle(path: Path) -> TrashResult | None:
    try:
        from send2trash import send2trash  # type: ignore import-not-found

        send2trash(str(path))
        return TrashResult(
            method="recycle-bin",
            restore_path=None,
            note="Restore from the Windows Recycle Bin.",
        )
    except Exception:
        pass
    try:
        return _shell_recycle(path)
    except Exception:
        return None


def _shell_recycle(path: Path) -> TrashResult:
    """SHFileOperationW with FOF_ALLOWUNDO — the Explorer delete behaviour."""
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = str(path.resolve()) + "\0\0"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0:
        raise OSError(f"SHFileOperationW failed with code {code}")
    return TrashResult(
        method="recycle-bin",
        restore_path=None,
        note="Restore from the Windows Recycle Bin.",
    )


def _fallback_trash(path: Path) -> TrashResult:
    destination = trash_dir() / f"{int(time.time() * 1000)}-{path.name}"
    shutil.move(str(path), str(destination))
    return TrashResult(
        method="ai-os-trash",
        restore_path=str(destination),
        note=f"Kept in the AI OS trash at {destination}.",
    )


def restore_from_trash(restore_path: str, original: str) -> None:
    source = Path(restore_path)
    target = Path(original)
    if not source.exists():
        raise FileNotFoundError(f"nothing to restore at {source}")
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
