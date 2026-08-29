"""Configuration for AI OS.

The config file is plain JSON so it can be inspected and hand-edited. API keys
are kept in the same file but the file is created with owner-only permissions,
and keys are never written to the audit log or printed by the CLI.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import config_path

# Permission policy values applied per risk tier.
POLICY_AUTO = "auto"          # run without asking
POLICY_CONFIRM = "confirm"    # ask for approval (with the option to edit)
POLICY_DENY = "deny"          # never run
POLICIES = (POLICY_AUTO, POLICY_CONFIRM, POLICY_DENY)

ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "local": "rules-v1",
}


@dataclass
class ProviderConfig:
    """Settings for a single LLM provider."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""

    def resolved_key(self, provider: str) -> str:
        """Config value first, then the provider's conventional env var."""
        return self.api_key or os.environ.get(ENV_KEYS.get(provider, ""), "")

    def resolved_model(self, provider: str) -> str:
        return self.model or DEFAULT_MODELS.get(provider, "")


@dataclass
class Config:
    """User-facing settings, surfaced by both the CLI and the Settings page."""

    provider: str = "local"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    # Permission rules, one policy per risk tier.
    policy_read_only: str = POLICY_AUTO
    policy_reversible: str = POLICY_CONFIRM
    policy_destructive: str = POLICY_CONFIRM

    # Folders the assistant may touch. Empty means "home directory only".
    allowed_folders: list[str] = field(default_factory=list)
    # "enforce" denies paths outside the whitelist; "warn" downgrades to approval.
    whitelist_mode: str = "enforce"

    dry_run_default: bool = False
    deletes_to_recycle_bin: bool = True
    start_on_boot: bool = False
    log_prompts: bool = True
    max_tool_iterations: int = 4

    def provider_config(self, name: str | None = None) -> ProviderConfig:
        name = name or self.provider
        return self.providers.setdefault(name, ProviderConfig())

    def effective_allowed_folders(self) -> list[Path]:
        if not self.allowed_folders:
            return [Path.home()]
        return [Path(p).expanduser() for p in self.allowed_folders]

    # -- persistence ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["providers"] = {k: asdict(v) for k, v in self.providers.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in known}
        providers = payload.pop("providers", {}) or {}
        cfg = cls(**payload)
        cfg.providers = {
            name: ProviderConfig(**{k: v for k, v in (values or {}).items()
                                    if k in {"api_key", "model", "base_url"}})
            for name, values in providers.items()
        }
        return cfg

    def redacted(self) -> dict[str, Any]:
        """Config safe to display or log: API keys replaced by a marker."""
        data = self.to_dict()
        for provider in data["providers"].values():
            if provider.get("api_key"):
                provider["api_key"] = "***set***"
        return data


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()
    return Config.from_dict(data)


def save_config(config: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2, sort_keys=True)
    path.write_text(payload, encoding="utf-8")
    _restrict_permissions(path)
    return path


def _restrict_permissions(path: Path) -> None:
    """Best-effort owner-only permissions; a no-op where chmod is meaningless."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
