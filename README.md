# AI OS

A natural-language AI control layer for Windows 11. Not a new kernel — a layer
that sits on top of Windows and lets you operate the machine by talking to it:
files, apps, browser, and power, with every risky action routed through a
permission system before anything happens.

> **Status: Phase 1 (Core).** The skill architecture, LLM router, permission
> engine, dry-run, undo, audit log, CLI, and GUI shell are in place. Phases 2–4
> (planner, memory, event bus, workflows, vision) are not built yet — see
> [docs/ROADMAP.md](docs/ROADMAP.md).

```
you> organize my desktop

  Proposed: Organize C:\Users\you\Desktop by type
  Risk:     reversible (bulk action: previewed with a dry run first)
  Preview:  Would move 34 file(s) into 5 folder(s).
            C:\Users\you\Desktop\photo.png -> ...\Desktop\Images\photo.png
            C:\Users\you\Desktop\notes.pdf -> ...\Desktop\Documents\notes.pdf
  [a]pprove / [r]eject / [e]dit? a

  [OK] Organize C:\Users\you\Desktop by type
       Organized 34 file(s) by type.
       undo with: ai-os undo --id 12
```

## Install

Python 3.10+ is required. On Windows, the standard python.org installer already
includes Tkinter, so the GUI needs no extra step.

```powershell
git clone https://github.com/Clinton-Gilly/Ai_OS.git
cd Ai_OS
pip install -e ".[windows]"     # on Linux/macOS, just: pip install -e .
```

The `windows` extra pulls in `send2trash` (real Recycle Bin support) and
`psutil` (richer process listing). Both are optional — AI OS falls back to a
local, reversible trash folder and to `tasklist` without them.

## Choose a model

AI OS is provider-agnostic. Phase 1 ships Claude, any OpenAI-compatible
endpoint, and an offline rule-based fallback used when no key is configured.

```powershell
ai-os settings set-provider anthropic --model claude-sonnet-4-5
ai-os settings set-key anthropic        # prompts without echoing

# or an OpenAI-compatible endpoint (also covers Groq/DeepSeek/OpenRouter
# by pointing base_url at them — those land properly in Phase 3)
ai-os settings set-provider openai --model gpt-4o-mini
ai-os settings set-key openai
```

Keys can also come from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. A stored key
takes precedence. Keys are never written to the audit log and `settings show`
redacts them.

## Use it

```powershell
ai-os run "organize my downloads by date"   # one request
ai-os run "delete old.txt" --dry-run        # preview, change nothing
ai-os chat                                  # interactive session
ai-os gui                                   # chat window + approval queue
ai-os skills                                # everything it can do
ai-os audit                                 # everything it has done
ai-os undo                                  # take the last change back
ai-os settings show
```

## How a request flows

```
request -> LLM router -> proposed actions -> safety engine -> approval queue
                                                                    |
                       audit log <- execution <- approved (or edited)
                            |
                        undo stack
```

1. **AI router** turns the request into calls against registered skills. Which
   model does that is a settings change, never a code change.
2. **Safety engine** classifies each proposed call by risk tier and applies the
   permission policy and the allowed-folder whitelist.
3. **Approval queue** shows the action, its risk, and — for anything touching
   more than one file — a dry-run preview. Approve, reject, or edit the
   arguments before it runs. Edited arguments are re-checked; editing cannot
   route around the whitelist.
4. **Audit log** records every proposal, decision, and outcome in local SQLite.
5. **Undo stack** reverses moves, renames, organizing, folder creation, and
   deletes made to the fallback trash.

## Permission tiers

| Tier | Examples | Default |
|---|---|---|
| **read-only** | list a folder, disk usage, running processes | runs automatically |
| **reversible** | move, rename, organize, open an app, lock | asks first |
| **destructive** | delete, close an app, shutdown, restart, sleep | always asks |

Destructive actions ask for approval regardless of configuration — that guard is
not a setting. Everything else is configurable:

```powershell
ai-os settings set-policy reversible auto      # auto | confirm | deny
ai-os settings allow-folder "C:\Users\you\Projects"
ai-os settings set deletes-to-recycle-bin true
ai-os settings set dry-run-default true
```

Paths outside the allowed folders are refused. Windows system locations
(`C:\Windows`, `Program Files`, …) are refused no matter what the whitelist
says.

## Skills

| Skill | Actions |
|---|---|
| `filesystem` | list, find, create folder, move, rename, organize, delete |
| `apps` | list running, open, close |
| `browser` | open a URL, a search, or a browser settings page |
| `power` | lock, sleep, shutdown, restart, cancel |
| `system` | OS info, disk usage, current time |

Adding a capability means writing a `Skill` and registering it — the router, the
permission engine, and both interfaces pick it up with no changes:

```python
class GreetSkill(Skill):
    name = "greet"
    description = "Say hello."

    def actions(self):
        return (ActionDef(
            name="greet_hello",
            skill=self.name,
            description="Say hello to someone.",
            risk=RiskLevel.READ_ONLY,
            handler=lambda args, ctx: ActionResult(True, f"Hello, {args['who']}!"),
            params=(Param("who", description="Who to greet."),),
        ),)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract,
including how to make an action undoable.

## Your data

Everything stays on the machine, under `%APPDATA%\AiOS` (override with
`AI_OS_HOME`):

- `config.json` — settings and API keys, written owner-only
- `ai_os.sqlite3` — audit log, undo stack, and the memory table Phase 2 uses
- `trash/` — the fallback trash used when the Recycle Bin is unavailable

`ai-os settings set log-prompts false` stops request text being recorded.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The test suite runs on Linux, macOS, and Windows: Windows-only calls are behind
platform checks, and the offline rule provider means no test needs a network or
an API key.

## License

MIT
