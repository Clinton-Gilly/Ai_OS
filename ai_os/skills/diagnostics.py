"""System diagnostics — "why is my computer slow".

Everything here is read-only: it measures, explains what it found, and suggests
fixes. Acting on a suggestion is a separate, reviewable action.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..platform_win import is_windows, list_processes, run
from .base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill

MB = 1024 * 1024
GB = 1024 ** 3

# Thresholds that turn a measurement into advice.
LOW_DISK_FRACTION = 0.10          # under 10% free is worth flagging
HEAVY_PROCESS_MB = 1000           # a single process over ~1 GB stands out
MANY_PROCESSES = 250              # an unusually crowded process table
LONG_UPTIME_DAYS = 7


def _uptime_seconds() -> float | None:
    """Seconds since boot, or None if the platform will not say."""
    try:
        import psutil  # type: ignore import-not-found

        return time.time() - psutil.boot_time()
    except Exception:
        pass
    if is_windows():
        try:
            import ctypes

            return ctypes.windll.kernel32.GetTickCount64() / 1000.0
        except Exception:
            return None
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _drives() -> list[Path]:
    if is_windows():
        found = []
        for letter in "CDEFGH":
            candidate = Path(f"{letter}:/")
            if candidate.exists():
                found.append(candidate)
        return found or [Path.home()]
    return [Path("/")]


class DiagnosticsSkill(Skill):
    name = "diagnostics"
    description = "Investigate slow performance and low disk space."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="diag_report",
                skill=self.name,
                description=(
                    "Investigate why the machine is slow: memory-hungry processes, "
                    "disk space, uptime, and startup programs, with suggested fixes."
                ),
                risk=RiskLevel.READ_ONLY,
                handler=self.report,
                summarize=lambda a: "Run a system diagnostic",
            ),
            ActionDef(
                name="diag_top_processes",
                skill=self.name,
                description="List the processes using the most memory.",
                risk=RiskLevel.READ_ONLY,
                handler=self.top_processes,
                params=(
                    Param("limit", type="integer", required=False, default=10,
                          description="How many processes to return."),
                ),
                summarize=lambda a: "List the heaviest processes",
            ),
            ActionDef(
                name="diag_startup_items",
                skill=self.name,
                description="List the programs configured to start when you sign in.",
                risk=RiskLevel.READ_ONLY,
                handler=self.startup_items,
                summarize=lambda a: "List startup programs",
            ),
            ActionDef(
                name="diag_large_files",
                skill=self.name,
                description="Find the largest files under a folder.",
                risk=RiskLevel.READ_ONLY,
                handler=self.large_files,
                params=(
                    Param("path", is_path=True, description="Folder to search."),
                    Param("limit", type="integer", required=False, default=15,
                          description="How many files to return."),
                    Param("min_mb", type="integer", required=False, default=100,
                          description="Ignore files smaller than this many MB."),
                ),
                summarize=lambda a: f"Find large files under {a['path']}",
            ),
        )

    # -- the headline report --------------------------------------------
    def report(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        processes = sorted(list_processes(), key=lambda p: p.memory_kb, reverse=True)
        top = [
            {"name": p.name, "pid": p.pid, "memory_mb": round(p.memory_kb / 1024, 1)}
            for p in processes[:10]
        ]
        disks = []
        for drive in _drives():
            try:
                usage = shutil.disk_usage(drive)
            except OSError:
                continue
            disks.append({
                "path": str(drive),
                "free_gb": round(usage.free / GB, 1),
                "total_gb": round(usage.total / GB, 1),
                "free_fraction": round(usage.free / usage.total, 3) if usage.total else 0,
            })
        uptime = _uptime_seconds()
        startup = self.startup_items({}, ctx).data.get("items", [])

        findings: list[str] = []
        suggestions: list[str] = []

        for disk in disks:
            if disk["free_fraction"] < LOW_DISK_FRACTION:
                findings.append(
                    f"{disk['path']} is nearly full ({disk['free_gb']} GB free of "
                    f"{disk['total_gb']} GB)")
                suggestions.append(
                    f"Free space on {disk['path']}: ask me to find large files there, "
                    "or empty the Recycle Bin.")

        heavy = [p for p in top if p["memory_mb"] >= HEAVY_PROCESS_MB]
        if heavy:
            names = ", ".join(f"{p['name']} ({p['memory_mb']} MB)" for p in heavy[:3])
            findings.append(f"Memory-hungry processes: {names}")
            suggestions.append(
                f"Close what you are not using — ask me to close {heavy[0]['name']}.")

        if len(processes) > MANY_PROCESSES:
            findings.append(f"{len(processes)} processes are running, which is a lot")
            suggestions.append("Review your startup programs; many run unnoticed.")

        if uptime and uptime > LONG_UPTIME_DAYS * 86400:
            days = int(uptime // 86400)
            findings.append(f"The machine has been up for {days} days")
            suggestions.append("A restart clears leaked memory and applies updates.")

        if len(startup) > 12:
            findings.append(f"{len(startup)} programs are set to start at sign-in")
            suggestions.append("Trim startup programs to speed up logging in.")

        if not findings:
            findings.append("Nothing obviously wrong: disk, memory, and uptime all "
                            "look reasonable")

        message = "; ".join(findings) + "."
        if suggestions:
            message += " Suggested: " + " ".join(suggestions)
        return ActionResult(True, message, {
            "findings": findings,
            "suggestions": suggestions,
            "top_processes": top,
            "process_count": len(processes),
            "disks": disks,
            "uptime_seconds": round(uptime) if uptime else None,
            "startup_item_count": len(startup),
        })

    def top_processes(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        limit = int(args.get("limit") or 10)
        processes = sorted(list_processes(), key=lambda p: p.memory_kb, reverse=True)
        rows = [
            {"name": p.name, "pid": p.pid, "memory_mb": round(p.memory_kb / 1024, 1)}
            for p in processes[:limit]
        ]
        if not rows:
            return ActionResult(True, "No process information is available.",
                                {"processes": []})
        headline = ", ".join(f"{row['name']} {row['memory_mb']} MB" for row in rows[:5])
        return ActionResult(True, f"Heaviest processes: {headline}.",
                            {"processes": rows})

    def startup_items(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        items: list[dict[str, str]] = []
        if is_windows():
            items.extend(_registry_startup_items())
            startup_folder = Path(os.environ.get("APPDATA", "")) / (
                "Microsoft/Windows/Start Menu/Programs/Startup")
            if startup_folder.is_dir():
                items.extend(
                    {"name": entry.name, "source": "Startup folder",
                     "command": str(entry)}
                    for entry in startup_folder.iterdir() if entry.is_file()
                )
        else:
            result = run(["systemctl", "--user", "list-unit-files", "--state=enabled",
                          "--no-legend"])
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts:
                    items.append({"name": parts[0], "source": "systemd user unit",
                                  "command": ""})
        if not items:
            return ActionResult(True, "No startup programs found (or none readable).",
                                {"items": []})
        return ActionResult(
            True,
            f"{len(items)} startup item(s): "
            + ", ".join(item["name"] for item in items[:10]),
            {"items": items},
        )

    def large_files(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        folder = Path(args["path"]).expanduser()
        if not folder.is_dir():
            return ActionResult(False, f"{folder} is not a folder.")
        limit = int(args.get("limit") or 15)
        minimum = int(args.get("min_mb") or 100) * MB

        found: list[tuple[int, str]] = []
        for entry in folder.rglob("*"):
            try:
                if entry.is_file():
                    size = entry.stat().st_size
                    if size >= minimum:
                        found.append((size, str(entry)))
            except OSError:
                continue
        found.sort(reverse=True)
        rows = [{"path": path, "size_mb": round(size / MB, 1)}
                for size, path in found[:limit]]
        if not rows:
            return ActionResult(
                True,
                f"No files over {minimum // MB} MB under {folder}.",
                {"files": []})
        total = round(sum(size for size, _ in found) / GB, 2)
        return ActionResult(
            True,
            f"{len(found)} file(s) over {minimum // MB} MB under {folder}, "
            f"{total} GB in total. Largest: {rows[0]['path']} ({rows[0]['size_mb']} MB).",
            {"files": rows, "total_gb": total},
        )


def _registry_startup_items() -> list[dict[str, str]]:
    """HKCU and HKLM Run keys — where most startup programs register."""
    import winreg  # type: ignore import-not-found

    items: list[dict[str, str]] = []
    roots = (
        (winreg.HKEY_CURRENT_USER, "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    )
    for root, label in roots:
        try:
            with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    items.append({"name": name, "source": f"{label} Run",
                                  "command": str(value)})
                    index += 1
        except OSError:
            continue
    return items
