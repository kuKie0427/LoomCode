import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import dotenv
from anthropic import Anthropic, AsyncAnthropic
from loguru import logger

dotenv.load_dotenv()

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

    def stream_iter(self, system, messages, tools, max_tokens=8000) -> Iterator[StreamEvent]:
        self._cancelled = False
        async def _collect():
            events: list[StreamEvent] = []
            current_block_type: str | None = None
            current_tool: dict[str, str] = {}

            async with self.async_client.messages.stream(
                model=self.model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            ) as stream:
                async for event in stream:
                    if self._cancelled:
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
                            events.append(StreamEvent(kind="text", text=delta.text))
                        elif delta.type == "thinking_delta":
                            events.append(StreamEvent(kind="thinking", text=delta.thinking))
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
                            events.append(
                                StreamEvent(
                                    kind="tool_use",
                                    tool_name=current_tool["name"],
                                    tool_input=parsed,
                                    tool_id=current_tool["id"],
                                )
                            )
                            current_tool = {}
                        current_block_type = None
                    elif event.type == "message_start":
                        events.append(
                            StreamEvent(
                                kind="usage",
                                input_tokens=event.message.usage.input_tokens,
                            )
                        )
                    elif event.type == "message_delta":
                        events.append(
                            StreamEvent(
                                kind="usage",
                                output_tokens=event.usage.output_tokens,
                                stop_reason=event.delta.stop_reason or "end_turn",
                            )
                        )

            return events

        yield from asyncio.run(_collect())

    def get_context_window(self) -> int:
        return _MODEL_WINDOWS.get(self.model, DEFAULT_WINDOW)
