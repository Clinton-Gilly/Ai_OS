"""The AI OS command line.

    ai-os run "organize my desktop"     one request
    ai-os chat                          interactive session
    ai-os skills                        what the assistant can do
    ai-os audit                         what it has done
    ai-os undo                          take the last change back
    ai-os settings ...                  provider, keys, permissions, whitelist
    ai-os gui                           the chat window
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .agent import ApprovalResponse, AutoApprover, Proposal, TurnResult
from .app import AiOS
from .config import POLICIES
from .llm.router import PROVIDERS
from .paths import app_home
from .skills.base import ActionResult, RiskLevel

RISK_MARK = {
    RiskLevel.READ_ONLY: "[auto]",
    RiskLevel.REVERSIBLE: "[reversible]",
    RiskLevel.DESTRUCTIVE: "[destructive]",
}


class ConsoleApprover:
    """The terminal approval queue: approve, reject, or edit before running."""

    def __init__(self, stream=sys.stdout) -> None:
        self.stream = stream

    def review(self, proposal: Proposal,
               dry_run_result: ActionResult | None = None) -> ApprovalResponse:
        print(file=self.stream)
        print(f"  Proposed: {proposal.summary}", file=self.stream)
        print(f"  Risk:     {proposal.decision.risk.label}"
              f" ({proposal.decision.explain()})", file=self.stream)
        if proposal.args:
            print(f"  Arguments: {json.dumps(proposal.args, default=str)}", file=self.stream)
        if dry_run_result is not None:
            print(f"  Preview:  {dry_run_result.message}", file=self.stream)
            for move in (dry_run_result.data or {}).get("moves", [])[:10]:
                print(f"            {move['from']} -> {move['to']}", file=self.stream)

        while True:
            try:
                answer = input("  [a]pprove / [r]eject / [e]dit? ").strip().lower()
            except EOFError:
                return ApprovalResponse(False, note="no input available")
            if answer in ("a", "approve", "y", "yes"):
                return ApprovalResponse(True)
            if answer in ("r", "reject", "n", "no", ""):
                return ApprovalResponse(False)
            if answer in ("e", "edit"):
                edited = self._edit(proposal)
                if edited is not None:
                    return ApprovalResponse(True, args=edited, note="edited")
            print("  Please answer a, r, or e.", file=self.stream)

    def _edit(self, proposal: Proposal) -> dict[str, Any] | None:
        edited = dict(proposal.args)
        for param in proposal.action.params:
            current = edited.get(param.name, "")
            try:
                new_value = input(f"    {param.name} [{current}]: ").strip()
            except EOFError:
                return None
            if new_value:
                edited[param.name] = new_value
        return edited


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-os", description="AI OS — talk to your PC.")
    parser.add_argument("--config", type=Path, help="Path to an alternate config file.")
    parser.add_argument("--db", type=Path, help="Path to an alternate audit database.")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run a single natural-language request.")
    run.add_argument("request", nargs="+", help="What you want done.")
    run.add_argument("--dry-run", action="store_true", help="Preview without changing anything.")
    run.add_argument("--yes", action="store_true",
                     help="Approve every action automatically (use with care).")
    run.add_argument("--json", action="store_true", help="Print the result as JSON.")

    chat = sub.add_parser("chat", help="Start an interactive session.")
    chat.add_argument("--dry-run", action="store_true", help="Preview everything.")

    sub.add_parser("skills", help="List the registered skills and actions.")
    sub.add_parser("gui", help="Open the chat window.")

    audit = sub.add_parser("audit", help="Show the local audit log.")
    audit.add_argument("--limit", type=int, default=20)
    audit.add_argument("--search", default="")
    audit.add_argument("--requests", action="store_true", help="Show requests, not actions.")

    undo = sub.add_parser("undo", help="Undo the most recent reversible action.")
    undo.add_argument("--id", type=int, help="Undo a specific entry from 'undo --list'.")
    undo.add_argument("--list", action="store_true", help="List what can be undone.")

    settings = sub.add_parser("settings", help="View and change settings.")
    settings_sub = settings.add_subparsers(dest="settings_command")
    settings_sub.add_parser("show", help="Print the current settings.")
    set_provider = settings_sub.add_parser("set-provider", help="Choose the active provider.")
    set_provider.add_argument("provider", choices=sorted(PROVIDERS))
    set_provider.add_argument("--model", default="", help="Model name to use.")
    set_key = settings_sub.add_parser("set-key", help="Store an API key for a provider.")
    set_key.add_argument("provider", choices=sorted(PROVIDERS))
    set_key.add_argument("api_key", nargs="?", default="",
                         help="Omit to be prompted without echoing.")
    set_policy = settings_sub.add_parser("set-policy", help="Change a permission rule.")
    set_policy.add_argument("tier", choices=["read-only", "reversible", "destructive"])
    set_policy.add_argument("policy", choices=list(POLICIES))
    allow = settings_sub.add_parser("allow-folder", help="Add a folder to the whitelist.")
    allow.add_argument("path", type=Path)
    disallow = settings_sub.add_parser("remove-folder", help="Remove a whitelisted folder.")
    disallow.add_argument("path", type=Path)
    toggle = settings_sub.add_parser("set", help="Set a boolean or simple option.")
    toggle.add_argument("option")
    toggle.add_argument("value")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "chat"

    app = AiOS.create(config_path=args.config, db_path=args.db)
    try:
        handlers = {
            "run": cmd_run,
            "chat": cmd_chat,
            "skills": cmd_skills,
            "audit": cmd_audit,
            "undo": cmd_undo,
            "settings": cmd_settings,
            "gui": cmd_gui,
        }
        return handlers[command](app, args)
    finally:
        app.close()


# -- commands -----------------------------------------------------------
def cmd_run(app: AiOS, args: argparse.Namespace) -> int:
    request = " ".join(args.request)
    approver = AutoApprover() if getattr(args, "yes", False) else ConsoleApprover()
    turn = app.supervisor.handle(request, approver, dry_run=args.dry_run)
    if getattr(args, "json", False):
        print(json.dumps(_turn_as_dict(turn), indent=2, default=str))
        return 0 if not turn.error else 1
    _print_turn(turn)
    return 0 if not turn.error else 1


def cmd_chat(app: AiOS, args: argparse.Namespace) -> int:
    provider = app.router.build()
    print(f"AI OS — provider: {provider.name} ({provider.model or 'default model'})")
    print(f"Data directory: {app_home()}")
    if args.dry_run:
        print("Dry-run mode: nothing will be changed.")
    print("Type 'exit' to quit, 'undo' to take the last change back.\n")
    approver = ConsoleApprover()
    while True:
        try:
            request = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not request:
            continue
        if request.lower() in {"exit", "quit"}:
            return 0
        if request.lower() == "undo":
            _run_undo(app, None)
            continue
        turn = app.supervisor.handle(request, approver, dry_run=args.dry_run)
        _print_turn(turn)


def cmd_skills(app: AiOS, args: argparse.Namespace) -> int:
    for skill in app.registry.skills():
        print(f"\n{skill.name} — {skill.description}")
        for action in skill.actions():
            mark = RISK_MARK[action.risk]
            params = ", ".join(
                p.name + ("" if p.required else "?") for p in action.params
            )
            print(f"  {action.name:<20} {mark:<14} ({params})")
            print(f"      {action.description}")
    print(f"\n{len(app.registry)} actions across {len(app.registry.skills())} skills.")
    return 0


def cmd_audit(app: AiOS, args: argparse.Namespace) -> int:
    if args.requests:
        rows = app.audit.recent_requests(args.limit)
        if not rows:
            print("No requests recorded yet.")
            return 0
        for row in rows:
            print(f"[{row['created_at']}] #{row['id']} ({row['provider']}/{row['model']}) "
                  f"{row['text']}")
            if row["response"]:
                print(f"    -> {row['response']}")
        return 0

    rows = app.audit.recent_actions(args.limit, args.search)
    if not rows:
        print("Nothing in the audit log yet.")
        return 0
    for row in rows:
        flag = " (dry-run)" if row["dry_run"] else ""
        print(f"[{row['created_at']}] {row['outcome']:<9}{flag} {row['summary']}")
        print(f"    risk={row['risk']} verdict={row['verdict']} :: {row['message']}")
    return 0


def cmd_undo(app: AiOS, args: argparse.Namespace) -> int:
    if args.list:
        entries = app.undo.pending()
        if not entries:
            print("Nothing to undo.")
            return 0
        for entry in entries:
            print(f"  #{entry.id} [{entry.created_at}] {entry.description}")
        return 0
    return _run_undo(app, args.id)


def _run_undo(app: AiOS, undo_id: int | None) -> int:
    outcome = app.undo.undo(undo_id)
    if outcome is None:
        print("Nothing to undo.")
        return 0
    status = "Undone" if outcome.result.ok else "Could not undo"
    print(f"{status}: {outcome.entry.description}\n  {outcome.result.message}")
    return 0 if outcome.result.ok else 1


def cmd_settings(app: AiOS, args: argparse.Namespace) -> int:
    command = getattr(args, "settings_command", None) or "show"
    config = app.config

    if command == "show":
        print(json.dumps(config.redacted(), indent=2, sort_keys=True))
        print(f"\nConfig file: {app.config_path or app_home() / 'config.json'}")
        return 0

    if command == "set-provider":
        config.provider = args.provider
        if args.model:
            config.provider_config(args.provider).model = args.model
        app.save()
        print(f"Active provider is now {args.provider}"
              f" ({config.provider_config(args.provider).resolved_model(args.provider)}).")
        return 0

    if command == "set-key":
        key = args.api_key
        if not key:
            import getpass

            key = getpass.getpass(f"{args.provider} API key: ").strip()
        if not key:
            print("No key entered; nothing changed.")
            return 1
        config.provider_config(args.provider).api_key = key
        app.save()
        print(f"Stored an API key for {args.provider}.")
        return 0

    if command == "set-policy":
        field = {
            "read-only": "policy_read_only",
            "reversible": "policy_reversible",
            "destructive": "policy_destructive",
        }[args.tier]
        setattr(config, field, args.policy)
        app.save()
        print(f"{args.tier} actions are now set to '{args.policy}'.")
        if args.tier == "destructive" and args.policy == "auto":
            print("Note: destructive actions still ask for approval; that guard is not"
                  " configurable.")
        return 0

    if command == "allow-folder":
        folder = str(args.path.expanduser())
        if folder not in config.allowed_folders:
            config.allowed_folders.append(folder)
            app.save()
        print(f"Allowed folders: {config.allowed_folders or ['(home directory)']}")
        return 0

    if command == "remove-folder":
        folder = str(args.path.expanduser())
        if folder in config.allowed_folders:
            config.allowed_folders.remove(folder)
            app.save()
        print(f"Allowed folders: {config.allowed_folders or ['(home directory)']}")
        return 0

    if command == "set":
        return _set_option(app, args.option, args.value)

    print("Unknown settings command.")
    return 1


def _set_option(app: AiOS, option: str, value: str) -> int:
    config = app.config
    name = option.replace("-", "_")
    if not hasattr(config, name):
        print(f"Unknown option {option!r}. Try: ai-os settings show")
        return 1
    current = getattr(config, name)
    if isinstance(current, bool):
        parsed: Any = value.strip().lower() in {"true", "yes", "1", "on"}
    elif isinstance(current, int):
        parsed = int(value)
    elif isinstance(current, list):
        parsed = [item.strip() for item in value.split(",") if item.strip()]
    else:
        parsed = value
    setattr(config, name, parsed)
    app.save()
    print(f"{option} = {parsed}")
    return 0


def cmd_gui(app: AiOS, args: argparse.Namespace) -> int:
    from .gui.window import run_gui

    return run_gui(app)


# -- output helpers -----------------------------------------------------
def _print_turn(turn: TurnResult) -> None:
    for outcome in turn.outcomes:
        icon = {
            "executed": "OK",
            "dry-run": "DRY",
            "rejected": "SKIP",
            "denied": "DENY",
            "failed": "FAIL",
        }.get(outcome.status, outcome.status.upper())
        print(f"  [{icon}] {outcome.proposal.summary}")
        print(f"        {outcome.message}")
        if outcome.undo_id:
            print(f"        undo with: ai-os undo --id {outcome.undo_id}")
    if turn.reply:
        print(f"\nai-os> {turn.reply}\n")


def _turn_as_dict(turn: TurnResult) -> dict[str, Any]:
    return {
        "request": turn.request,
        "reply": turn.reply,
        "provider": turn.provider,
        "model": turn.model,
        "error": turn.error,
        "outcomes": [
            {
                "action": outcome.proposal.action.name,
                "arguments": outcome.proposal.args,
                "summary": outcome.proposal.summary,
                "risk": outcome.proposal.decision.risk.label,
                "verdict": outcome.proposal.decision.verdict.value,
                "status": outcome.status,
                "message": outcome.message,
                "undo_id": outcome.undo_id,
            }
            for outcome in turn.outcomes
        ],
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
