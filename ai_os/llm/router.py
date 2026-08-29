"""The AI router: pick a provider by name, keeping core logic model-agnostic."""

from __future__ import annotations

from typing import Any

from ..config import Config
from .anthropic import AnthropicProvider
from .base import LLMError, LLMResponse, Message, Provider, ToolCall
from .local import LocalRuleProvider
from .openai_compat import OpenAICompatibleProvider

# Phase 1 ships Claude plus one OpenAI-compatible provider. Phase 3 adds the
# rest; the OpenAI-compatible class already covers most of them via base_url.
PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
    "local": LocalRuleProvider,
}


class LLMRouter:
    """Builds the configured provider and forwards chat requests to it."""

    def __init__(self, config: Config) -> None:
        self.config = config

    @staticmethod
    def provider_names() -> list[str]:
        return sorted(PROVIDERS)

    def build(self, name: str | None = None) -> Provider:
        provider_name = (name or self.config.provider or "local").lower()
        cls = PROVIDERS.get(provider_name)
        if cls is None:
            raise LLMError(
                f"Unknown provider {provider_name!r}. "
                f"Available: {', '.join(self.provider_names())}"
            )
        settings = self.config.provider_config(provider_name)
        provider = cls(
            api_key=settings.resolved_key(provider_name),
            model=settings.resolved_model(provider_name),
            base_url=settings.base_url,
        )
        provider.name = provider_name
        return provider

    def chat(self, messages: list[Message], tools: list[dict[str, Any]],
             provider: str | None = None) -> LLMResponse:
        return self.build(provider).chat(messages, tools)


__all__ = ["LLMRouter", "LLMError", "LLMResponse", "Message", "Provider", "ToolCall",
           "PROVIDERS"]
