"""The router keeps core logic model-agnostic; each provider owns its wire format."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ai_os.config import Config, ProviderConfig
from ai_os.llm import LLMError, LLMRouter, Message, ToolCall
from ai_os.llm.anthropic import AnthropicProvider
from ai_os.llm.local import LocalRuleProvider, resolve_folder
from ai_os.llm.openai_compat import OpenAICompatibleProvider
from ai_os.skills import default_registry

TOOLS = [{
    "name": "fs_delete",
    "description": "Delete a file. (risk: destructive)",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]},
}]


def test_router_lists_and_builds_providers():
    router = LLMRouter(Config())
    assert {"anthropic", "openai", "local"} <= set(router.provider_names())
    assert router.build("local").name == "local"


def test_router_rejects_an_unknown_provider():
    with pytest.raises(LLMError):
        LLMRouter(Config()).build("not-a-provider")


def test_router_reads_keys_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    provider = LLMRouter(Config(provider="anthropic")).build()
    assert provider.api_key == "sk-from-env"


def test_config_key_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    config = Config(provider="anthropic",
                    providers={"anthropic": ProviderConfig(api_key="sk-stored")})
    assert LLMRouter(config).build().api_key == "sk-stored"


def test_switching_provider_needs_no_core_change():
    """Swapping the model is a config change, nothing more."""
    config = Config(provider="local")
    router = LLMRouter(config)
    assert router.build().name == "local"
    config.provider = "openai"
    assert router.build().name == "openai"


def test_provider_without_a_key_reports_it_clearly():
    with pytest.raises(LLMError) as excinfo:
        AnthropicProvider(api_key="", model="claude-sonnet-4-5").chat(
            [Message("user", "hi")], TOOLS)
    assert "API key" in str(excinfo.value)


def test_anthropic_request_and_response_round_trip(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, payload, headers, timeout=60):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "Deleting that now."},
                {"type": "tool_use", "id": "tu_1", "name": "fs_delete",
                 "input": {"path": "C:/Users/x/a.txt"}},
            ],
        }

    monkeypatch.setattr("ai_os.llm.anthropic.post_json", fake_post)
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-5")
    response = provider.chat(
        [Message("system", "be careful"), Message("user", "delete a.txt")], TOOLS)

    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["payload"]["system"] == "be careful"
    assert captured["payload"]["tools"][0]["input_schema"]["required"] == ["path"]
    assert response.text == "Deleting that now."
    assert response.tool_calls[0].name == "fs_delete"
    assert response.tool_calls[0].arguments == {"path": "C:/Users/x/a.txt"}


def test_anthropic_encodes_tool_results_as_user_blocks(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, payload, headers, timeout=60):
        captured["payload"] = payload
        return {"content": [{"type": "text", "text": "done"}]}

    monkeypatch.setattr("ai_os.llm.anthropic.post_json", fake_post)
    AnthropicProvider(api_key="sk", model="m").chat([
        Message("user", "delete a.txt"),
        Message("assistant", "", tool_calls=[ToolCall("tu_1", "fs_delete", {"path": "a"})]),
        Message("tool", '{"status": "executed"}', tool_call_id="tu_1"),
    ], TOOLS)
    messages = captured["payload"]["messages"]
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "tu_1"


def test_openai_request_and_response_round_trip(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, payload, headers, timeout=60):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "model": "gpt-4o-mini",
            "choices": [{"message": {
                "content": "On it.",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "fs_delete",
                                 "arguments": json.dumps({"path": "C:/a.txt"})},
                }],
            }}],
        }

    monkeypatch.setattr("ai_os.llm.openai_compat.post_json", fake_post)
    response = OpenAICompatibleProvider(api_key="sk-o", model="gpt-4o-mini").chat(
        [Message("user", "delete a.txt")], TOOLS)

    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer sk-o"
    assert captured["payload"]["tools"][0]["type"] == "function"
    assert response.tool_calls[0].arguments == {"path": "C:/a.txt"}
    assert response.text == "On it."


def test_openai_base_url_can_point_at_another_compatible_provider(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, payload, headers, timeout=60):
        captured["url"] = url
        return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr("ai_os.llm.openai_compat.post_json", fake_post)
    OpenAICompatibleProvider(api_key="k", model="m",
                             base_url="https://api.groq.com/openai/v1").chat(
        [Message("user", "hi")], [])
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_openai_rejects_malformed_tool_arguments(monkeypatch):
    monkeypatch.setattr("ai_os.llm.openai_compat.post_json", lambda *a, **k: {
        "choices": [{"message": {"tool_calls": [
            {"id": "1", "function": {"name": "fs_delete", "arguments": "{not json"}}]}}]})
    with pytest.raises(LLMError):
        OpenAICompatibleProvider(api_key="k", model="m").chat([Message("user", "x")], [])


@pytest.mark.parametrize("request_text,expected", [
    ("organize my desktop", "fs_organize"),
    ("what time is it", "system_time"),
    ("open chrome", "app_open"),
    ("shut down", "power_shutdown"),
    ("lock my laptop", "power_lock"),
    ("go to github.com", "browser_open"),
    ("what is running", "app_list_running"),
])
def test_local_provider_matches_common_phrasings(request_text, expected):
    tools = default_registry().tool_schemas()
    response = LocalRuleProvider().chat([Message("user", request_text)], tools)
    assert [call.name for call in response.tool_calls] == [expected]


def test_local_provider_only_proposes_registered_tools():
    response = LocalRuleProvider().chat([Message("user", "shut down")], [])
    assert response.tool_calls == []
    assert "offline rule provider" in response.text


def test_local_provider_says_so_when_it_cannot_help():
    tools = default_registry().tool_schemas()
    response = LocalRuleProvider().chat(
        [Message("user", "write me a poem about disks")], tools)
    assert response.tool_calls == []
    assert "could not match" in response.text


def test_local_provider_summarizes_tool_results():
    response = LocalRuleProvider().chat([
        Message("user", "what time is it"),
        Message("tool", json.dumps({"status": "executed", "message": "It is 10:00."})),
    ], [])
    assert response.text == "It is 10:00."


def test_folder_shorthand_resolves_to_real_paths(home):
    assert resolve_folder("my desktop") == str(home / "Desktop")
    assert resolve_folder("downloads/old.txt") == str(home / "Downloads" / "old.txt")
