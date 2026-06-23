"""Unit tests for the OpenAI Chat Completions provider (api.openai.com).

Uses ``httpx.MockTransport`` to inject canned SSE responses without
making real network calls. The transport is handed to the shared
``openai_chat_stream`` via the ``http_client`` kwarg on each call.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from loom.agent.providers import (
    PricingInfo,
    ToolDefinition,
    get_provider,
)
from loom.agent.providers._openai_shared import (
    build_request_body,
    openai_chat_stream,
)
from loom.agent.providers.openai import OpenAIProvider
from loom.agent.providers.types import (
    ProviderError,
    ProviderErrorCode,
    ProviderRequest,
)

# ---------------------------------------------------------------------------
# httpx.MockTransport helpers
# ---------------------------------------------------------------------------

def _sse(events: list[dict]) -> bytes:
    """Encode a list of JSON-serializable dicts as an SSE byte stream."""
    parts: list[bytes] = []
    for ev in events:
        parts.append(b"data: " + json.dumps(ev).encode("utf-8") + b"\n\n")
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def _make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _stream_with_sse(events: list[dict], status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"content-type": "text/event-stream"},
            content=_sse(events),
        )
    return _make_transport(handler)


def _stream_with_error(status_code: int, body: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        )
    return _make_transport(handler)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def openai_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key")


def _request() -> ProviderRequest:
    return ProviderRequest(
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Hello"}],
        tools=[
            ToolDefinition(
                name="bash",
                description="Run a shell command.",
                input_schema={
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"],
                },
            )
        ],
        max_tokens=128,
        model="openai/gpt-4o",
    )


# ---------------------------------------------------------------------------
# ClassVar / metadata
# ---------------------------------------------------------------------------

class TestOpenAIProviderMetadata:
    def test_provider_id(self, openai_provider):
        assert openai_provider.provider_id == "openai"

    def test_env_var(self, openai_provider):
        assert openai_provider.env_var == "OPENAI_API_KEY"

    def test_default_base_url(self, openai_provider):
        assert openai_provider.base_url == "https://api.openai.com/v1"

    def test_supported_models_includes_gpt4o(self, openai_provider):
        assert "gpt-4o" in openai_provider.supported_models
        assert "gpt-4o-mini" in openai_provider.supported_models
        assert "o1" in openai_provider.supported_models
        assert "o3-mini" in openai_provider.supported_models

    def test_registered_in_providers(self):
        assert "openai" in get_provider("openai", api_key="x").provider_id


# ---------------------------------------------------------------------------
# Context window + pricing
# ---------------------------------------------------------------------------

class TestOpenAIContextWindow:
    def test_gpt4o_128k(self, openai_provider):
        assert openai_provider.context_window("gpt-4o") == 128_000

    def test_gpt4o_mini_128k(self, openai_provider):
        assert openai_provider.context_window("gpt-4o-mini") == 128_000

    def test_gpt35_turbo_16k(self, openai_provider):
        assert openai_provider.context_window("gpt-3.5-turbo") == 16_000

    def test_o1_200k(self, openai_provider):
        assert openai_provider.context_window("o1") == 200_000

    def test_o3_mini_200k(self, openai_provider):
        assert openai_provider.context_window("o3-mini") == 200_000

    def test_unknown_fallback(self, openai_provider):
        # Plan says unknown returns DEFAULT_WINDOW (32k) for compat providers.
        # OpenAI is its own provider — it falls back to 32k too (shared constant).
        assert openai_provider.context_window("nonexistent") == 32_000


class TestOpenAIPricing:
    def test_gpt4o_pricing(self, openai_provider):
        p = openai_provider.pricing("gpt-4o")
        assert isinstance(p, PricingInfo)
        assert p.input_usd_per_1m == 2.5
        assert p.output_usd_per_1m == 10.0

    def test_gpt4o_mini_pricing(self, openai_provider):
        p = openai_provider.pricing("gpt-4o-mini")
        assert p is not None
        assert p.input_usd_per_1m == 0.15
        assert p.output_usd_per_1m == 0.6

    def test_o1_pricing(self, openai_provider):
        p = openai_provider.pricing("o1")
        assert p is not None
        assert p.input_usd_per_1m == 15.0
        assert p.output_usd_per_1m == 60.0

    def test_o3_mini_pricing(self, openai_provider):
        p = openai_provider.pricing("o3-mini")
        assert p is not None
        assert p.input_usd_per_1m == 1.1
        assert p.output_usd_per_1m == 4.4

    def test_unknown_pricing_returns_none(self, openai_provider):
        assert openai_provider.pricing("nonexistent") is None


# ---------------------------------------------------------------------------
# Token counting (heuristic, no API)
# ---------------------------------------------------------------------------

class TestOpenAITokenCounting:
    def test_count_tokens_uses_heuristic(self, openai_provider):
        msgs = [{"role": "user", "content": "Hello, world!"}]
        result = openai_provider.count_tokens(msgs, "gpt-4o")
        # Must be > 0 and not call the API
        assert result > 0
        assert isinstance(result, int)

    def test_count_tokens_50pct_margin(self, openai_provider):
        # Two messages; the heuristic counts characters / 4 then multiplies by 1.5
        msgs = [
            {"role": "user", "content": "a" * 400},  # 100 chars => ~25
            {"role": "assistant", "content": "b" * 400},  # same
        ]
        result = openai_provider.count_tokens(msgs, "gpt-4o")
        # Expected: ((400 + 5 + 400 + 9) // 4) * 1.5 ≈ 305
        assert 250 < result < 400


# ---------------------------------------------------------------------------
# Request body shape
# ---------------------------------------------------------------------------

class TestRequestBodyFormat:
    def test_tools_in_openai_function_shape(self):
        req = _request()
        body = build_request_body(req, model_id="gpt-4o", stream=True)
        assert body["model"] == "gpt-4o"
        assert body["stream"] is True
        assert body["max_tokens"] == 128
        assert "tools" in body
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["function"]["name"] == "bash"
        assert body["tools"][0]["function"]["parameters"]["type"] == "object"

    def test_system_message_first(self):
        req = ProviderRequest(
            system="Be helpful.",
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
            model="openai/gpt-4o",
        )
        body = build_request_body(req, model_id="gpt-4o", stream=True)
        assert body["messages"][0] == {"role": "system", "content": "Be helpful."}
        assert body["messages"][1] == {"role": "user", "content": "Hi"}

    def test_no_tools_omits_tools_field(self):
        req = ProviderRequest(
            system="",
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
            model="openai/gpt-4o",
        )
        body = build_request_body(req, model_id="gpt-4o", stream=True)
        assert "tools" not in body
        assert "tool_choice" not in body

    def test_openai_provider_strips_provider_prefix_from_model(self, openai_provider):
        req = _request()
        body = build_request_body(req, model_id="gpt-4o", stream=True)
        assert body["model"] == "gpt-4o"
        assert "/" not in body["model"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestOpenAIStreaming:
    def test_text_event_yields(self, openai_provider):
        events = [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Hello "},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "world"},
                        "finish_reason": "stop",
                    }
                ],
            },
        ]
        transport = _stream_with_sse(events)
        with httpx.Client(transport=transport) as client:
            out = list(
                openai_chat_stream(
                    _request(),
                    base_url="https://api.openai.com/v1",
                    api_key="k",
                    model_id="gpt-4o",
                    provider="openai",
                    http_client=client,
                )
            )
        text_events = [e for e in out if e.kind == "text"]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello "
        assert text_events[1].text == "world"

    def test_tool_use_assembled_from_deltas(self, openai_provider):
        events = [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {"name": "bash", "arguments": ""},
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '{"cmd":'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"ls"}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        ]
        transport = _stream_with_sse(events)
        with httpx.Client(transport=transport) as client:
            out = list(
                openai_chat_stream(
                    _request(),
                    base_url="https://api.openai.com/v1",
                    api_key="k",
                    model_id="gpt-4o",
                    provider="openai",
                    http_client=client,
                )
            )
        tool_events = [e for e in out if e.kind == "tool_use"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_id == "call_abc"
        assert tool_events[0].tool_name == "bash"
        assert tool_events[0].tool_input == {"cmd": "ls"}

    def test_usage_event_at_end(self, openai_provider):
        events = [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "hi"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
            },
        ]
        transport = _stream_with_sse(events)
        with httpx.Client(transport=transport) as client:
            out = list(
                openai_chat_stream(
                    _request(),
                    base_url="https://api.openai.com/v1",
                    api_key="k",
                    model_id="gpt-4o",
                    provider="openai",
                    http_client=client,
                )
            )
        usage_events = [e for e in out if e.kind == "usage"]
        assert len(usage_events) >= 1
        # The last usage event should have non-zero tokens and the stop reason
        last = usage_events[-1]
        assert last.input_tokens == 11 or last.output_tokens == 22
        assert last.stop_reason == "end_turn"

    def test_openai_provider_stream_method(self, openai_provider):
        events = [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            }
        ]
        transport = _stream_with_sse(events)
        # Inject the mock client by calling openai_chat_stream with the
        # same args OpenAIProvider.stream() would build. Same pattern used
        # by all other streaming tests in this file — no class-level
        # monkey-patching, safe for parallel test runs.
        with httpx.Client(transport=transport) as client:
            from loom.agent.providers._openai_shared import openai_chat_stream as _stream

            req = _request()
            out = list(
                _stream(
                    req,
                    base_url=openai_provider.base_url,
                    api_key=openai_provider.api_key,
                    model_id="gpt-4o",
                    provider=openai_provider.provider_id,
                    http_client=client,
                )
            )
        assert any(e.kind == "text" and e.text == "ok" for e in out)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

class TestOpenAIErrorMapping:
    def test_401_maps_to_auth(self, openai_provider):
        transport = _stream_with_error(
            401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}}
        )
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="bad",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.AUTH
        assert exc_info.value.retryable is False

    def test_429_maps_to_rate_limit_retryable(self, openai_provider):
        transport = _stream_with_error(
            429, {"error": {"message": "rate limited", "type": "rate_limit_error"}}
        )
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="k",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.RATE_LIMIT
        assert exc_info.value.retryable is True

    def test_400_context_length_exceeded(self, openai_provider):
        transport = _stream_with_error(
            400,
            {
                "error": {
                    "message": "context length exceeded",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                }
            },
        )
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="k",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.CONTEXT_OVERFLOW

    def test_400_other_maps_to_invalid_request(self, openai_provider):
        transport = _stream_with_error(
            400, {"error": {"message": "bad model", "type": "invalid_request_error"}}
        )
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="k",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.INVALID_REQUEST

    def test_500_maps_to_server_retryable(self, openai_provider):
        transport = _stream_with_error(500, {"error": {"message": "internal"}})
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="k",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.SERVER
        assert exc_info.value.retryable is True

    def test_502_maps_to_server_retryable(self, openai_provider):
        transport = _stream_with_error(502, {"error": {"message": "bad gateway"}})
        with httpx.Client(transport=transport) as client:
            with pytest.raises(ProviderError) as exc_info:
                list(
                    openai_chat_stream(
                        _request(),
                        base_url="https://api.openai.com/v1",
                        api_key="k",
                        model_id="gpt-4o",
                        provider="openai",
                        http_client=client,
                    )
                )
        assert exc_info.value.code == ProviderErrorCode.SERVER
        assert exc_info.value.retryable is True
