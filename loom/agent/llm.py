import asyncio
import json
import os
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import dotenv
from anthropic import Anthropic, AsyncAnthropic
from loguru import logger

from loom.agent.config import LLM_CONFIG

DEFAULT_WINDOW = 128_000
MIN_CACHEABLE_TOKENS = 1024  # Anthropic prompt-cache minimum content length


def with_cache_control(system: str | list) -> str | list:
    """Wrap a string system prompt as a list of text blocks with
    `cache_control: ephemeral` on the LAST block. If the system is
    already a list (e.g. caller pre-built blocks), pass it through
    unchanged — the caller is expected to mark their own cache point.

    Anthropic's prompt-cache requires content >= 1024 tokens for caching
    to be honored. Short system prompts (< ~4KB) won't cache; we still
    send the marker for API consistency.
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
    """Mark the LAST tool with `cache_control: ephemeral` so the tools
    list caches across turns. The tool definitions are stable across
    a session (loom has a fixed TOOLS list), so this is a big win.
    """
    if not tools:
        return tools
    out = list(tools)
    last = dict(out[-1])
    last["cache_control"] = {"type": "ephemeral"}
    out[-1] = last
    return out

_MODEL_WINDOWS = {
    "deepseek-v4-flash": 1000000,
    "deepseek-v4-pro": 1000000,
}
DEFAULT_WINDOW = 128000


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response."""
    kind: Literal["text", "thinking", "tool_use", "usage"]
    text: str = ""
    tool_name: str = ""
    tool_input: dict | None = None
    tool_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"


class LLMClient:
    def __init__(self, model: str):
        self.model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
        self.client = self._llm_client(self.api_key, self.base_url)
        self.async_client = self._async_client(self.api_key, self.base_url)
        self._cancelled = False
        self._cancel_event = threading.Event()

    def _llm_client(self, api_key: str, base_url: str) -> Anthropic:
        try:
            return Anthropic(
                api_key=api_key,
                base_url=base_url,
                max_retries=3,
                timeout=60.0,
            )
        except Exception as e:
            logger.error(f"Error initializing LLM client: {e}")
            raise e

    def _async_client(self, api_key: str, base_url: str) -> AsyncAnthropic:
        try:
            return AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                max_retries=3,
                timeout=60.0,
            )
        except Exception as e:
            logger.error(f"Error initializing async LLM client: {e}")
            raise e

    def change_model(self, new_model: str) -> None:
        self.model = new_model

    def cancel(self) -> None:
        self._cancelled = True
        # Also signal the producer thread so the async stream aborts promptly.
        self._cancel_event.set()

    def stream_iter(self, system, messages, tools, max_tokens: int | None = None) -> Iterator[StreamEvent]:
        # Reset per-stream state so a previously cancelled stream can't poison us.
        self._cancelled = False
        self._cancel_event = threading.Event()
        if max_tokens is None:
            max_tokens = LLM_CONFIG.max_output_tokens

        event_queue: queue.Queue[StreamEvent | None] = queue.Queue()

        async def _consume() -> list[StreamEvent] | None:
            """Run the async stream and push converted StreamEvents onto the queue.

            Returns ``None`` for the real streaming path (events are pushed to the
            queue as they arrive). Returns a list of events when ``asyncio.run``
            has been monkey-patched by tests to bypass the async loop entirely.
            """
            current_block_type: str | None = None
            current_tool: dict[str, str] = {}
            emitted: list[StreamEvent] = []
            try:
                async with self.async_client.messages.stream(
                    model=self.model,
                    system=with_cache_control(system),
                    messages=messages,
                    tools=with_tool_cache_control(list(tools)) if tools else tools,
                    max_tokens=max_tokens,
                ) as stream:
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
                                        "stream_iter: malformed tool_use input JSON, falling back to empty input. "
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
            finally:
                event_queue.put(None)
            # Returning a list lets legacy tests that patch asyncio.run to
            # return events synchronously still drive the consumer. In the real
            # path asyncio.run never exposes this return value (it pushes to the
            # queue during iteration), so returning the list is harmless.
            return emitted if emitted else None

        def _producer_target() -> None:
            coro = _consume()
            try:
                result = asyncio.run(coro)
                if result is not None:
                    for ev in result:
                        event_queue.put(ev)
            except Exception as exc:
                logger.error("stream_iter: producer thread crashed: {}", exc)
            finally:
                # If asyncio.run was monkey-patched by tests, the coroutine was
                # never awaited; close it explicitly so Python's GC doesn't emit
                # a "coroutine was never awaited" warning.
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
            # If the consumer stops iterating early (cancel/early break), make
            # sure the producer aborts instead of hanging on async iteration.
            self._cancel_event.set()

    def get_context_window(self) -> int:
        return _MODEL_WINDOWS.get(self.model, DEFAULT_WINDOW)
