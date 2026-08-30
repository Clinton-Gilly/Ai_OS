"""The AI OS command line.

    ai-os run "organize my desktop"     one request
    ai-os chat                          interactive session
    ai-os skills                        what the assistant can do
    ai-os audit                         what it has done
    ai-os undo                          take the last change back
    ai-os settings ...                  provider, keys, permissions, whitelist
    ai-os gui                           the chat window
    ai-os palette                       the command palette on its own
    ai-os listen                        speak a request (push-to-talk)
    ai-os diagnose                      why is my computer slow
    ai-os memory ...                    inspect and edit what it remembers
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .agent import ApprovalResponse, AutoApprover, PlanResponse, Proposal, TurnResult
from .app import AiOS
from .config import POLICIES
from .llm.router import PROVIDERS
from .memory import KINDS
from .paths import app_home
from .planner import Plan
from .skills.base import ActionResult, RiskLevel
from .voice import build_transcriber

RISK_MARK = {
    RiskLevel.READ_ONLY: "[auto]",
    RiskLevel.REVERSIBLE: "[reversible]",
    RiskLevel.DESTRUCTIVE: "[destructive]",
}


class ConsoleApprover:
    """The terminal approval queue: approve, reject, or edit before running."""

    def __init__(self, stream: Any = None) -> None:
        # Resolved per write, not bound at import time, so redirected output
        # (tests, piping, the GUI) still reaches the right place.
        self._stream = stream

    @property
    def stream(self) -> Any:
        return self._stream if self._stream is not None else sys.stdout

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

    def review_plan(self, plan: Plan) -> PlanResponse:
        print(file=self.stream)
        print("  Plan:", file=self.stream)
        for step in plan.steps:
            print(f"    {step.render()}", file=self.stream)
        if plan.notes:
            print(f"  Note: {plan.notes}", file=self.stream)

        while True:
            try:
                answer = input("  Run this plan? [a]pprove / [r]eject / [e]dit? ") \
                    .strip().lower()
            except EOFError:
                return PlanResponse(False, note="no input available")
            if answer in ("a", "approve", "y", "yes"):
                return PlanResponse(True)
            if answer in ("r", "reject", "n", "no", ""):
                return PlanResponse(False)
            if answer in ("e", "edit"):
                steps = self._edit_plan(plan)
                if steps is not None:
                    return PlanResponse(True, steps=steps, note="edited")
            print("  Please answer a, r, or e.", file=self.stream)

    def _edit_plan(self, plan: Plan) -> list[str] | None:
        print("  Edit each step. Enter keeps it, a blank dash '-' drops it.",
              file=self.stream)
        edited: list[str] = []
        for step in plan.steps:
            try:
                answer = input(f"    {step.number}. [{step.description}]: ").strip()
            except EOFError:
                return None
            if answer == "-":
                continue
            edited.append(answer or step.description)
        return edited or None

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
    run.add_argument("--no-plan", action="store_true",
                     help="Skip the planner and act on the request directly.")
    run.add_argument("--yes", action="store_true",
                     help="Approve every action automatically (use with care).")
    run.add_argument("--json", action="store_true", help="Print the result as JSON.")

    chat = sub.add_parser("chat", help="Start an interactive session.")
    chat.add_argument("--dry-run", action="store_true", help="Preview everything.")
    chat.add_argument("--no-plan", action="store_true",
                      help="Skip the planner and act on each request directly.")

    sub.add_parser("skills", help="List the registered skills and actions.")
    sub.add_parser("gui", help="Open the chat window.")
    sub.add_parser("palette", help="Open the command palette on its own.")

    listen = sub.add_parser("listen", help="Speak a request (push-to-talk).")
    listen.add_argument("--seconds", type=int, default=0,
                        help="How long to listen for; defaults to the setting.")
    listen.add_argument("--dry-run", action="store_true", help="Preview only.")

    diagnose = sub.add_parser("diagnose", help="Investigate slow performance.")
    diagnose.add_argument("--json", action="store_true")

    memory = sub.add_parser("memory", help="Inspect and edit what AI OS remembers.")
    memory_sub = memory.add_subparsers(dest="memory_command")
    memory_list = memory_sub.add_parser("list", help="Show everything remembered.")
    memory_list.add_argument("--kind", default="", choices=["", *KINDS])
    memory_list.add_argument("--search", default="")
    memory_set = memory_sub.add_parser("remember", help="Remember something.")
    memory_set.add_argument("kind", choices=list(KINDS))
    memory_set.add_argument("key")
    memory_set.add_argument("value")
    memory_forget = memory_sub.add_parser("forget", help="Forget remembered items.")
    memory_forget.add_argument("--kind", default="", choices=["", *KINDS])
    memory_forget.add_argument("--key", default="")
    memory_forget.add_argument("--all", action="store_true",
                               help="Forget everything AI OS remembers.")

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
            "palette": cmd_palette,
            "listen": cmd_listen,
            "diagnose": cmd_diagnose,
            "memory": cmd_memory,
        }
        return handlers[command](app, args)
    finally:
        app.close()


# -- commands -----------------------------------------------------------
def cmd_run(app: AiOS, args: argparse.Namespace) -> int:
    request = " ".join(args.request)
    if getattr(args, "no_plan", False):
        app.config.planner_enabled = False
    approver = AutoApprover() if getattr(args, "yes", False) else ConsoleApprover()
    turn = app.supervisor.handle(request, approver, dry_run=args.dry_run)
    if getattr(args, "json", False):
        print(json.dumps(_turn_as_dict(turn), indent=2, default=str))
        return 0 if not turn.error else 1
    _print_turn(turn)
    return 0 if not turn.error else 1


def cmd_chat(app: AiOS, args: argparse.Namespace) -> int:
    if getattr(args, "no_plan", False):
        app.config.planner_enabled = False
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


def cmd_palette(app: AiOS, args: argparse.Namespace) -> int:
    from .gui.window import run_gui

    return run_gui(app, palette_only=True)


def cmd_listen(app: AiOS, args: argparse.Namespace) -> int:
    transcriber = build_transcriber(app.config.voice_enabled)
    if not app.config.voice_enabled:
        print("Voice input is off. Turn it on with: "
              "ai-os settings set voice-enabled true")
        return 1
    if not transcriber.available():
        from .voice import INSTALL_HINT

        print(INSTALL_HINT)
        return 1

    seconds = args.seconds or app.config.voice_seconds
    print(f"Listening for up to {seconds}s… speak now.")
    result = transcriber.transcribe(seconds, app.config.voice_language)
    if not result.ok:
        print(result.error)
        return 1

    print(f'Heard: "{result.text}"')
    turn = app.supervisor.handle(result.text, ConsoleApprover(), dry_run=args.dry_run,
                                 interface="voice")
    _print_turn(turn)
    return 0 if not turn.error else 1


def cmd_diagnose(app: AiOS, args: argparse.Namespace) -> int:
    action = app.registry.get("diag_report")
    if action is None:
        print("The diagnostics skill is not registered.")
        return 1
    from .skills.base import ExecContext

    result = action.handler({}, ExecContext(dry_run=False, config=app.config))
    if args.json:
        print(json.dumps(result.data, indent=2, default=str))
        return 0
    for finding in result.data.get("findings", []):
        print(f"  - {finding}")
    suggestions = result.data.get("suggestions", [])
    if suggestions:
        print("\nSuggested:")
        for suggestion in suggestions:
            print(f"  - {suggestion}")
    return 0


def cmd_memory(app: AiOS, args: argparse.Namespace) -> int:
    command = getattr(args, "memory_command", None) or "list"

    if command == "list":
        items = app.memory.search(args.search) if args.search \
            else app.memory.items(args.kind)
        if not items:
            print("Nothing is remembered yet.")
            return 0
        for item in items:
            print(f"  [{item.kind}] {item.describe()}")
        print(f"\n{len(items)} item(s). Forget one with: "
              "ai-os memory forget --kind KIND --key KEY")
        return 0

    if command == "remember":
        app.memory.remember(args.kind, args.key, args.value)
        print(f"Remembered {args.kind}: {args.key} = {args.value}")
        return 0

    if command == "forget":
        if not (args.all or args.kind or args.key):
            print("Nothing selected. Pass --kind, --key, or --all.")
            return 1
        if args.key and not args.kind:
            print("--key needs --kind as well.")
            return 1
        count = app.memory.forget("" if args.all else args.kind,
                                  "" if args.all else args.key)
        print(f"Forgot {count} item(s).")
        return 0

    print("Unknown memory command.")
    return 1


# -- output helpers -----------------------------------------------------
def _print_turn(turn: TurnResult) -> None:
    if turn.plan and turn.plan.multi_step:
        print("  Plan:")
        for step in turn.plan.steps:
            print(f"    {step.render()}")
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
        "plan": turn.plan.to_dict() if turn.plan else None,
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
