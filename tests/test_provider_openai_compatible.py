"""Unit tests for the OpenAI-compatible provider (DeepSeek, Ollama, OpenRouter).

Verifies:
  - DeepSeek / Ollama / OpenRouter profiles are registered
  - Per-profile base_url / env_var / supported_models are pinned
  - Context windows + pricing look up correctly
  - Ollama works without an API key (local default)
  - OpenRouter preserves slashes in the model_id
  - Shared streaming logic is used (via the openai_chat_stream seam)
"""

from __future__ import annotations

import pytest

from loom.agent.providers import (
    MODEL_PROFILES,
    PROVIDERS,
    get_provider,
    parse_model_id,
)
from loom.agent.providers._openai_shared import (
    DEFAULT_WINDOW,
    get_profile,
    openai_chat_stream,
)
from loom.agent.providers._openai_shared import (
    MODEL_PROFILES as SHARED_PROFILES,
)
from loom.agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    register_compatible_profiles,
)
from loom.agent.providers.types import ProviderRequest

# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------

class TestProfileResolution:
    def test_deepseek_provider_resolved_by_parse_model_id(self):
        provider_id, model_id = parse_model_id("deepseek/deepseek-chat")
        assert provider_id == "deepseek"
        assert model_id == "deepseek-chat"
        p = get_provider(provider_id, api_key="x")
        assert p.provider_id == "deepseek"
        assert p.base_url == "https://api.deepseek.com/v1"

    def test_ollama_provider_default_base_url_no_key_required(self):
        p = get_provider("ollama", api_key="")
        assert p.provider_id == "ollama"
        # No key required for local Ollama — base_url is the local default
        assert p.base_url == "http://localhost:11434/v1"
        assert p.api_key == ""  # not auto-populated from env

    def test_openrouter_provider_with_provider_prefix_in_model_id(self):
        provider_id, model_id = parse_model_id("openrouter/anthropic/claude-3.5-sonnet")
        assert provider_id == "openrouter"
        assert model_id == "anthropic/claude-3.5-sonnet"
        p = get_provider(provider_id, api_key="x")
        assert p.base_url == "https://openrouter.ai/api/v1"
        assert "anthropic/claude-3.5-sonnet" in p.supported_models


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_deepseek_registered(self):
        assert "deepseek" in PROVIDERS
        assert issubclass(PROVIDERS["deepseek"], OpenAICompatibleProvider)

    def test_ollama_registered(self):
        assert "ollama" in PROVIDERS
        assert issubclass(PROVIDERS["ollama"], OpenAICompatibleProvider)

    def test_openrouter_registered(self):
        assert "openrouter" in PROVIDERS
        assert issubclass(PROVIDERS["openrouter"], OpenAICompatibleProvider)

    def test_openai_compatible_profiles_match_model_profiles(self):
        registered = {k for k in PROVIDERS if k != "anthropic" and k != "openai"}
        assert registered == set(MODEL_PROFILES)
        assert registered == set(SHARED_PROFILES)

    def test_register_compatible_profiles_is_idempotent(self):
        # Re-registering must not raise or duplicate entries.
        before = dict(PROVIDERS)
        register_compatible_profiles()
        assert PROVIDERS == before

    def test_get_profile_helper(self):
        assert get_profile("deepseek") is not None
        assert get_profile("ollama") is not None
        assert get_profile("openrouter") is not None
        assert get_profile("nonexistent") is None


# ---------------------------------------------------------------------------
# Context windows + pricing
# ---------------------------------------------------------------------------

class TestDeepSeekContextAndPricing:
    def test_context_window_64k(self):
        p = get_provider("deepseek", api_key="x")
        assert p.context_window("deepseek-chat") == 64_000
        assert p.context_window("deepseek-reasoner") == 64_000

    def test_pricing_deepseek_chat(self):
        p = get_provider("deepseek", api_key="x")
        price = p.pricing("deepseek-chat")
        assert price is not None
        # Plan says 0.27 / 1.10 / 0.07 / 0.27 (input/output/cache_read/cache_write)
        assert price.input_usd_per_1m == pytest.approx(0.27)
        assert price.output_usd_per_1m == pytest.approx(1.10)

    def test_pricing_deepseek_reasoner_higher(self):
        p = get_provider("deepseek", api_key="x")
        chat = p.pricing("deepseek-chat")
        reasoner = p.pricing("deepseek-reasoner")
        assert reasoner is not None
        # Reasoner is ~2x chat for input
        assert reasoner.input_usd_per_1m > chat.input_usd_per_1m
        assert reasoner.output_usd_per_1m > chat.output_usd_per_1m

    def test_context_window_unknown_fallback(self):
        p = get_provider("deepseek", api_key="x")
        assert p.context_window("nonexistent") == DEFAULT_WINDOW


class TestOllamaContextAndPricing:
    def test_context_window_llama3_8k(self):
        p = get_provider("ollama", api_key="")
        assert p.context_window("llama3") == 8_192

    def test_context_window_llama31_128k(self):
        p = get_provider("ollama", api_key="")
        assert p.context_window("llama3.1") == 128_000

    def test_pricing_free(self):
        p = get_provider("ollama", api_key="")
        # Pricing is empty for local models
        assert p.pricing("llama3") is None
        assert p.pricing("llama3.1") is None


class TestOpenRouterContextAndPricing:
    def test_context_window_unknown_fallback_32k(self):
        p = get_provider("openrouter", api_key="x")
        # All OpenRouter models fall through to the default since the
        # profile's context_windows dict is empty
        assert p.context_window("anthropic/claude-3.5-sonnet") == DEFAULT_WINDOW
        assert p.context_window("unknown/model") == DEFAULT_WINDOW

    def test_pricing_see_openrouter(self):
        p = get_provider("openrouter", api_key="x")
        # OpenRouter pricing is empty (resale model)
        assert p.pricing("anthropic/claude-3.5-sonnet") is None


# ---------------------------------------------------------------------------
# Per-profile ClassVar identifiers
# ---------------------------------------------------------------------------

class TestProfileMetadata:
    def test_deepseek_metadata(self):
        p = get_provider("deepseek", api_key="x")
        assert p.env_var == "DEEPSEEK_API_KEY"
        assert p.display_name == "DeepSeek"
        assert "deepseek-chat" in p.supported_models
        assert "deepseek-reasoner" in p.supported_models

    def test_ollama_metadata(self):
        p = get_provider("ollama", api_key="")
        assert p.env_var == "OLLAMA_API_KEY"
        assert p.display_name == "Ollama (local)"
        assert "llama3" in p.supported_models
        assert "qwen2.5" in p.supported_models

    def test_openrouter_metadata(self):
        p = get_provider("openrouter", api_key="x")
        assert p.env_var == "OPENROUTER_API_KEY"
        assert p.display_name == "OpenRouter"
        assert "openai/gpt-4o" in p.supported_models


# ---------------------------------------------------------------------------
# Shared streaming logic — verify the same `openai_chat_stream` is used
# ---------------------------------------------------------------------------

class TestSharedStreamingLogic:
    def test_openai_compatible_uses_shared_stream_logic(self):
        """Both providers must call the same underlying streaming function.
        We assert by patching the seam and verifying both code paths
        delegate to it.
        """
        called_with: list[dict[str, str]] = []

        original = openai_chat_stream

        def spy(request, **kwargs):
            called_with.append({"provider": kwargs.get("provider", "")})
            yield from original(request, **kwargs)

        import loom.agent.providers._openai_shared as shared
        import loom.agent.providers.openai as openai_mod
        import loom.agent.providers.openai_compatible as compat_mod

        shared.openai_chat_stream = spy
        openai_mod.openai_chat_stream = spy
        compat_mod.openai_chat_stream = spy
        try:
            req = ProviderRequest(
                system="x",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                model="deepseek/deepseek-chat",
            )
            p = get_provider("deepseek", api_key="x")
            iterator = p.stream(req)
            # No real server → some httpx network error. We assert on the
            # side-effect (spy called) afterwards; the exception type is
            # incidental.
            with pytest.raises(Exception):  # noqa: B017
                list(iterator)
        finally:
            shared.openai_chat_stream = original
            openai_mod.openai_chat_stream = original
            compat_mod.openai_chat_stream = original

        assert any(c["provider"] == "deepseek" for c in called_with)


# ---------------------------------------------------------------------------
# count_tokens heuristic (no API call)
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_deepseek_count_tokens_uses_heuristic(self):
        p = get_provider("deepseek", api_key="x")
        result = p.count_tokens(
            [{"role": "user", "content": "Hello, world!"}],
            "deepseek-chat",
        )
        assert result > 0
        assert isinstance(result, int)

    def test_ollama_count_tokens(self):
        p = get_provider("ollama", api_key="")
        result = p.count_tokens(
            [{"role": "user", "content": "test"}],
            "llama3",
        )
        assert result > 0
