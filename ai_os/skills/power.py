"""Power control: shutdown, restart, sleep, lock, and cancel a pending shutdown."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..platform_win import is_windows, run
from .base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill


class PowerSkill(Skill):
    name = "power"
    description = "Shut down, restart, sleep, or lock the machine."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="power_lock",
                skill=self.name,
                description="Lock the workstation.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.lock,
                summarize=lambda a: "Lock the machine",
            ),
            ActionDef(
                name="power_sleep",
                skill=self.name,
                description="Put the machine to sleep.",
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.sleep,
                summarize=lambda a: "Put the machine to sleep",
            ),
            ActionDef(
                name="power_shutdown",
                skill=self.name,
                description="Shut the machine down. Unsaved work may be lost.",
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.shutdown,
                params=(
                    Param("delay_seconds", type="integer", required=False, default=30,
                          description="Grace period before shutting down."),
                ),
                summarize=lambda a: (
                    f"Shut down in {a.get('delay_seconds', 30)}s"
                ),
            ),
            ActionDef(
                name="power_restart",
                skill=self.name,
                description="Restart the machine. Unsaved work may be lost.",
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.restart,
                params=(
                    Param("delay_seconds", type="integer", required=False, default=30,
                          description="Grace period before restarting."),
                ),
                summarize=lambda a: f"Restart in {a.get('delay_seconds', 30)}s",
            ),
            ActionDef(
                name="power_cancel",
                skill=self.name,
                description="Cancel a pending shutdown or restart.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.cancel,
                summarize=lambda a: "Cancel a pending shutdown",
            ),
        )

    def _unsupported(self, what: str) -> ActionResult:
        return ActionResult(False, f"{what} is only supported on Windows.")

    def lock(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        if ctx.dry_run:
            return ActionResult(True, "Would lock the machine.", {"dry_run": True})
        if not is_windows():
            return self._unsupported("Locking")
        result = run(["rundll32.exe", "user32.dll,LockWorkStation"])
        if result.returncode != 0:
            return ActionResult(False, "Could not lock the machine.")
        return ActionResult(True, "Locked.")

    def sleep(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        if ctx.dry_run:
            return ActionResult(True, "Would put the machine to sleep.", {"dry_run": True})
        if not is_windows():
            return self._unsupported("Sleep")
        result = run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        if result.returncode != 0:
            return ActionResult(False, "Could not put the machine to sleep.")
        return ActionResult(True, "Going to sleep.")

    def _schedule(self, flag: str, args: dict[str, Any], ctx: ExecContext,
                  verb: str) -> ActionResult:
        delay = max(0, int(args.get("delay_seconds") or 0))
        if ctx.dry_run:
            return ActionResult(True, f"Would {verb} in {delay}s.", {"dry_run": True})
        if not is_windows():
            return self._unsupported(verb.capitalize())
        result = run(["shutdown", flag, "/t", str(delay)])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return ActionResult(False, f"Could not {verb}. {detail}".strip())
        return ActionResult(
            True,
            f"Scheduled {verb} in {delay}s. Run power_cancel to stop it.",
            {"delay_seconds": delay},
        )

    def shutdown(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        return self._schedule("/s", args, ctx, "shut down")

    def restart(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        return self._schedule("/r", args, ctx, "restart")

    def cancel(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        if ctx.dry_run:
            return ActionResult(True, "Would cancel a pending shutdown.", {"dry_run": True})
        if not is_windows():
            return self._unsupported("Cancelling a shutdown")
        result = run(["shutdown", "/a"])
        if result.returncode != 0:
            return ActionResult(False, "There was no pending shutdown to cancel.")
        return ActionResult(True, "Cancelled the pending shutdown.")
