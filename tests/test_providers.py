"""Tests for the LLMProvider abstraction (Wave 4 of f-multi-model-providers-p0).

Verifies:
  - parse_model_id splits on first / and defaults to anthropic
  - get_provider raises on unknown provider
  - AnthropicProvider has correct context windows and pricing
  - LLMClient delegates stream to provider
"""

from __future__ import annotations

import pytest

from loom.agent.providers import (
    PROVIDERS,
    LLMProvider,
    PricingInfo,
    StreamEvent,
    Usage,
    get_provider,
    parse_model_id,
)
from loom.agent.providers.base import LLMProvider as LLMProviderClass
from loom.agent.providers.types import (
    ProviderError,
    ProviderErrorCode,
)


class TestParseModelId:
    def test_with_prefix_anthropic(self):
        assert parse_model_id("anthropic/claude-sonnet-4-5") == ("anthropic", "claude-sonnet-4-5")

    def test_with_prefix_openai(self):
        assert parse_model_id("openai/gpt-4o") == ("openai", "gpt-4o")

    def test_with_slash_in_model_id(self):
        assert parse_model_id("openrouter/anthropic/claude-3.5-sonnet") == (
            "openrouter",
            "anthropic/claude-3.5-sonnet",
        )

    def test_without_prefix_defaults_to_anthropic(self):
        assert parse_model_id("claude-sonnet-4-5") == ("anthropic", "claude-sonnet-4-5")

    def test_strips_whitespace(self):
        assert parse_model_id("  openai/gpt-4o  ") == ("openai", "gpt-4o")


class TestGetProvider:
    def test_anthropic_returns_instance(self):
        p = get_provider("anthropic", "test-key")
        assert isinstance(p, LLMProvider)
        assert p.provider_id == "anthropic"

    def test_unknown_raises(self):
        with pytest.raises(ProviderError) as exc_info:
            get_provider("nonexistent", "k")
        assert exc_info.value.code == ProviderErrorCode.UNKNOWN_PROVIDER

    def test_base_url_passed_through(self):
        p = get_provider("anthropic", "k", base_url="https://custom.example.com")
        assert p.base_url == "https://custom.example.com"


class TestAnthropicProvider:
    def test_context_window_sonnet(self):
        p = get_provider("anthropic", "k")
        assert p.context_window("claude-sonnet-4-5") == 200_000

    def test_context_window_deepseek(self):
        p = get_provider("anthropic", "k")
        assert p.context_window("deepseek-v4-flash") == 64_000

    def test_context_window_unknown_fallback(self):
        p = get_provider("anthropic", "k")
        assert p.context_window("unknown-model") == 200_000

    def test_pricing_sonnet_known(self):
        p = get_provider("anthropic", "k")
        price = p.pricing("claude-sonnet-4-5")
        assert price is not None
        assert price.input_usd_per_1m == 3.0
        assert price.output_usd_per_1m == 15.0

    def test_pricing_deepseek_zero(self):
        p = get_provider("anthropic", "k")
        price = p.pricing("deepseek-v4-flash")
        assert price is not None
        assert price.input_usd_per_1m == 0.0

    def test_pricing_unknown_none(self):
        p = get_provider("anthropic", "k")
        assert p.pricing("nonexistent-model") is None

    def test_supported_models_contains_sonnet(self):
        p = get_provider("anthropic", "k")
        assert "claude-sonnet-4-5" in p.supported_models

    def test_env_var_anthropic(self):
        p = get_provider("anthropic", "k")
        assert p.env_var == "ANTHROPIC_API_KEY"

    def test_display_name(self):
        p = get_provider("anthropic", "k")
        assert p.display_name == "Anthropic"


class TestAnthropicThinkingParam:
    """Extended thinking wiring: LOOM_THINKING_BUDGET env var opt-out + budget control."""

    def test_default_for_capable_model(self, monkeypatch) -> None:
        monkeypatch.delenv("LOOM_THINKING_BUDGET", raising=False)
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") == {
            "type": "enabled",
            "budget_tokens": 8000,
        }

    def test_default_for_opus_4_1(self, monkeypatch) -> None:
        monkeypatch.delenv("LOOM_THINKING_BUDGET", raising=False)
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-opus-4-1") == {
            "type": "enabled",
            "budget_tokens": 8000,
        }

    def test_disabled_for_non_capable_model(self, monkeypatch) -> None:
        monkeypatch.delenv("LOOM_THINKING_BUDGET", raising=False)
        from loom.agent.providers.anthropic import _build_thinking_param
        # Claude 3.5 family does not support extended thinking.
        assert _build_thinking_param("claude-haiku-3-5") is None

    def test_opt_out_via_zero(self, monkeypatch) -> None:
        monkeypatch.setenv("LOOM_THINKING_BUDGET", "0")
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") is None

    def test_disabled_when_budget_below_minimum(self, monkeypatch) -> None:
        # Anthropic requires budget_tokens >= 1024.
        monkeypatch.setenv("LOOM_THINKING_BUDGET", "500")
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") is None

    def test_custom_budget(self, monkeypatch) -> None:
        monkeypatch.setenv("LOOM_THINKING_BUDGET", "15000")
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") == {
            "type": "enabled",
            "budget_tokens": 15000,
        }

    def test_invalid_budget_returns_none(self, monkeypatch) -> None:
        # Malformed env var → disable rather than crash the agent loop.
        monkeypatch.setenv("LOOM_THINKING_BUDGET", "not_a_number")
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") is None

    def test_negative_budget_disables(self, monkeypatch) -> None:
        monkeypatch.setenv("LOOM_THINKING_BUDGET", "-100")
        from loom.agent.providers.anthropic import _build_thinking_param
        assert _build_thinking_param("claude-sonnet-4-5") is None


class TestProvidersDict:
    def test_contains_anthropic(self):
        assert "anthropic" in PROVIDERS

    def test_anthropic_class_is_llmprovider_subclass(self):
        from loom.agent.providers.anthropic import AnthropicProvider
        assert issubclass(AnthropicProvider, LLMProviderClass)


class TestTypes:
    def test_stream_event_default_kind(self):
        ev = StreamEvent(kind="text")
        assert ev.text == ""
        assert ev.tool_input is None

    def test_usage_total_tokens(self):
        u = Usage(input_tokens=100, output_tokens=50)
        assert u.total_tokens == 150

    def test_pricing_info_frozen(self):
        from dataclasses import FrozenInstanceError
        p = PricingInfo(input_usd_per_1m=1.0)
        with pytest.raises(FrozenInstanceError):
            p.input_usd_per_1m = 2.0

    def test_pricing_info_all_optional(self):
        p = PricingInfo()
        assert p.input_usd_per_1m is None
        assert p.output_usd_per_1m is None
        assert p.cache_read_usd_per_1m is None
        assert p.cache_write_usd_per_1m is None
