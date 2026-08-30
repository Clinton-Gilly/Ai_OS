"""Task scheduling through the Windows Task Scheduler.

Two separate capabilities, because they carry very different weight: a reminder
just shows a message, while scheduling a command means something will run later
without anyone watching. The second is destructive-tier for that reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from ..platform_win import is_windows, run
from .base import (
    ActionDef,
    ActionResult,
    ExecContext,
    Param,
    RiskLevel,
    Skill,
    UndoRecord,
)

TASK_PREFIX = "AiOS_"
SCHEDULES = ("once", "daily", "weekly", "hourly")
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def task_name(name: str) -> str:
    """Namespaced, so AI OS never edits a task it did not create."""
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "_")
    if not cleaned:
        cleaned = "task"
    return cleaned if cleaned.startswith(TASK_PREFIX) else TASK_PREFIX + cleaned


def parse_when(value: str) -> tuple[str, str]:
    """Turn "17:30" or "in 20 minutes" into schtasks (start_date, start_time)."""
    text = (value or "").strip().lower()
    # "at 17:30" and "17:30" mean the same thing; "in 20 minutes" keeps its "in".
    text = re.sub(r"^at\s+", "", text)
    now = datetime.now()

    relative = re.match(r"in\s+(\d+)\s*(minute|minutes|min|hour|hours|h|m)\b", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(hours=amount) if unit.startswith(("h",)) else \
            timedelta(minutes=amount)
        moment = now + delta
        return moment.strftime("%m/%d/%Y"), moment.strftime("%H:%M")

    clock = re.match(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?$", text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        meridiem = clock.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        moment = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if moment <= now:
            moment += timedelta(days=1)
        return moment.strftime("%m/%d/%Y"), moment.strftime("%H:%M")

    hour_only = re.match(r"(\d{1,2})\s*(am|pm)$", text)
    if hour_only:
        hour = int(hour_only.group(1)) % 12
        if hour_only.group(2) == "pm":
            hour += 12
        moment = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if moment <= now:
            moment += timedelta(days=1)
        return moment.strftime("%m/%d/%Y"), moment.strftime("%H:%M")

    raise ValueError(
        f"Could not read {value!r} as a time. Try '17:30', '5pm', or 'in 20 minutes'.")


def _powershell_message(text: str) -> str:
    """A reminder pops up a message box; no extra software needed."""
    safe = text.replace("'", "''")
    return (
        "powershell -WindowStyle Hidden -Command "
        "\"Add-Type -AssemblyName PresentationFramework; "
        f"[System.Windows.MessageBox]::Show('{safe}','AI OS reminder')\""
    )


class SchedulerSkill(Skill):
    name = "scheduler"
    description = "Schedule reminders and recurring tasks."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="schedule_list",
                skill=self.name,
                description="List the scheduled tasks AI OS created.",
                risk=RiskLevel.READ_ONLY,
                handler=self.list_tasks,
                summarize=lambda a: "List scheduled tasks",
            ),
            ActionDef(
                name="schedule_reminder",
                skill=self.name,
                description=(
                    "Schedule a reminder that shows a message at a given time. "
                    "Time can be '17:30', '5pm', or 'in 20 minutes'."
                ),
                risk=RiskLevel.REVERSIBLE,
                handler=self.create_reminder,
                reversible=True,
                params=(
                    Param("name", description="Short name for the reminder."),
                    Param("message", description="What the reminder should say."),
                    Param("when", description="When it should fire."),
                    Param("repeat", required=False, default="once", enum=SCHEDULES,
                          description="How often it repeats."),
                    Param("day", required=False, default="",
                          description="Day for a weekly repeat, e.g. MON."),
                ),
                summarize=lambda a: (
                    f"Remind you {a['when']}"
                    + (f" ({a.get('repeat')})" if a.get("repeat", "once") != "once" else "")
                    + f": {a['message']}"
                ),
            ),
            ActionDef(
                name="schedule_command",
                skill=self.name,
                description=(
                    "Schedule a command to run later, unattended. Use only when a "
                    "reminder will not do."
                ),
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.create_command,
                reversible=True,
                params=(
                    Param("name", description="Short name for the task."),
                    Param("command", description="The command line to run."),
                    Param("when", description="When it should run."),
                    Param("repeat", required=False, default="once", enum=SCHEDULES,
                          description="How often it repeats."),
                    Param("day", required=False, default="",
                          description="Day for a weekly repeat, e.g. MON."),
                ),
                summarize=lambda a: f"Run {a['command']!r} at {a['when']}",
            ),
            ActionDef(
                name="schedule_delete",
                skill=self.name,
                description="Delete a scheduled task that AI OS created.",
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.delete_task,
                params=(Param("name", description="Name of the task to delete."),),
                summarize=lambda a: f"Delete the scheduled task {a['name']}",
            ),
        )

    def undo_handlers(self) -> dict[str, Callable[[UndoRecord, ExecContext], ActionResult]]:
        return {"scheduler.create": self._undo_create}

    def _unsupported(self) -> ActionResult:
        return ActionResult(
            False,
            "Task scheduling uses the Windows Task Scheduler and is only "
            "available on Windows.",
        )

    def list_tasks(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        if not is_windows():
            return self._unsupported()
        result = run(["schtasks", "/query", "/fo", "csv", "/nh"])
        if result.returncode != 0:
            return ActionResult(False, "Could not read the task list.")
        tasks = []
        for line in result.stdout.splitlines():
            parts = [item.strip('"') for item in line.split('","')]
            if not parts or TASK_PREFIX not in parts[0]:
                continue
            tasks.append({
                "name": parts[0].lstrip("\\"),
                "next_run": parts[1] if len(parts) > 1 else "",
                "status": parts[2] if len(parts) > 2 else "",
            })
        if not tasks:
            return ActionResult(True, "AI OS has not scheduled any tasks.", {"tasks": []})
        return ActionResult(
            True,
            f"{len(tasks)} scheduled task(s): "
            + ", ".join(f"{t['name']} (next: {t['next_run']})" for t in tasks),
            {"tasks": tasks},
        )

    def create_reminder(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        return self._create(args, ctx, _powershell_message(args["message"]),
                            what=f"reminder {args['message']!r}")

    def create_command(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        return self._create(args, ctx, args["command"],
                            what=f"command {args['command']!r}")

    def _create(self, args: dict[str, Any], ctx: ExecContext, command: str,
                what: str) -> ActionResult:
        name = task_name(args["name"])
        repeat = (args.get("repeat") or "once").lower()
        try:
            start_date, start_time = parse_when(args["when"])
        except ValueError as exc:
            return ActionResult(False, str(exc))

        schedule = {"once": "ONCE", "daily": "DAILY", "weekly": "WEEKLY",
                    "hourly": "HOURLY"}.get(repeat, "ONCE")
        argv = ["schtasks", "/create", "/tn", name, "/tr", command,
                "/sc", schedule, "/st", start_time, "/f"]
        if schedule == "ONCE":
            argv += ["/sd", start_date]
        if schedule == "WEEKLY":
            day = (args.get("day") or "MON").upper()
            if day not in WEEKDAYS:
                return ActionResult(
                    False, f"{day!r} is not a weekday; use one of {', '.join(WEEKDAYS)}.")
            argv += ["/d", day]

        if ctx.dry_run:
            return ActionResult(
                True,
                f"Would schedule {name}: {what} at {start_time} ({repeat}).",
                {"dry_run": True, "task": name, "time": start_time,
                 "date": start_date, "repeat": repeat},
            )
        if not is_windows():
            return self._unsupported()

        result = run(argv)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return ActionResult(False, f"Could not schedule {name}. {detail}".strip())
        return ActionResult(
            True,
            f"Scheduled {name}: {what} at {start_time} ({repeat}).",
            {"task": name, "time": start_time, "repeat": repeat},
            UndoRecord("scheduler.create", {"task": name}, f"Delete the task {name}"),
        )

    def delete_task(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        name = task_name(args["name"])
        if ctx.dry_run:
            return ActionResult(True, f"Would delete the scheduled task {name}.",
                                {"dry_run": True})
        if not is_windows():
            return self._unsupported()
        result = run(["schtasks", "/delete", "/tn", name, "/f"])
        if result.returncode != 0:
            return ActionResult(False, f"Could not delete {name}; it may not exist.")
        return ActionResult(True, f"Deleted the scheduled task {name}.", {"task": name})

    def _undo_create(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        name = record.payload["task"]
        if not is_windows():
            return self._unsupported()
        result = run(["schtasks", "/delete", "/tn", name, "/f"])
        if result.returncode != 0:
            return ActionResult(False, f"Could not remove {name}.")
        return ActionResult(True, f"Removed the scheduled task {name}.")
