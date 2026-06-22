"""Multi-model provider abstraction for loom.

Public API:
  - LLMProvider (ABC), PricingInfo
  - StreamEvent, Usage, ContentBlock, ToolDefinition, StopReason
  - ProviderRequest, ProviderResponse, ProviderError, ProviderErrorCode
  - parse_model_id, get_provider, resolve_model_id, PROVIDERS

Concrete providers (in this package):
  - AnthropicProvider (anthropic.py) — Anthropic Messages API
  - (more in P1: OpenAIProvider, OpenAICompatibleProvider)
"""

from loom.agent.providers import anthropic  # noqa: F401  (registration side effect)
from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.registry import (
    PROVIDERS,
    get_provider,
    parse_model_id,
    register,
    resolve_model_id,
)
from loom.agent.providers.types import (
    ContentBlock,
    ProviderError,
    ProviderErrorCode,
    ProviderRequest,
    ProviderResponse,
    StopReason,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

__all__ = [
    "LLMProvider",
    "PricingInfo",
    "StreamEvent",
    "Usage",
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "StopReason",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderError",
    "ProviderErrorCode",
    "ToolDefinition",
    "parse_model_id",
    "get_provider",
    "resolve_model_id",
    "register",
    "PROVIDERS",
]
