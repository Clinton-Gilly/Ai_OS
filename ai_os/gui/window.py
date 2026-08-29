"""The AI OS window: chat, approval queue, audit, and settings.

Tkinter is used deliberately — it ships with Python on Windows, so the GUI
shell has no extra install step. The supervisor runs on a worker thread; the UI
polls for approvals and results so the window never freezes mid-request.
"""

from __future__ import annotations

import threading
from typing import Any

try:  # tkinter is optional on some Linux builds; the CLI must still work.
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    TK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the Python build
    TK_AVAILABLE = False

from ..app import AiOS
from ..config import POLICIES
from ..llm.router import PROVIDERS
from ..paths import app_home
from ..startup import is_supported as startup_supported
from ..startup import set_enabled as set_startup
from .approval import PendingApproval, QueueApprover

POLL_MS = 120


class AiOsWindow:
    """The main window. One instance per process."""

    def __init__(self, app: AiOS) -> None:
        self.app = app
        self.approver = QueueApprover()
        self.pending: PendingApproval | None = None
        self.results: list[Any] = []
        self.busy = False
        self._lock = threading.Lock()

        self.root = tk.Tk()
        self.root.title("AI OS")
        self.root.geometry("880x620")
        self.root.minsize(700, 480)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_chat_tab()
        self._build_approvals_tab()
        self._build_audit_tab()
        self._build_settings_tab()

        self.status = tk.StringVar(value=f"Ready — data in {app_home()}")
        ttk.Label(self.root, textvariable=self.status, anchor="w").pack(
            fill="x", padx=12, pady=(0, 8))
        self.root.after(POLL_MS, self._poll)

    # -- chat -----------------------------------------------------------
    def _build_chat_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Chat")

        self.transcript = scrolledtext.ScrolledText(frame, wrap="word", height=20,
                                                    state="disabled")
        self.transcript.pack(fill="both", expand=True, padx=8, pady=8)
        self.transcript.tag_configure("you", foreground="#1a5fb4")
        self.transcript.tag_configure("assistant", foreground="#26734d")
        self.transcript.tag_configure("system", foreground="#8b6f00")
        self.transcript.tag_configure("error", foreground="#a51d2d")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self._send())
        self.dry_run = tk.BooleanVar(value=self.app.config.dry_run_default)
        ttk.Checkbutton(row, text="Dry run", variable=self.dry_run).pack(side="left", padx=6)
        self.send_button = ttk.Button(row, text="Send", command=self._send)
        self.send_button.pack(side="left")

        provider = self.app.router.build()
        self._write(
            f"AI OS ready. Provider: {provider.name} "
            f"({provider.model or 'default model'}). Risky actions appear in the "
            "Approvals tab.\n", "system")

    def _write(self, text: str, tag: str = "") -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tag)
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _send(self) -> None:
        request = self.entry.get().strip()
        if not request or self.busy:
            return
        self.entry.delete(0, "end")
        self._write(f"\nyou: {request}\n", "you")
        self.busy = True
        self.send_button.state(["disabled"])
        self.status.set("Thinking…")
        dry_run = self.dry_run.get()
        thread = threading.Thread(target=self._run_request, args=(request, dry_run),
                                  daemon=True)
        thread.start()

    def _run_request(self, request: str, dry_run: bool) -> None:
        try:
            turn = self.app.supervisor.handle(
                request, self.approver, dry_run=dry_run, interface="gui")
        except Exception as exc:  # keep the UI alive whatever a skill does
            turn = exc
        with self._lock:
            self.results.append(turn)

    # -- approvals ------------------------------------------------------
    def _build_approvals_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Approvals")
        self.approvals_frame = frame

        self.approval_text = scrolledtext.ScrolledText(frame, wrap="word", height=14,
                                                       state="disabled")
        self.approval_text.pack(fill="both", expand=True, padx=8, pady=8)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        self.approve_button = ttk.Button(buttons, text="Approve", command=self._approve)
        self.reject_button = ttk.Button(buttons, text="Reject", command=self._reject)
        self.edit_button = ttk.Button(buttons, text="Edit…", command=self._edit)
        for button in (self.approve_button, self.edit_button, self.reject_button):
            button.pack(side="left", padx=4)
            button.state(["disabled"])
        self._set_approval_text("Nothing is waiting for approval.")

    def _set_approval_text(self, text: str) -> None:
        self.approval_text.configure(state="normal")
        self.approval_text.delete("1.0", "end")
        self.approval_text.insert("1.0", text)
        self.approval_text.configure(state="disabled")

    def _show_pending(self, pending: PendingApproval) -> None:
        self.pending = pending
        proposal = pending.proposal
        lines = [
            f"Action:    {proposal.action.name}",
            f"Summary:   {proposal.summary}",
            f"Risk:      {proposal.decision.risk.label}",
            f"Reason:    {proposal.decision.explain()}",
            "",
            "Arguments:",
        ]
        lines += [f"  {key} = {value}" for key, value in proposal.args.items()]
        if pending.preview is not None:
            lines += ["", f"Preview:   {pending.preview.message}"]
            for move in (pending.preview.data or {}).get("moves", [])[:20]:
                lines.append(f"  {move['from']} -> {move['to']}")
        self._set_approval_text("\n".join(lines))
        for button in (self.approve_button, self.edit_button, self.reject_button):
            button.state(["!disabled"])
        self.notebook.select(self.approvals_frame)
        self.status.set("Waiting for your approval")

    def _clear_pending(self) -> None:
        self.pending = None
        self._set_approval_text("Nothing is waiting for approval.")
        for button in (self.approve_button, self.edit_button, self.reject_button):
            button.state(["disabled"])

    def _approve(self) -> None:
        if self.pending:
            self.pending.resolve(True)
            self._write("  approved\n", "system")
            self._clear_pending()

    def _reject(self) -> None:
        if self.pending:
            self.pending.resolve(False)
            self._write("  rejected\n", "system")
            self._clear_pending()

    def _edit(self) -> None:
        if not self.pending:
            return
        proposal = self.pending.proposal
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit {proposal.action.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        entries: dict[str, tk.Entry] = {}
        for index, param in enumerate(proposal.action.params):
            ttk.Label(dialog, text=param.name).grid(row=index, column=0, sticky="e",
                                                    padx=6, pady=4)
            entry = ttk.Entry(dialog, width=60)
            entry.insert(0, str(proposal.args.get(param.name, "")))
            entry.grid(row=index, column=1, padx=6, pady=4)
            entries[param.name] = entry

        def submit() -> None:
            edited = {name: entry.get().strip() for name, entry in entries.items()}
            edited = {k: v for k, v in edited.items() if v != ""}
            dialog.destroy()
            if self.pending:
                self.pending.resolve(True, edited, note="edited")
                self._write("  approved with edits\n", "system")
                self._clear_pending()

        row = len(entries)
        ttk.Button(dialog, text="Approve edited action", command=submit).grid(
            row=row, column=0, columnspan=2, pady=8)

    # -- audit ----------------------------------------------------------
    def _build_audit_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Audit")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Search").pack(side="left")
        self.audit_search = ttk.Entry(top, width=30)
        self.audit_search.pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self._refresh_audit).pack(side="left")
        ttk.Button(top, text="Undo selected", command=self._undo_selected).pack(
            side="left", padx=6)

        columns = ("when", "action", "risk", "outcome", "message")
        self.audit_tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        widths = {"when": 150, "action": 130, "risk": 90, "outcome": 90, "message": 360}
        for column in columns:
            self.audit_tree.heading(column, text=column.title())
            self.audit_tree.column(column, width=widths[column], anchor="w")
        self.audit_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._refresh_audit()

    def _refresh_audit(self) -> None:
        for item in self.audit_tree.get_children():
            self.audit_tree.delete(item)
        rows = self.app.audit.recent_actions(200, self.audit_search.get().strip())
        for row in rows:
            self.audit_tree.insert("", "end", values=(
                row["created_at"], row["action"], row["risk"],
                row["outcome"] + (" (dry-run)" if row["dry_run"] else ""),
                row["message"] or "",
            ))

    def _undo_selected(self) -> None:
        outcome = self.app.undo.undo()
        if outcome is None:
            messagebox.showinfo("Undo", "There is nothing to undo.")
            return
        messagebox.showinfo("Undo", f"{outcome.entry.description}\n\n{outcome.result.message}")
        self._refresh_audit()

    # -- settings -------------------------------------------------------
    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Settings")
        config = self.app.config

        provider_box = ttk.LabelFrame(frame, text="AI provider")
        provider_box.pack(fill="x", padx=8, pady=8)
        self.provider_var = tk.StringVar(value=config.provider)
        ttk.Label(provider_box, text="Provider").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Combobox(provider_box, textvariable=self.provider_var, state="readonly",
                     values=sorted(PROVIDERS), width=22).grid(row=0, column=1, sticky="w")
        ttk.Label(provider_box, text="Model").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.model_var = tk.StringVar(value=config.provider_config().model)
        ttk.Entry(provider_box, textvariable=self.model_var, width=36).grid(
            row=1, column=1, sticky="w")
        ttk.Label(provider_box, text="API key").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.api_key_var = tk.StringVar(value="")
        ttk.Entry(provider_box, textvariable=self.api_key_var, width=36, show="•").grid(
            row=2, column=1, sticky="w")
        ttk.Label(provider_box, text="(leave blank to keep the stored key)").grid(
            row=2, column=2, sticky="w", padx=6)

        rules_box = ttk.LabelFrame(frame, text="Permission rules")
        rules_box.pack(fill="x", padx=8, pady=8)
        self.policy_vars: dict[str, tk.StringVar] = {}
        tiers = [
            ("Read-only (auto)", "policy_read_only"),
            ("Reversible", "policy_reversible"),
            ("Destructive", "policy_destructive"),
        ]
        for index, (label, attribute) in enumerate(tiers):
            ttk.Label(rules_box, text=label).grid(row=index, column=0, sticky="e",
                                                  padx=6, pady=4)
            var = tk.StringVar(value=getattr(config, attribute))
            ttk.Combobox(rules_box, textvariable=var, state="readonly",
                         values=list(POLICIES), width=14).grid(row=index, column=1, sticky="w")
            self.policy_vars[attribute] = var
        ttk.Label(rules_box, text="Destructive actions always ask, whatever this is set to.")\
            .grid(row=len(tiers), column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))

        folders_box = ttk.LabelFrame(frame, text="Allowed folders (blank = home only)")
        folders_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.folders_text = tk.Text(folders_box, height=5)
        self.folders_text.insert("1.0", "\n".join(config.allowed_folders))
        self.folders_text.pack(fill="both", expand=True, padx=6, pady=6)

        options_box = ttk.LabelFrame(frame, text="Options")
        options_box.pack(fill="x", padx=8, pady=8)
        self.option_vars = {
            "dry_run_default": tk.BooleanVar(value=config.dry_run_default),
            "deletes_to_recycle_bin": tk.BooleanVar(value=config.deletes_to_recycle_bin),
            "start_on_boot": tk.BooleanVar(value=config.start_on_boot),
            "log_prompts": tk.BooleanVar(value=config.log_prompts),
        }
        labels = {
            "dry_run_default": "Preview by default (dry run)",
            "deletes_to_recycle_bin": "Send deletes to the Recycle Bin",
            "start_on_boot": "Start AI OS when I sign in"
                             + ("" if startup_supported() else " (Windows only)"),
            "log_prompts": "Record my requests in the audit log",
        }
        for index, (name, var) in enumerate(self.option_vars.items()):
            check = ttk.Checkbutton(options_box, text=labels[name], variable=var)
            check.grid(row=index, column=0, sticky="w", padx=6, pady=2)
            if name == "start_on_boot" and not startup_supported():
                check.state(["disabled"])

        ttk.Button(frame, text="Save settings", command=self._save_settings).pack(
            padx=8, pady=(0, 12), anchor="e")

    def _save_settings(self) -> None:
        config = self.app.config
        config.provider = self.provider_var.get()
        provider_config = config.provider_config(config.provider)
        provider_config.model = self.model_var.get().strip()
        new_key = self.api_key_var.get().strip()
        if new_key:
            provider_config.api_key = new_key
            self.api_key_var.set("")
        for attribute, var in self.policy_vars.items():
            setattr(config, attribute, var.get())
        config.allowed_folders = [
            line.strip() for line in self.folders_text.get("1.0", "end").splitlines()
            if line.strip()
        ]
        for name, var in self.option_vars.items():
            setattr(config, name, bool(var.get()))

        message = ""
        if startup_supported():
            _, message = set_startup(config.start_on_boot)
        self.app.save()
        self.app.router.config = config
        self.app.safety.config = config
        self.status.set(f"Settings saved. {message}".strip())
        messagebox.showinfo("Settings", f"Saved.\n{message}".strip())

    # -- event loop -----------------------------------------------------
    def _poll(self) -> None:
        pending = self.approver.next_pending()
        if pending is not None and self.pending is None:
            self._show_pending(pending)

        with self._lock:
            finished, self.results = self.results, []
        for turn in finished:
            self._render_turn(turn)

        self.root.after(POLL_MS, self._poll)

    def _render_turn(self, turn: Any) -> None:
        self.busy = False
        self.send_button.state(["!disabled"])
        if isinstance(turn, Exception):
            self._write(f"error: {turn}\n", "error")
            self.status.set("Something went wrong")
            return
        for outcome in turn.outcomes:
            self._write(f"  [{outcome.status}] {outcome.proposal.summary}\n"
                        f"      {outcome.message}\n", "system")
        if turn.reply:
            self._write(f"ai-os: {turn.reply}\n", "error" if turn.error else "assistant")
        self.status.set("Ready")
        self._refresh_audit()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_gui(app: AiOS) -> int:
    if not TK_AVAILABLE:
        print("The GUI needs tkinter, which is not available in this Python build.\n"
              "On Windows it ships with the standard installer; on Linux install "
              "python3-tk. The CLI works either way: ai-os chat")
        return 1
    return AiOsWindow(app).run()
