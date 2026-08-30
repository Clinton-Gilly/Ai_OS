# Roadmap

The build order from the project plan. Something usable ships early rather than
chasing the full spec before anything works.

## Phase 1 — Core ✅

- [x] Skill/plugin architecture from day one — no hardcoded `if/elif` command handling
- [x] LLM router: Claude plus one OpenAI-compatible provider
- [x] Permission engine: auto / reversible-cancelable / must-approve-with-edit
- [x] Dry-run mode for anything touching more than one file or system state
- [x] Undo for file operations; deletes go to the Recycle Bin by default
- [x] Audit log (local SQLite)
- [x] CLI first, then the GUI shell: chat window, approval queue, Settings page
- [x] Settings: provider and API key management, model choice, permission rules,
      allowed-folder whitelist, start-on-boot toggle

Core control covered: file operations, application control, browser control,
power control.

## Phase 2 — Intelligence ✅ (this release)

- [x] Planner for multi-step requests ("prepare my laptop for development"),
      reviewed and editable as a whole before any step runs
- [x] Memory system — inspectable and deletable, visible in Settings and from
      `ai-os memory`; learns folders, apps, and repeated sequences
- [x] System diagnostics ("why is my computer slow"), with suggested fixes
- [x] Global hotkey and command palette
- [x] Voice input (push-to-talk; optional extra, off by default)
- [x] Task scheduling: one-off reminders and recurring tasks

Learned workflows are recorded and counted (`ai-os memory list --kind
workflow`); offering to save a repeated sequence as a single named command
belongs with the workflow engine in Phase 3.

## Phase 3 — Automation platform

- [ ] Event bus: file created, USB connected, battery low, app crashed
- [ ] Workflow engine ("when X happens, do Y") — tightly scoped; new rules
      require approval
- [ ] Remaining providers: Gemini, DeepSeek, Groq, OpenRouter
      (the OpenAI-compatible provider already reaches several via `base_url`)
- [ ] MCP compatibility

## Phase 4 — Advanced

- [ ] Screen/vision control, under its own stricter permission tier
- [ ] Browser agent — navigate, read, extract, not just open a URL
- [ ] Secrets redaction and a full Privacy Center
- [ ] Local + cloud model routing
- [ ] Multi-agent supervisor architecture
