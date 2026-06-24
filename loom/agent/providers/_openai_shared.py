"""Shared logic for OpenAI Chat Completions API providers.

This module contains the streaming + parsing code reused by
``OpenAIProvider`` (api.openai.com) and ``OpenAICompatibleProvider``
(DeepSeek, Ollama, OpenRouter, etc.). All concrete OpenAI-compatible
APIs share the same wire format (`POST /chat/completions` with
`stream: true` returning SSE `data: {json}\n\n` chunks), so the
parsing + tool-call delta accumulation lives here.

No imports from provider SDKs. Uses ``httpx`` (already a transitive
dependency through ``loom.agent.tools``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from loguru import logger

from loom.agent.providers.base import PricingInfo
from loom.agent.providers.types import (
    ProviderError,
    ProviderErrorCode,
    ProviderRequest,
    StopReason,
    StreamEvent,
)

if TYPE_CHECKING:
    from loom.agent.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_WINDOW = 32_000
"""Fallback context window for OpenAI-compatible providers whose
per-model context size is unknown (e.g. OpenRouter, Ollama)."""


def _strip_provider_prefix(model: str) -> str:
    """Strip the ``provider/`` prefix from a model string, returning just
    the model_id. e.g. ``openai/gpt-4o`` → ``gpt-4o``. The inner model_id
    may itself contain ``/`` (e.g. ``openrouter/anthropic/claude-3.5-sonnet``)
    so we only split on the first ``/``.
    """
    return model.split("/", 1)[1] if "/" in model else model


# ---------------------------------------------------------------------------
# MODEL_PROFILES
# ---------------------------------------------------------------------------

# Each profile fully specifies a single "provider" in the registry.
# ``register_compatible_profiles()`` walks this dict and creates a
# dynamically-bound ``OpenAICompatibleProvider`` subclass for each entry.
# Pricing inputs/outputs/cache_read/cache_write are USD per 1M tokens.
#
# Notes on values:
#  * Ollama: pricing is empty (local inference is free).
#  * OpenRouter: pricing is empty (resale model — user should check
#    openrouter.ai/models for current prices).
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "deepseek": {
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "env_var": "DEEPSEEK_API_KEY",
        "models": [
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",      # deprecated — maps to v4-flash non-thinking
            "deepseek-reasoner",  # deprecated — maps to v4-flash thinking
        ],
        "context_windows": {
            "deepseek-v4-flash": 1_000_000,
            "deepseek-v4-pro": 1_000_000,
            "deepseek-chat": 64_000,
            "deepseek-reasoner": 64_000,
        },
        "pricing": {
            "deepseek-v4-flash": PricingInfo(0.14, 0.28, 0.0028, 0.14),
            "deepseek-v4-pro": PricingInfo(0.435, 0.87, 0.003625, 0.435),
            "deepseek-chat": PricingInfo(0.27, 1.10, 0.07, 0.27),
            "deepseek-reasoner": PricingInfo(0.55, 2.19, 0.14, 0.55),
        },
        # DeepSeek V4 supports thinking mode (enabled by default).
        # ``reasoning_effort`` controls thinking intensity; ``thinking``
        # toggles the mode.  Both are top-level keys in the request body.
        # See https://api-docs.deepseek.com/ for current docs.
        "default_body": {
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
    },
    "ollama": {
        "display_name": "Ollama (local)",
        "base_url": "http://localhost:11434/v1",
        "env_var": "OLLAMA_API_KEY",  # optional
        "models": ["llama3", "llama3.1", "qwen2.5", "mistral", "codellama"],
        "context_windows": {
            "llama3": 8_192,
            "llama3.1": 128_000,
            "qwen2.5": 32_000,
            "mistral": 32_000,
            "codellama": 16_000,
        },
        "pricing": {},
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_var": "OPENROUTER_API_KEY",
        "models": [
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o",
            "google/gemini-pro",
            "meta-llama/llama-3.1-405b-instruct",
        ],
        "context_windows": {},
        "pricing": {},
    },
}


def get_profile(provider_id: str) -> dict[str, Any] | None:
    """Look up a model profile by provider_id. Returns None if unknown."""
    return MODEL_PROFILES.get(provider_id)


def make_compatible_provider_class(
    provider_id: str,
    profile: Mapping[str, Any],
) -> type["OpenAICompatibleProvider"]:
    """Create a dynamically-bound ``OpenAICompatibleProvider`` subclass
    for a single profile. The class's ClassVar identifiers are pinned
    from the profile dict so the registry lookup works as if it were
    hand-written.

    Defined as a factory (not direct class definition) to keep MODEL_PROFILES
    as the single source of truth and to allow future per-profile tweaks
    without duplicating the class body.
    """
    from loom.agent.providers.openai_compatible import OpenAICompatibleProvider

    # Bind the outer-scope values to locals with distinct names so they
    # don't get shadowed by the class attribute names below. In a class
    # body, each annotated assignment creates a binding in the class
    # scope, and `from __future__ import annotations` defers the
    # annotation order — so a name like `provider_id` on the LHS can
    # shadow the outer-scope parameter when used on the RHS. Explicit
    # locals with distinct names keep the binding unambiguous.
    _pid = provider_id
    _display = profile.get("display_name", provider_id.title())
    _env_var = profile.get("env_var", "")
    _base_url = profile.get("base_url")
    _models = list(profile.get("models", []))
    _ctx = dict(profile.get("context_windows", {}))
    _pricing = dict(profile.get("pricing", {}))
    _default_body = dict(profile.get("default_body", {}))

    class _BoundProvider(OpenAICompatibleProvider):
        provider_id: ClassVar[str] = _pid
        display_name: ClassVar[str] = _display
        env_var: ClassVar[str] = _env_var
        default_base_url: ClassVar[str | None] = _base_url
        supported_models: ClassVar[list[str]] = _models
        _CONTEXT_WINDOWS: ClassVar[dict[str, int]] = _ctx
        _PRICING: ClassVar[dict[str, PricingInfo]] = _pricing
        _DEFAULT_BODY: ClassVar[dict[str, Any]] = _default_body

        def __init__(self, api_key: str = "", base_url: str | None = None) -> None:
            super().__init__(
                api_key=api_key,
                base_url=base_url,
                _provider_id=_pid,
                _env_var=_env_var,
                _base_url=_base_url,
            )

    _BoundProvider.__name__ = f"OpenAICompatible_{provider_id}"
    _BoundProvider.__qualname__ = _BoundProvider.__name__
    return _BoundProvider


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def _map_status_error(
    *,
    status_code: int,
    body: str,
    provider: str,
) -> ProviderError:
    """Translate an HTTP error from an OpenAI-compatible endpoint into a
    ``ProviderError``. The error mapping table is:

      * 401 → ``auth`` (not retryable)
      * 429 → ``rate_limit`` (retryable)
      * 400 with ``error.code == "context_length_exceeded"`` → ``context_overflow``
      * other 4xx → ``invalid_request`` (not retryable)
      * 5xx → ``server`` (retryable)

    The exception message uses the parsed ``error.message`` field (capped at
    200 chars) rather than the raw response body, to avoid leaking prompt
    content or credentials echoed by misconfigured servers.
    """
    detail_code = ""
    detail_message = ""
    try:
        payload = json.loads(body) if body else {}
        if isinstance(payload, dict):
            err = payload.get("error", {})
            if isinstance(err, dict):
                detail_code = str(err.get("code", ""))
                msg = err.get("message", "")
                if msg:
                    detail_message = str(msg)[:200]
            elif err:
                detail_message = str(err)[:200]
    except (json.JSONDecodeError, ValueError):
        detail_code = ""

    def _msg(prefix: str) -> str:
        return f"{provider}: {prefix} — {detail_message}" if detail_message else f"{provider}: {prefix}"

    if status_code == 401:
        return ProviderError(
            ProviderErrorCode.AUTH,
            _msg("401 unauthorized"),
            provider=provider,
            status_code=status_code,
        )
    if status_code == 429:
        return ProviderError(
            ProviderErrorCode.RATE_LIMIT,
            _msg("429 rate limited"),
            retryable=True,
            provider=provider,
            status_code=status_code,
        )
    if status_code == 400 and detail_code == "context_length_exceeded":
        return ProviderError(
            ProviderErrorCode.CONTEXT_OVERFLOW,
            _msg("context length exceeded"),
            provider=provider,
            status_code=status_code,
        )
    if 400 <= status_code < 500:
        return ProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            _msg(f"{status_code} bad request"),
            provider=provider,
            status_code=status_code,
        )
    return ProviderError(
        ProviderErrorCode.SERVER,
        _msg(f"{status_code} server error"),
        retryable=True,
        provider=provider,
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Tool + request body conversion
# ---------------------------------------------------------------------------

def _serialize_messages(
    messages: list[dict],
) -> list[dict]:
    """Convert loom's internal message format to OpenAI Chat Completions format.

    The agent loop stores content as ``list[TextBlock/ToolUseBlock]`` and
    tool results as ``list[dict]`` inside user messages (Anthropic format).
    The OpenAI-compatible API expects:
    - ``tool_result`` blocks extracted to separate ``role: "tool"`` messages
    - ``TextBlock`` objects serialized as ``{"type": "text", "text": "..."}``
    - All other content passed through as plain dicts or strings.
    """
    from loom.agent.providers.types import TextBlock

    result: list[dict] = []
    for msg in messages:
        msg = dict(msg)  # shallow copy
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        serialized: list[dict | str] = []
        for block in content:
            if isinstance(block, TextBlock):
                serialized.append({"type": "text", "text": block.text})
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                # OpenAI format requires tool_result as a separate
                # ``role: "tool"`` message, not embedded in user content.
                tool_msg: dict[str, Any] = {"role": "tool"}
                tool_msg["tool_call_id"] = block.get("tool_use_id", "")
                # ''.join all content parts if it's a list.
                raw_content = block.get("content", "")
                if isinstance(raw_content, list):
                    tool_msg["content"] = "\n".join(
                        b.get("text", str(b)) if isinstance(b, dict) else str(b)
                        for b in raw_content
                    )
                else:
                    tool_msg["content"] = str(raw_content)
                result.append(tool_msg)
            elif isinstance(block, dict):
                serialized.append(block)
            else:
                serialized.append(str(block))
        msg["content"] = serialized
        result.append(msg)
    return result


def build_request_body(
    request: ProviderRequest,
    *,
    model_id: str,
    stream: bool = True,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a ``ProviderRequest`` into the OpenAI Chat Completions
    body. System messages are prepended; the messages list is passed
    through (assumed already in OpenAI-shape from the agent loop).

    The body uses ``stream: true`` by default — call sites that want a
    non-streaming response can set ``stream=False``.

    ``extra_body`` is merged into the top-level request body (used for
    provider-specific parameters like ``reasoning_effort`` and ``thinking``
    config for DeepSeek thinking models).
    """
    messages: list[dict[str, Any]] = []

    system = request.system
    if isinstance(system, str):
        if system:
            messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        # Each block is either a plain string (legacy) or a dict
        # {"type": "text", "text": "..."}.
        joined = "\n\n".join(
            b["text"] if isinstance(b, dict) and "text" in b else str(b)
            for b in system
        )
        if joined:
            messages.append({"role": "system", "content": joined})
    messages.extend(_serialize_messages(request.messages))

    tools: list[dict[str, Any]] = []
    for t in request.tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
        )

    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    if extra_body:
        body.update(extra_body)
    return body


# ---------------------------------------------------------------------------
# SSE streaming + tool-call delta accumulation
# ---------------------------------------------------------------------------

class _ToolCallAccumulator:
    """Accumulate a tool call's name/arguments across multiple SSE chunks.

    OpenAI Chat Completions streams a single tool call across N chunks:

      * chunk 1: ``{"index": 0, "id": "call_xxx", "type": "function",
                    "function": {"name": "bash", "arguments": ""}}``
      * chunk 2: ``{"index": 0, "function": {"arguments": "{\\"cmd\\":"}}``
      * chunk 3: ``{"index": 0, "function": {"arguments": "\\"ls\\"}"}}``

    Some providers emit the full tool call in a single chunk. The
    accumulator merges partial deltas by ``index`` and exposes the
    full payload on demand.
    """

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, str]] = {}

    def absorb(self, raw_deltas: list[dict[str, Any]]) -> None:
        for tc in raw_deltas:
            idx = int(tc.get("index", 0))
            entry = self._by_index.setdefault(
                idx,
                {"id": "", "name": "", "arguments": ""},
            )
            if tc.get("id"):
                entry["id"] = str(tc["id"])
            fn = tc.get("function") or {}
            if isinstance(fn, dict):
                if fn.get("name"):
                    entry["name"] = str(fn["name"])
                if "arguments" in fn and fn["arguments"] is not None:
                    entry["arguments"] += str(fn["arguments"])

    def all_complete(self) -> list[dict[str, str]]:
        return [dict(self._by_index[k]) for k in sorted(self._by_index)]

    def reset(self) -> None:
        self._by_index.clear()


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse a single SSE ``data: ...`` line. Returns the JSON payload
    or None for ``[DONE]``/empty/heartbeat lines.
    """
    if not line:
        return None
    if line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("OpenAI SSE: malformed JSON line, skipping: {}", payload[:120])
        return None


def _finish_reason_to_stop_reason(reason: str | None) -> str:
    """Map OpenAI's ``finish_reason`` to a ``StopReason`` value."""
    if reason == "tool_calls":
        return StopReason.TOOL_USE.value
    if reason == "length":
        return StopReason.MAX_TOKENS.value
    if reason == "content_filter":
        return StopReason.CONTENT_FILTERED.value
    if reason == "stop":
        return StopReason.END_TURN.value
    return StopReason.END_TURN.value


def openai_chat_stream(
    request: ProviderRequest,
    *,
    base_url: str,
    api_key: str,
    model_id: str,
    provider: str,
    http_client: httpx.Client | None = None,
    timeout: float = 60.0,
    extra_body: dict[str, Any] | None = None,
) -> Iterator[StreamEvent]:
    """Core SSE streaming generator. Yields ``StreamEvent``s and raises
    ``ProviderError`` on transport / HTTP failures.

    ``http_client`` is the dependency-injection seam for tests. The
    production code path creates a fresh ``httpx.Client`` per call to
    avoid sharing connections across requests.

    ``extra_body`` is provider-specific top-level keys merged into the
    request body (e.g. ``reasoning_effort``, ``thinking`` for DeepSeek
    thinking models).
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = build_request_body(request, model_id=model_id, stream=True, extra_body=extra_body)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        try:
            cm = client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                ProviderErrorCode.NETWORK,
                f"{provider}: network error opening stream — {exc}",
                retryable=True,
                provider=provider,
            ) from exc

        try:
            with cm as response:
                if response.status_code >= 400:
                    err_body = response.read().decode("utf-8", errors="replace")
                    raise _map_status_error(
                        status_code=response.status_code,
                        body=err_body,
                        provider=provider,
                    )

                acc = _ToolCallAccumulator()
                pending_tool_calls: list[dict[str, Any]] = []
                last_input_tokens = 0
                last_output_tokens = 0
                last_stop_reason: str = StopReason.END_TURN.value
                emitted_usage = False

                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
                    evt = _parse_sse_line(line)
                    if evt is None:
                        continue

                    # Top-level usage (some OpenAI-compatible APIs only
                    # include it on the last chunk with
                    # `stream_options.include_usage`).
                    if "usage" in evt and isinstance(evt["usage"], dict):
                        u = evt["usage"]
                        last_input_tokens = int(u.get("prompt_tokens", 0) or 0)
                        last_output_tokens = int(u.get("completion_tokens", 0) or 0)

                    choices = evt.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    finish_reason = choice.get("finish_reason")

                    content = delta.get("content")
                    if content:
                        yield StreamEvent(kind="text", text=str(content))

                    # DeepSeek V4 (and some other OpenAI-compatible models)
                    # send thinking/reasoning content in a separate delta
                    # field.  Surface it as a thinking event so the TUI can
                    # display it inline (opencode-compatible pattern).
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        yield StreamEvent(kind="thinking", text=str(reasoning))

                    tcs = delta.get("tool_calls")
                    if tcs:
                        acc.absorb(tcs)
                        pending_tool_calls = acc.all_complete()

                    if finish_reason == "tool_calls" and pending_tool_calls:
                        for tc in pending_tool_calls:
                            try:
                                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except json.JSONDecodeError:
                                logger.warning(
                                    "OpenAI SSE: malformed tool_call arguments for {}, falling back to empty",
                                    tc.get("id", "?"),
                                )
                                args = {}
                            yield StreamEvent(
                                kind="tool_use",
                                tool_id=tc.get("id", ""),
                                tool_name=tc.get("name", ""),
                                tool_input=args,
                            )
                        pending_tool_calls = []
                        acc.reset()

                    # Some providers send tool calls without a
                    # `finish_reason="tool_calls"` marker. Drain any
                    # pending tool calls on any other finish reason.
                    if finish_reason and finish_reason != "tool_calls" and pending_tool_calls:
                        for tc in pending_tool_calls:
                            try:
                                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield StreamEvent(
                                kind="tool_use",
                                tool_id=tc.get("id", ""),
                                tool_name=tc.get("name", ""),
                                tool_input=args,
                            )
                        pending_tool_calls = []
                        acc.reset()

                    if finish_reason:
                        last_stop_reason = _finish_reason_to_stop_reason(finish_reason)
                        yield StreamEvent(
                            kind="usage",
                            input_tokens=last_input_tokens,
                            output_tokens=last_output_tokens,
                            stop_reason=last_stop_reason,
                        )
                        emitted_usage = True

                if not emitted_usage:
                    yield StreamEvent(
                        kind="usage",
                        input_tokens=last_input_tokens,
                        output_tokens=last_output_tokens,
                        stop_reason=StopReason.END_TURN.value,
                    )
        except httpx.HTTPStatusError as exc:
            raise _map_status_error(
                status_code=exc.response.status_code,
                body=exc.response.text,
                provider=provider,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                ProviderErrorCode.NETWORK,
                f"{provider}: network error during stream — {exc}",
                retryable=True,
                provider=provider,
            ) from exc
    finally:
        if owns_client:
            client.close()
