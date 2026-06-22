"""LLMProvider abstract base class + PricingInfo.

Subclasses implement concrete provider behavior (Anthropic, OpenAI,
OpenAI-compatible, ...). The ABC defines the minimum interface a provider
must implement to be wired into the agent loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

from loom.agent.providers.types import (
    ProviderRequest,
    StreamEvent,
)


@dataclass(frozen=True)
class PricingInfo:
    """Per-million-token USD pricing. All fields optional — None means
    pricing is unknown (cost display will show "$?").

    cache_read/cache_write are per-Anthropic semantics (cache reads ~10%
    of input, cache writes ~125% of input for 5-min TTL). For OpenAI-
    compatible providers without prompt caching, set both to None.
    """
    input_usd_per_1m: float | None = None
    output_usd_per_1m: float | None = None
    cache_read_usd_per_1m: float | None = None
    cache_write_usd_per_1m: float | None = None


class LLMProvider(ABC):
    """Abstract base for LLM providers.

    Subclasses must set the four `ClassVar` identifiers and implement
    `stream()`. Default implementations exist for `count_tokens` (heuristic)
    and `pricing` (returns None).
    """

    provider_id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    env_var: ClassVar[str] = ""
    default_base_url: ClassVar[str | None] = None
    supported_models: ClassVar[list[str]] = []

    def __init__(self, api_key: str = "", base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url or self.default_base_url

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[StreamEvent]:
        """Stream LLM response events. Must yield at least one event and
        must end with kind="usage" (or kind="error" on failure)."""

    @abstractmethod
    def context_window(self, model: str) -> int:
        """Return the context window size in tokens for the given model."""

    def pricing(self, model: str) -> PricingInfo | None:
        """Return pricing info for the model, or None if unknown."""
        return None

    def count_tokens(self, messages: list[dict], model: str) -> int:
        """Estimate token count. Default heuristic: chars / 4. Subclasses
        with a real count_tokens API should override."""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content)
            else:
                total += len(str(content))
            total += len(m.get("role", ""))
        return total // 4
