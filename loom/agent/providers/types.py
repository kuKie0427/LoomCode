"""Provider-agnostic types for the multi-model LLM abstraction.

This module defines the data types shared between all provider implementations
(Anthropic, OpenAI, OpenAI-compatible, etc.) and the agent loop. NO provider
SDK imports here — this is the lingua franca.

Mirrors the opencode `packages/llm/src/schema/` shape but simpler (no 4-axis
Protocol/Endpoint/Auth/Framing decomposition — Python single-process ABC
dispatch is sufficient for loom's needs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response.

    `kind` discriminates which fields are meaningful:
      - "text": `text` set
      - "thinking": `text` set
      - "tool_use": `tool_name`, `tool_id`, `tool_input` set
      - "usage": `input_tokens`, `output_tokens`, `cache_read_tokens`,
                  `cache_creation_tokens`, `reasoning_tokens`, `stop_reason` set
      - "error": `error_code`, `error_message` set
    """
    kind: Literal["text", "thinking", "tool_use", "usage", "error"]
    text: str = ""
    tool_name: str = ""
    tool_input: dict | None = None
    tool_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    stop_reason: str = "end_turn"
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class Usage:
    """Token usage breakdown. All fields are inclusive.

    Provider mapping notes:
      - Anthropic: input_tokens = non-cached; cache_read + cache_creation
        are additional.
      - OpenAI: input_tokens is inclusive; cache_read comes from
        prompt_tokens_details.cached_tokens.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ToolDefinition:
    """A tool the model can call. input_schema is a JSON Schema dict
    following the OpenAI/Anthropic convention."""
    name: str
    description: str
    input_schema: dict


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ThinkingBlock:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""


@dataclass
class ToolUseBlock:
    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str | list = ""
    is_error: bool = False


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


class StopReason(str, Enum):  # noqa: UP042  (StrEnum not in all supported Pythons)
    """Why the model stopped generating."""
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    CONTENT_FILTERED = "content_filtered"
    ERROR = "error"


@dataclass(frozen=True)
class ProviderRequest:
    """A request to a provider. Immutable so it can be safely passed around."""
    system: str | list
    messages: list[dict]
    tools: list[ToolDefinition]
    max_tokens: int | None = None
    model: str = ""
    provider_options: dict | None = None


@dataclass(frozen=True)
class ProviderResponse:
    """A complete (non-streaming) response. The agent loop primarily consumes
    StreamEvents; this type is for callers that want the full result (e.g.
    context.autocompact, tools.spawn_subagent)."""
    model: str
    content: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage


class ProviderErrorCode:
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    INVALID_REQUEST = "invalid_request"
    SERVER = "server"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UNKNOWN_PROVIDER = "unknown_provider"
    MISSING_CREDENTIAL = "missing_credential"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """Raised by providers for any failure. `code` is a string from
    ProviderErrorCode constants; `retryable` indicates whether the agent
    loop should attempt a retry."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider = provider
        self.status_code = status_code
