"""Anthropic provider. Port of the existing loom/agent/llm.py stream logic,
restructured to satisfy the LLMProvider ABC.

Backward compatibility:
  - claude-opus-4-1, claude-sonnet-4-5, claude-haiku-3-5, claude-haiku-4-5
  - deepseek-v4-flash, deepseek-v4-pro (served via Anthropic-compatible API
    at https://api.deepseek.com/anthropic — preserved as legacy defaults)
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from collections.abc import Iterator
from typing import Any, ClassVar

import anthropic
from loguru import logger

from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.registry import register
from loom.agent.providers.types import (
    ProviderErrorCode,
    ProviderRequest,
    StreamEvent,
)

MIN_CACHEABLE_TOKENS = 1024
DEFAULT_WINDOW = 200_000

# Models that support Anthropic's extended thinking feature. Claude 3.5 and
# earlier do NOT support it. Add new Claude 4+ family members here.
_THINKING_CAPABLE_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-5",
    "claude-opus-4-1",
})

# Default token budget for extended thinking. Anthropic requires
# budget_tokens >= 1024 and budget_tokens < max_tokens.
_DEFAULT_THINKING_BUDGET_TOKENS = 8000
_MIN_THINKING_BUDGET_TOKENS = 1024


def _build_thinking_param(model_id: str) -> dict[str, Any] | None:
    """Build the ``thinking`` kwarg for ``messages.stream``, or ``None``.

    Opt-out: set ``LOOM_THINKING_BUDGET=0`` (or any value < 1024).
    Opt-in with custom budget: set ``LOOM_THINKING_BUDGET=<N>`` where N >= 1024.
    Non-Claude-4 models always return ``None`` regardless of env.

    Invalid (non-numeric) values are treated as "disable" rather than crash,
    so a malformed env var never breaks the agent loop.
    """
    if model_id not in _THINKING_CAPABLE_MODELS:
        return None
    raw = os.getenv("LOOM_THINKING_BUDGET")
    if raw is None:
        budget = _DEFAULT_THINKING_BUDGET_TOKENS
    else:
        try:
            budget = int(raw)
        except ValueError:
            return None
    if budget < _MIN_THINKING_BUDGET_TOKENS:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def with_cache_control(system: str | list) -> str | list:
    """Wrap a string system prompt as a list of text blocks with
    `cache_control: ephemeral` on the LAST block. If the system is
    already a list, pass it through unchanged.
    """
    if isinstance(system, list):
        return system
    if not system:
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def with_tool_cache_control(tools: list) -> list:
    """Mark the LAST tool with `cache_control: ephemeral`."""
    if not tools:
        return tools
    out = list(tools)
    last = dict(out[-1])
    last["cache_control"] = {"type": "ephemeral"}
    out[-1] = last
    return out


@register
class AnthropicProvider(LLMProvider):
    provider_id: ClassVar[str] = "anthropic"
    display_name: ClassVar[str] = "Anthropic"
    env_var: ClassVar[str] = "ANTHROPIC_API_KEY"
    default_base_url: ClassVar[str | None] = None

    supported_models: ClassVar[list[str]] = [
        "claude-sonnet-4-5",
        "claude-opus-4-1",
        "claude-haiku-3-5",
        "claude-haiku-4-5",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]

    _CONTEXT_WINDOWS: ClassVar[dict[str, int]] = {
        "claude-sonnet-4-5": 200_000,
        "claude-opus-4-1": 200_000,
        "claude-haiku-3-5": 200_000,
        "claude-haiku-4-5": 200_000,
        "deepseek-v4-flash": 64_000,
        "deepseek-v4-pro": 64_000,
    }

    _PRICING: ClassVar[dict[str, PricingInfo]] = {
        "claude-opus-4-1": PricingInfo(15.0, 75.0, 1.50, 18.75),
        "claude-sonnet-4-5": PricingInfo(3.0, 15.0, 0.30, 3.75),
        "claude-haiku-3-5": PricingInfo(0.80, 4.0, 0.08, 1.0),
        "claude-haiku-4-5": PricingInfo(0.80, 4.0, 0.08, 1.0),
        "deepseek-v4-flash": PricingInfo(0.0, 0.0, 0.0, 0.0),
        "deepseek-v4-pro": PricingInfo(0.0, 0.0, 0.0, 0.0),
    }

    def __init__(self, api_key: str = "", base_url: str | None = None) -> None:
        super().__init__(api_key=api_key, base_url=base_url)
        effective_key = self.api_key or os.getenv(self.env_var, "")
        effective_url = self.base_url or os.getenv(
            "ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"
        )
        self._client = anthropic.Anthropic(
            api_key=effective_key,
            base_url=effective_url,
            max_retries=3,
            timeout=60.0,
        )
        self._async_client = anthropic.AsyncAnthropic(
            api_key=effective_key,
            base_url=effective_url,
            max_retries=3,
            timeout=60.0,
        )
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def context_window(self, model: str) -> int:
        return self._CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)

    def pricing(self, model: str) -> PricingInfo | None:
        return self._PRICING.get(model)

    def count_tokens(self, messages: list[dict], model: str) -> int:
        try:
            result = self._client.messages.count_tokens(
                model=model, messages=messages  # type: ignore[arg-type]
            )
            return result.input_tokens
        except Exception as exc:
            logger.debug("count_tokens SDK call failed, using heuristic: {}", exc)
            return super().count_tokens(messages, model)

    def stream(self, request: ProviderRequest) -> Iterator[StreamEvent]:
        self._cancel_event = threading.Event()
        if request.max_tokens is None:
            request = ProviderRequest(
                system=request.system,
                messages=request.messages,
                tools=request.tools,
                max_tokens=8192,
                model=request.model,
            )

        model_id = request.model
        if "/" in model_id:
            model_id = model_id.split("/", 1)[1]

        thinking_param = _build_thinking_param(model_id)
        # When thinking is enabled, max_tokens must exceed budget_tokens.
        # Reserve 2048 tokens for the response after the thinking block completes.
        max_tokens = request.max_tokens or 8192
        if thinking_param is not None:
            max_tokens = max(max_tokens, thinking_param["budget_tokens"] + 2048)

        tool_dicts = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in request.tools
        ]

        event_queue: queue.Queue[StreamEvent | None] = queue.Queue()

        async def _consume() -> list[StreamEvent] | None:
            current_block_type: str | None = None
            current_tool: dict[str, str] = {}
            emitted: list[StreamEvent] = []
            try:
                stream_kwargs: dict[str, Any] = {
                    "model": model_id,
                    "system": with_cache_control(request.system),
                    "messages": request.messages,
                    "tools": with_tool_cache_control(tool_dicts) if tool_dicts else tool_dicts,
                    "max_tokens": max_tokens,
                }
                if thinking_param is not None:
                    stream_kwargs["thinking"] = thinking_param
                async with self._async_client.messages.stream(**stream_kwargs) as stream:
                    async for event in stream:
                        if self._cancel_event.is_set():
                            break
                        if event.type == "content_block_start":
                            block = event.content_block
                            current_block_type = block.type
                            if block.type == "tool_use":
                                current_tool = {
                                    "name": block.name,
                                    "id": block.id,
                                    "input_json": "",
                                }
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                ev = StreamEvent(kind="text", text=delta.text)
                                event_queue.put(ev)
                                emitted.append(ev)
                            elif delta.type == "thinking_delta":
                                ev = StreamEvent(kind="thinking", text=delta.thinking)
                                event_queue.put(ev)
                                emitted.append(ev)
                            elif delta.type == "input_json_delta":
                                current_tool["input_json"] += delta.partial_json
                        elif event.type == "content_block_stop":
                            if current_block_type == "tool_use":
                                try:
                                    parsed = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                                except json.JSONDecodeError:
                                    logger.warning(
                                        "stream: malformed tool_use input JSON, falling back to empty input. "
                                        "tool_id={} raw_len={}",
                                        current_tool.get("id", "?"),
                                        len(current_tool.get("input_json", "")),
                                    )
                                    parsed = {}
                                ev = StreamEvent(
                                    kind="tool_use",
                                    tool_name=current_tool["name"],
                                    tool_input=parsed,
                                    tool_id=current_tool["id"],
                                )
                                event_queue.put(ev)
                                emitted.append(ev)
                                current_tool = {}
                            current_block_type = None
                        elif event.type == "message_start":
                            ev = StreamEvent(
                                kind="usage",
                                input_tokens=event.message.usage.input_tokens,
                            )
                            event_queue.put(ev)
                            emitted.append(ev)
                        elif event.type == "message_delta":
                            ev = StreamEvent(
                                kind="usage",
                                output_tokens=event.usage.output_tokens,
                                stop_reason=event.delta.stop_reason or "end_turn",
                            )
                            event_queue.put(ev)
                            emitted.append(ev)
            except anthropic.APIStatusError as exc:
                code = ProviderErrorCode.SERVER
                if exc.status_code == 401:
                    code = ProviderErrorCode.AUTH
                elif exc.status_code == 429:
                    code = ProviderErrorCode.RATE_LIMIT
                elif exc.status_code in (400, 404, 409, 413, 422):
                    code = ProviderErrorCode.INVALID_REQUEST
                err_ev = StreamEvent(
                    kind="error",
                    error_code=code,
                    error_message=str(exc),
                )
                event_queue.put(err_ev)
                emitted.append(err_ev)
            except anthropic.APIConnectionError as exc:
                err_ev = StreamEvent(
                    kind="error",
                    error_code=ProviderErrorCode.NETWORK,
                    error_message=str(exc),
                )
                event_queue.put(err_ev)
                emitted.append(err_ev)
            finally:
                event_queue.put(None)
            return emitted if emitted else None

        def _producer_target() -> None:
            coro = _consume()
            try:
                result = asyncio.run(coro)
                if result is not None:
                    for ev in result:
                        event_queue.put(ev)
            except Exception as exc:
                logger.error("stream: producer thread crashed: {}", exc)
            finally:
                if coro.cr_frame is None:
                    coro.close()
                event_queue.put(None)

        producer = threading.Thread(target=_producer_target, daemon=True)
        producer.start()

        try:
            while True:
                ev = event_queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            self._cancel_event.set()
