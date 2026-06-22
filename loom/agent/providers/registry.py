"""Provider registry: parse model IDs and instantiate providers.

The model ID format is `provider_id/model_id`, e.g.
`anthropic/claude-sonnet-4-5` or `openrouter/anthropic/claude-3.5-sonnet`.
The model_id segment is allowed to contain `/` (only the first `/`
separates provider from model).
"""

from __future__ import annotations

from typing import TypeVar

from loom.agent.providers.base import LLMProvider
from loom.agent.providers.types import ProviderError, ProviderErrorCode

T = TypeVar("T", bound=type[LLMProvider])

PROVIDERS: dict[str, type[LLMProvider]] = {}


def register[T: type[LLMProvider]](cls: T) -> T:
    """Class decorator: register a provider class under its `provider_id`."""
    if not cls.provider_id:
        raise ValueError(
            f"{cls.__name__}.provider_id must be a non-empty string"
        )
    PROVIDERS[cls.provider_id] = cls
    return cls


def parse_model_id(model: str) -> tuple[str, str]:
    """Parse a model string into (provider_id, model_id).

    Splits on the first `/`. If no `/` is present, defaults the provider
    to `anthropic` for backward compatibility with loom's pre-multi-model
    config (`MODEL=claude-sonnet-4-5`).
    """
    if "/" in model:
        provider_id, _, model_id = model.partition("/")
        return provider_id.strip(), model_id.strip()
    return "anthropic", model.strip()


def get_provider(
    provider_id: str,
    api_key: str = "",
    base_url: str | None = None,
) -> LLMProvider:
    """Look up and instantiate a provider by id.

    Raises ProviderError(code="unknown_provider") if not registered.
    """
    cls = PROVIDERS.get(provider_id)
    if cls is None:
        raise ProviderError(
            ProviderErrorCode.UNKNOWN_PROVIDER,
            f"Unknown provider '{provider_id}'. Registered: {sorted(PROVIDERS)}",
            provider=provider_id,
        )
    return cls(api_key=api_key, base_url=base_url)


def resolve_model_id(model: str) -> tuple[str, str, str]:
    """Resolve a model string to (provider_id, model_id, env_var_name).

    The env_var is the conventional env var the user can set for the
    API key. Use this when you need to read from a specific env var.
    """
    provider_id, model_id = parse_model_id(model)
    try:
        provider = get_provider(provider_id)
        env_var = provider.env_var
    except ProviderError:
        env_var = ""
    return provider_id, model_id, env_var
