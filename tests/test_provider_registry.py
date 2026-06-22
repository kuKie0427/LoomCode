"""Tests for the provider registry: parse_model_id, get_provider, resolve_model_id.

Covers the P1 deliverable: anthropic + openai + deepseek + ollama + openrouter
are all registered and discoverable through the unified registry. A 6th
provider `custom` is mentioned in the plan but is not yet implemented
in P1 (P2 will add a generic 'custom' provider for user-supplied
base_urls); we therefore assert `len(PROVIDERS) >= 5` for the P1
deliverable, and the plan's broader `>= 6` check is deferred to P2.
"""

from __future__ import annotations

import pytest

from loom.agent.providers import (
    PROVIDERS,
    LLMProvider,
    get_provider,
    parse_model_id,
    resolve_model_id,
)
from loom.agent.providers.types import (
    ProviderError,
    ProviderErrorCode,
)


class TestProvidersDict:
    def test_contains_anthropic(self):
        assert "anthropic" in PROVIDERS

    def test_contains_openai(self):
        assert "openai" in PROVIDERS

    def test_contains_deepseek(self):
        assert "deepseek" in PROVIDERS

    def test_contains_ollama(self):
        assert "ollama" in PROVIDERS

    def test_contains_openrouter(self):
        assert "openrouter" in PROVIDERS

    def test_minimum_5_providers_p1(self):
        # P1 deliverable: anthropic, openai, deepseek, ollama, openrouter
        assert len(PROVIDERS) >= 5

    def test_all_entries_are_llmprovider_subclasses(self):
        for pid, cls in PROVIDERS.items():
            assert issubclass(cls, LLMProvider), f"{pid} is not an LLMProvider"


class TestGetProvider:
    def test_unknown_raises_provider_error(self):
        with pytest.raises(ProviderError) as exc_info:
            get_provider("nonexistent", "k")
        assert exc_info.value.code == ProviderErrorCode.UNKNOWN_PROVIDER

    def test_anthropic_returns_instance(self):
        p = get_provider("anthropic", "k")
        assert isinstance(p, LLMProvider)
        assert p.provider_id == "anthropic"

    def test_openai_returns_instance(self):
        p = get_provider("openai", "k")
        assert p.provider_id == "openai"

    def test_deepseek_returns_instance(self):
        p = get_provider("deepseek", "k")
        assert p.provider_id == "deepseek"

    def test_ollama_returns_instance(self):
        p = get_provider("ollama", "")
        assert p.provider_id == "ollama"

    def test_openrouter_returns_instance(self):
        p = get_provider("openrouter", "k")
        assert p.provider_id == "openrouter"

    def test_unpacking_parse_model_id_signature(self):
        # The smoke test in the plan uses this exact shape:
        # get_provider(*parse_model_id('deepseek/deepseek-chat'), api_key='fake')
        p = get_provider(*parse_model_id("deepseek/deepseek-chat"), api_key="fake")
        assert p.provider_id == "deepseek"


class TestResolveModelId:
    def test_resolve_anthropic(self):
        provider_id, model_id, env_var = resolve_model_id("anthropic/claude-sonnet-4-5")
        assert provider_id == "anthropic"
        assert model_id == "claude-sonnet-4-5"
        assert env_var == "ANTHROPIC_API_KEY"

    def test_resolve_openai(self):
        provider_id, model_id, env_var = resolve_model_id("openai/gpt-4o")
        assert provider_id == "openai"
        assert model_id == "gpt-4o"
        assert env_var == "OPENAI_API_KEY"

    def test_resolve_deepseek(self):
        provider_id, model_id, env_var = resolve_model_id("deepseek/deepseek-chat")
        assert provider_id == "deepseek"
        assert model_id == "deepseek-chat"
        assert env_var == "DEEPSEEK_API_KEY"

    def test_resolve_ollama(self):
        provider_id, model_id, env_var = resolve_model_id("ollama/llama3")
        assert provider_id == "ollama"
        assert model_id == "llama3"
        assert env_var == "OLLAMA_API_KEY"

    def test_resolve_openrouter_with_slash_in_model_id(self):
        # `parse_model_id` only splits on the first `/`, so the model_id
        # segment retains the `anthropic/` prefix.
        provider_id, model_id, env_var = resolve_model_id(
            "openrouter/anthropic/claude-3.5-sonnet"
        )
        assert provider_id == "openrouter"
        assert model_id == "anthropic/claude-3.5-sonnet"
        assert env_var == "OPENROUTER_API_KEY"

    def test_resolve_unknown_provider_returns_empty_env(self):
        provider_id, model_id, env_var = resolve_model_id("nonexistent/foo")
        assert provider_id == "nonexistent"
        assert model_id == "foo"
        assert env_var == ""  # unknown → empty


class TestParseModelIdWithProfileCompat:
    """Verify the parse → get_provider → context_window path end-to-end."""

    def test_anthropic_end_to_end(self):
        p = get_provider(*parse_model_id("anthropic/claude-sonnet-4-5"), api_key="k")
        assert p.provider_id == "anthropic"
        assert p.context_window("claude-sonnet-4-5") == 200_000

    def test_openai_end_to_end(self):
        p = get_provider(*parse_model_id("openai/gpt-4o"), api_key="k")
        assert p.provider_id == "openai"
        assert p.context_window("gpt-4o") == 128_000

    def test_deepseek_end_to_end(self):
        p = get_provider(*parse_model_id("deepseek/deepseek-chat"), api_key="k")
        assert p.provider_id == "deepseek"
        assert p.context_window("deepseek-chat") == 64_000

    def test_ollama_end_to_end(self):
        p = get_provider(*parse_model_id("ollama/llama3"), api_key="")
        assert p.provider_id == "ollama"
        assert p.context_window("llama3") == 8_192

    def test_openrouter_end_to_end(self):
        p = get_provider(
            *parse_model_id("openrouter/anthropic/claude-3.5-sonnet"), api_key="k"
        )
        assert p.provider_id == "openrouter"
        assert p.context_window("anthropic/claude-3.5-sonnet") == 32_000  # fallback
