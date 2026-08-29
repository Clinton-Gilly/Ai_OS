"""The safety engine.

Every proposed action passes through here before anything runs. It resolves the
risk tier into one of three verdicts using the configured permission policy, and
it enforces the allowed-folder whitelist for actions that touch paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config import POLICY_AUTO, POLICY_CONFIRM, POLICY_DENY, Config
from .skills.base import ActionDef, RiskLevel

# Locations no assistant should be rearranging, whitelist or not.
_WINDOWS_PROTECTED = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
    "c:/$recycle.bin",
    "c:/system volume information",
)
_POSIX_PROTECTED = ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr")


class Verdict(str, Enum):
    ALLOW = "allow"          # run it now
    APPROVE = "approve"      # ask the user first
    DENY = "deny"            # refuse outright


@dataclass
class Decision:
    verdict: Verdict
    risk: RiskLevel
    reasons: list[str] = field(default_factory=list)
    force_dry_run: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict is not Verdict.DENY

    def explain(self) -> str:
        return "; ".join(self.reasons) if self.reasons else self.risk.label


def is_protected_path(path: Path) -> bool:
    """True for OS-owned locations that are always off limits."""
    text = str(path.resolve()).replace("\\", "/").lower().rstrip("/")
    protected = _WINDOWS_PROTECTED if os.name == "nt" else _POSIX_PROTECTED
    for root in protected:
        if text == root or text.startswith(root.rstrip("/") + "/"):
            return True
    return False


def within_allowed(path: Path, allowed: list[Path]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for folder in allowed:
        try:
            resolved.relative_to(folder.expanduser().resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


class SafetyEngine:
    """Applies permission rules to a proposed action."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def policy_for(self, risk: RiskLevel) -> str:
        return {
            RiskLevel.READ_ONLY: self.config.policy_read_only,
            RiskLevel.REVERSIBLE: self.config.policy_reversible,
            RiskLevel.DESTRUCTIVE: self.config.policy_destructive,
        }[risk]

    def evaluate(self, action: ActionDef, args: dict[str, Any]) -> Decision:
        reasons: list[str] = []
        policy = self.policy_for(action.risk)
        verdict = {
            POLICY_AUTO: Verdict.ALLOW,
            POLICY_CONFIRM: Verdict.APPROVE,
            POLICY_DENY: Verdict.DENY,
        }.get(policy, Verdict.APPROVE)
        reasons.append(f"{action.risk.label} action, policy is {policy}")

        path_verdict, path_reasons = self._check_paths(action, args)
        reasons.extend(path_reasons)
        if path_verdict is not None:
            verdict = _strictest(verdict, path_verdict)

        # Anything that can touch many items at once gets previewed first.
        force_dry_run = action.bulk and verdict is not Verdict.DENY
        if force_dry_run:
            reasons.append("bulk action: previewed with a dry run first")

        if action.risk is RiskLevel.DESTRUCTIVE and verdict is Verdict.ALLOW:
            # Destructive work never runs unattended, whatever the policy says.
            verdict = Verdict.APPROVE
            reasons.append("destructive actions always require approval")

        return Decision(verdict, action.risk, reasons, force_dry_run)

    def _check_paths(self, action: ActionDef,
                     args: dict[str, Any]) -> tuple[Verdict | None, list[str]]:
        names = action.path_params()
        if not names:
            return None, []
        allowed = self.config.effective_allowed_folders()
        verdict: Verdict | None = None
        reasons: list[str] = []
        for name in names:
            raw = args.get(name)
            if not raw:
                continue
            path = Path(str(raw)).expanduser()
            if is_protected_path(path):
                return Verdict.DENY, [f"{path} is a protected system location"]
            if not within_allowed(path, allowed):
                if self.config.whitelist_mode == "warn":
                    verdict = _strictest(verdict or Verdict.ALLOW, Verdict.APPROVE)
                    reasons.append(f"{path} is outside the allowed folders")
                else:
                    return Verdict.DENY, [f"{path} is outside the allowed folders"]
        return verdict, reasons


def _strictest(left: Verdict, right: Verdict) -> Verdict:
    order = {Verdict.ALLOW: 0, Verdict.APPROVE: 1, Verdict.DENY: 2}
    return left if order[left] >= order[right] else right
