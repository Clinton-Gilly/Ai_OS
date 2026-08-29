"""Application control: launch, close, and list running apps."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from typing import Any

from ..platform_win import is_windows, list_processes, run, which
from .base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill

# Friendly names people actually say, mapped to what Windows launches.
APP_ALIASES: dict[str, str] = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "notepad": "notepad.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "calculator": "calc.exe",
    "terminal": "wt.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
}


def resolve_app(name: str) -> str:
    return APP_ALIASES.get(name.strip().lower(), name.strip())


class AppSkill(Skill):
    name = "apps"
    description = "Open, close, and inspect applications."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="app_list_running",
                skill=self.name,
                description="List currently running processes.",
                risk=RiskLevel.READ_ONLY,
                handler=self.list_running,
                params=(
                    Param("filter", required=False, default="",
                          description="Only include processes whose name contains this text."),
                    Param("limit", type="integer", required=False, default=40,
                          description="Maximum number of processes to return."),
                ),
                summarize=lambda a: "List running processes",
            ),
            ActionDef(
                name="app_open",
                skill=self.name,
                description=(
                    "Open an application by name, e.g. 'chrome', 'notepad', 'spotify'."
                ),
                risk=RiskLevel.REVERSIBLE,
                handler=self.open_app,
                params=(
                    Param("name", description="Application name or executable."),
                    Param("arguments", type="array", required=False,
                          description="Optional command-line arguments."),
                ),
                summarize=lambda a: f"Open {a['name']}",
            ),
            ActionDef(
                name="app_close",
                skill=self.name,
                description=(
                    "Close an application by name. Unsaved work in that app may be lost."
                ),
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.close_app,
                params=(
                    Param("name", description="Application or process name to close."),
                    Param("force", type="boolean", required=False, default=False,
                          description="Force termination instead of asking it to close."),
                ),
                summarize=lambda a: f"Close {a['name']}",
            ),
        )

    def list_running(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        needle = (args.get("filter") or "").lower()
        limit = int(args.get("limit") or 40)
        processes = [p for p in list_processes() if needle in p.name.lower()]
        processes.sort(key=lambda p: p.memory_kb, reverse=True)
        rows = [
            {"name": p.name, "pid": p.pid, "memory_kb": p.memory_kb}
            for p in processes[:limit]
        ]
        return ActionResult(
            True,
            f"{len(processes)} process(es) running"
            + (f" matching {needle!r}" if needle else "") + ".",
            {"processes": rows},
        )

    def open_app(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        target = resolve_app(args["name"])
        arguments = list(args.get("arguments") or [])
        if ctx.dry_run:
            return ActionResult(True, f"Would open {target}.", {"dry_run": True})
        try:
            if is_windows():
                if arguments:
                    subprocess.Popen([target, *arguments])
                else:
                    os.startfile(target)  # type: ignore[attr-defined]
            else:
                executable = which(target) or which(target.removesuffix(".exe"))
                if not executable:
                    return ActionResult(False, f"Could not find an executable for {target!r}.")
                subprocess.Popen([executable, *arguments])
        except OSError as exc:
            return ActionResult(False, f"Could not open {target}: {exc}")
        return ActionResult(True, f"Opened {target}.", {"target": target})

    def close_app(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        target = resolve_app(args["name"])
        force = bool(args.get("force"))
        if ctx.dry_run:
            verb = "force close" if force else "close"
            return ActionResult(True, f"Would {verb} {target}.", {"dry_run": True})
        if is_windows():
            command = ["taskkill", "/im", target]
            if force:
                command.append("/f")
        else:
            command = ["pkill", "-9" if force else "-15", "-f", target.removesuffix(".exe")]
        result = run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return ActionResult(False, f"Could not close {target}. {detail}".strip())
        return ActionResult(True, f"Closed {target}.", {"target": target})
