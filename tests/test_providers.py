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

    def test_context_window_unknown_fallback(self):
        p = get_provider("anthropic", "k")
        assert p.context_window("unknown-model") == 200_000

    def test_pricing_sonnet_known(self):
        p = get_provider("anthropic", "k")
        price = p.pricing("claude-sonnet-4-5")
        assert price is not None
        assert price.input_usd_per_1m == 3.0
        assert price.output_usd_per_1m == 15.0

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


class TestAnthropicStreamErrorPaths:
    """Stream error handling: APITimeoutError + unexpected exceptions must
    surface as `error` StreamEvents instead of crashing _consume silently.

    Regression: a thinking model that took >60s caused APITimeoutError, which
    was NOT in the except list. The async coroutine crashed, the producer
    thread logged + put None, and the consumer saw zero events — TUI froze
    on `thinking · Ns` with no content forever.
    """

    def _make_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.delenv("LOOM_THINKING_BUDGET", raising=False)
        from loom.agent.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key="k")

    def _patch_stream(self, monkeypatch, provider, exc_to_raise):
        """Replace messages.stream with an async ctx mgr that raises on __aenter__."""

        class _RaisingStream:
            async def __aenter__(self_inner):
                raise exc_to_raise

            async def __aexit__(self_inner, *_a):
                return False

        def _stream(**kwargs):
            return _RaisingStream()

        monkeypatch.setattr(provider._async_client.messages, "stream", _stream)

    def _make_request(self):
        from loom.agent.providers.types import ProviderRequest
        return ProviderRequest(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            max_tokens=1024,
            model="anthropic/claude-haiku-3-5",
        )

    def test_timeout_surfaces_as_error_event(self, monkeypatch):
        import anthropic

        provider = self._make_provider(monkeypatch)
        timeout_exc = anthropic.APITimeoutError(request=None)  # type: ignore[arg-type]
        self._patch_stream(monkeypatch, provider, timeout_exc)

        events = list(provider.stream(self._make_request()))
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].error_code == ProviderErrorCode.TIMEOUT
        assert "timed out" in (events[0].error_message or "").lower()

    def test_unexpected_exception_surfaces_as_error_event(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        self._patch_stream(monkeypatch, provider, RuntimeError("boom"))

        events = list(provider.stream(self._make_request()))
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].error_code == ProviderErrorCode.UNKNOWN
        assert "RuntimeError" in (events[0].error_message or "")
        assert "boom" in (events[0].error_message or "")

    def test_connection_error_still_works(self, monkeypatch):
        import anthropic

        provider = self._make_provider(monkeypatch)
        conn_exc = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
        self._patch_stream(monkeypatch, provider, conn_exc)

        events = list(provider.stream(self._make_request()))
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].error_code == ProviderErrorCode.NETWORK

    def test_timeout_env_var_default_is_600s(self, monkeypatch):
        monkeypatch.delenv("LOOM_LLM_TIMEOUT", raising=False)
        provider = self._make_provider(monkeypatch)
        assert provider._async_client.timeout == 600.0

    def test_timeout_env_var_custom(self, monkeypatch):
        monkeypatch.setenv("LOOM_LLM_TIMEOUT", "120")
        provider = self._make_provider(monkeypatch)
        assert provider._async_client.timeout == 120.0

    def test_timeout_env_var_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("LOOM_LLM_TIMEOUT", "not_a_number")
        provider = self._make_provider(monkeypatch)
        assert provider._async_client.timeout == 600.0

    def test_timeout_env_var_zero_falls_back(self, monkeypatch):
        monkeypatch.setenv("LOOM_LLM_TIMEOUT", "0")
        provider = self._make_provider(monkeypatch)
        assert provider._async_client.timeout == 600.0


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
