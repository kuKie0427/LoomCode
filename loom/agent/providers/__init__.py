"""Multi-model provider abstraction for loom.

Public API:
  - LLMProvider (ABC), PricingInfo
  - StreamEvent, Usage, ContentBlock, ToolDefinition, StopReason
  - ProviderRequest, ProviderResponse, ProviderError, ProviderErrorCode
  - parse_model_id, get_provider, resolve_model_id, PROVIDERS

Concrete providers (in this package):
  - AnthropicProvider (anthropic.py) — Anthropic Messages API
  - OpenAIProvider (openai.py) — OpenAI Chat Completions (api.openai.com)
  - OpenAICompatibleProvider (openai_compatible.py) — DeepSeek / Ollama /
    OpenRouter, dynamically bound from MODEL_PROFILES
"""

from loom.agent.providers import (
    anthropic,  # noqa: F401  (registration side effect)
    openai,  # noqa: F401  (registration side effect)
    openai_compatible,  # noqa: F401  (registration side effect)
)
from loom.agent.providers._openai_shared import MODEL_PROFILES
from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    register_compatible_profiles,
)
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

# Register DeepSeek / Ollama / OpenRouter from MODEL_PROFILES.
register_compatible_profiles()

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
    "OpenAICompatibleProvider",
    "MODEL_PROFILES",
]
