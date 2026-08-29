"""Read-only system information — the auto-run tier in practice."""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill


class SystemSkill(Skill):
    name = "system"
    description = "Report on the machine: OS, disk space, and the current time."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="system_info",
                skill=self.name,
                description="Report the operating system, machine name, and user.",
                risk=RiskLevel.READ_ONLY,
                handler=self.info,
                summarize=lambda a: "Read system information",
            ),
            ActionDef(
                name="system_disk_usage",
                skill=self.name,
                description="Report free and used disk space for a drive or folder.",
                risk=RiskLevel.READ_ONLY,
                handler=self.disk_usage,
                params=(
                    Param("path", required=False, default=str(Path.home()), is_path=True,
                          description="Drive or folder to measure."),
                ),
                summarize=lambda a: f"Check disk usage for {a.get('path', 'home')}",
            ),
            ActionDef(
                name="system_time",
                skill=self.name,
                description="Report the current local date and time.",
                risk=RiskLevel.READ_ONLY,
                handler=self.now,
                summarize=lambda a: "Read the current time",
            ),
        )

    def info(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        data = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "node": platform.node(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "python": platform.python_version(),
        }
        return ActionResult(True, f"{data['system']} {data['release']} on {data['node']}.", data)

    def disk_usage(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        target = Path(args.get("path") or Path.home()).expanduser()
        if not target.exists():
            return ActionResult(False, f"{target} does not exist.")
        usage = shutil.disk_usage(target)
        gb = 1024 ** 3
        return ActionResult(
            True,
            f"{usage.free / gb:.1f} GB free of {usage.total / gb:.1f} GB on {target}.",
            {
                "path": str(target),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            },
        )

    def now(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        stamp = datetime.now()
        return ActionResult(
            True,
            stamp.strftime("It is %A %d %B %Y, %H:%M local time."),
            {"iso": stamp.isoformat(timespec="seconds")},
        )
