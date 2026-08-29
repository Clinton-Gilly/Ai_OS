"""Thin wrappers around the Windows-specific calls AI OS needs.

Each helper degrades gracefully off Windows so the rest of the codebase — and
the test suite — runs anywhere, while the real behaviour is used on Windows 11.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class UnsupportedPlatform(RuntimeError):
    """Raised when an action needs Windows and we are not on Windows."""


def is_windows() -> bool:
    return os.name == "nt"


def require_windows(what: str) -> None:
    if not is_windows():
        raise UnsupportedPlatform(f"{what} is only available on Windows.")


@dataclass
class ProcessInfo:
    name: str
    pid: int
    memory_kb: int = 0


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command without opening a console window on Windows."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if is_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, check=False, **kwargs)


def list_processes() -> list[ProcessInfo]:
    """Running processes, via psutil when available and tasklist otherwise."""
    try:
        import psutil  # type: ignore import-not-found

        processes = []
        for proc in psutil.process_iter(["name", "pid", "memory_info"]):
            info = proc.info
            memory = info.get("memory_info")
            processes.append(ProcessInfo(
                name=info.get("name") or "",
                pid=info.get("pid") or 0,
                memory_kb=int(memory.rss / 1024) if memory else 0,
            ))
        return processes
    except Exception:
        pass

    if is_windows():
        result = run(["tasklist", "/fo", "csv", "/nh"])
        processes = []
        for line in result.stdout.splitlines():
            parts = [item.strip('"') for item in line.split('","')]
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            memory = parts[4].replace(",", "").replace(" K", "").strip()
            processes.append(ProcessInfo(parts[0], pid, int(memory) if memory.isdigit() else 0))
        return processes

    result = run(["ps", "-eo", "comm=,pid=,rss="])
    processes = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].isdigit():
            processes.append(ProcessInfo(parts[0], int(parts[1]), int(parts[2])))
    return processes


def which(executable: str) -> str | None:
    return shutil.which(executable)
