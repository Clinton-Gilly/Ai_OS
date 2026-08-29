# Architecture

## Layout

```
ai_os/
  agent.py         supervisor: request -> proposals -> approval -> execution
  safety.py        permission engine: risk tiers, policies, folder whitelist
  audit.py         SQLite audit log, undo stack, memory table
  undo.py          reverses executed actions from the undo stack
  config.py        settings and provider/API key management
  app.py           wiring — builds a ready-to-use instance
  cli.py           command line, including the terminal approval queue
  trash.py         Recycle Bin, with a reversible fallback elsewhere
  startup.py       start-on-boot toggle (HKCU Run key)
  platform_win.py  the Windows-specific calls, with off-Windows fallbacks
  llm/
    base.py        provider-neutral Message / ToolCall / LLMResponse
    router.py      picks a provider by name
    anthropic.py   Claude (Messages API)
    openai_compat.py  any OpenAI-compatible chat-completions endpoint
    local.py       offline rule-based fallback (dev and tests)
  skills/
    base.py        the Skill / ActionDef contract
    registry.py    the tool router
    filesystem.py apps.py browser.py power.py system.py
  gui/
    window.py      Tkinter shell: chat, approvals, audit, settings
    approval.py    thread-safe approval queue behind the window
```

## The request path

```
       ┌────────────┐   natural language   ┌────────────┐
       │ CLI / GUI  │ ───────────────────▶ │ Supervisor │
       └────────────┘                      └─────┬──────┘
              ▲                                  │ tool schemas
              │                            ┌─────▼──────┐
              │                            │ LLM router │ ── Claude / OpenAI / local
              │                            └─────┬──────┘
              │                                  │ tool calls
              │                            ┌─────▼──────┐
              │                            │   Safety   │ risk tier + policy
              │                            │   engine   │ + folder whitelist
              │                            └─────┬──────┘
              │        approve/reject/edit       │ allow | approve | deny
              └──────────────────────────────────┤
                                           ┌─────▼──────┐
                                           │Skill (tool)│ dry-run or execute
                                           └─────┬──────┘
                                 ┌───────────────┴────────────┐
                           ┌─────▼─────┐               ┌──────▼─────┐
                           │ Audit log │               │ Undo stack │
                           └───────────┘               └────────────┘
```

Tool results are fed back to the model so it can summarize what actually
happened, bounded by `max_tool_iterations`.

## Writing a skill

An action declares its own risk, parameters, handler, and how to undo itself.

```python
from ai_os.skills.base import (
    ActionDef, ActionResult, Param, RiskLevel, Skill, UndoRecord,
)


class NotesSkill(Skill):
    name = "notes"
    description = "Keep a scratch notes file."

    def actions(self):
        return (ActionDef(
            name="notes_append",
            skill=self.name,
            description="Append a line to the notes file.",
            risk=RiskLevel.REVERSIBLE,
            handler=self.append,
            reversible=True,
            params=(
                Param("path", description="Notes file.", is_path=True),
                Param("text", description="Line to append."),
            ),
            summarize=lambda a: f"Append a line to {a['path']}",
        ),)

    def append(self, args, ctx):
        path = Path(args["path"])
        if ctx.dry_run:                       # dry-run must never mutate
            return ActionResult(True, f"Would append to {path}.", {"dry_run": True})
        before = path.read_text() if path.exists() else ""
        path.write_text(before + args["text"] + "\n")
        return ActionResult(
            True, f"Appended to {path}.", {},
            UndoRecord("notes.append", {"path": str(path), "before": before},
                       f"Restore {path}"),
        )

    def undo_handlers(self):
        return {"notes.append": self.undo_append}

    def undo_append(self, record, ctx):
        Path(record.payload["path"]).write_text(record.payload["before"])
        return ActionResult(True, "Restored the notes file.")
```

Register it and it is immediately available to the model, the CLI, and the GUI:

```python
registry = default_registry()
registry.register(NotesSkill())
```

Rules a skill must follow:

- **Honour `ctx.dry_run`.** A dry run reports what would change and changes
  nothing.
- **Pick the honest risk tier.** `READ_ONLY` means it observes and nothing else.
- **Mark path parameters with `is_path=True`** so the whitelist applies to them.
- **Return an `UndoRecord`** whenever the change can be reversed.
- **Return failures, don't raise.** The supervisor catches exceptions, but an
  `ActionResult(False, ...)` gives the user a usable message.
- **Set `bulk=True`** if one call can touch many items; the safety engine then
  forces a preview before approval.

## Adding an LLM provider

Implement `Provider.chat(messages, tools) -> LLMResponse`, translating the
neutral types to the provider's wire format, and add it to `PROVIDERS` in
`llm/router.py`. Nothing else changes. Providers that speak the OpenAI
chat-completions format need no new class at all — set `base_url` on the
existing one.

## The database

`ai_os.sqlite3` holds four tables:

- `requests` — one row per natural-language request, with provider and model
- `actions` — every proposal: arguments, risk, verdict, outcome, message
- `undo_stack` — reversal records, marked `undone_at` when applied
- `memory` — `(kind, key) -> value`, the Phase 2 memory system's storage

Actions are recorded whatever the outcome: denied and rejected proposals are
logged next to executed ones, which is what makes the log a real audit trail.
