"""Token cost calculator (provider-agnostic).

Provides `compute_cost(model, usage) -> CostBreakdown` that
delegates pricing lookups to the provider registry via
``get_provider`` / ``parse_model_id`` / ``PricingInfo``.

No hardcoded pricing table — each provider carries its own
pricing data (e.g. ``AnthropicProvider._PRICING``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class CostBreakdown:
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    total_usd: float = 0.0


def compute_cost(model: str, usage: TokenUsage) -> CostBreakdown:
    from loom.agent.providers import get_provider, parse_model_id

    provider_id, model_id = parse_model_id(model)
    try:
        provider = get_provider(provider_id)
        pricing = provider.pricing(model_id)
    except Exception:
        pricing = None

    if pricing is None or pricing.input_usd_per_1m is None:
        return CostBreakdown()

    input_cost = round(
        usage.input_tokens * (pricing.input_usd_per_1m or 0) / 1_000_000, 6
    )
    output_cost = round(
        usage.output_tokens * (pricing.output_usd_per_1m or 0) / 1_000_000, 6
    )
    cache_read_cost = round(
        usage.cache_read_tokens * (pricing.cache_read_usd_per_1m or 0) / 1_000_000, 6
    )
    cache_write_cost = round(
        usage.cache_creation_tokens * (pricing.cache_write_usd_per_1m or 0) / 1_000_000, 6
    )
    total = round(input_cost + output_cost + cache_read_cost + cache_write_cost, 6)

    return CostBreakdown(input_cost, output_cost, cache_read_cost, cache_write_cost, total)


def usage_from_response(usage: Any) -> TokenUsage:
    """Extract TokenUsage from a Usage object (provider-agnostic) or
    Anthropic response.usage (legacy).

    Accepts:
      - loom.agent.providers.types.Usage dataclass (new)
      - anthropic.types.Usage (legacy, has cache_creation_input_tokens /
        cache_read_input_tokens attribute names)
    """
    cache_read = getattr(usage, "cache_read_tokens", None)
    if cache_read is None:
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
    cache_creation = getattr(usage, "cache_creation_tokens", None)
    if cache_creation is None:
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=cache_read or 0,
        cache_creation_tokens=cache_creation or 0,
    )


@dataclass
class SessionCostAccumulator:
    """Per-session running total of cost. Lives in the trace module
    (set by loom.agent.loop, queried by /cost slash command or UI).
    """
    total_usd: float = 0.0
    total_input: int = 0
    total_output: int = 0
    total_cache_read: int = 0
    total_cache_creation: int = 0
    turns: int = 0

    def add(self, usage: TokenUsage, cost: CostBreakdown) -> None:
        self.total_usd = round(self.total_usd + cost.total_usd, 6)
        self.total_input += usage.input_tokens
        self.total_output += usage.output_tokens
        self.total_cache_read += usage.cache_read_tokens
        self.total_cache_creation += usage.cache_creation_tokens
        self.turns += 1

    def as_dict(self) -> dict:
        return {
            "total_usd": round(self.total_usd, 6),
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_cache_read_tokens": self.total_cache_read,
            "total_cache_creation_tokens": self.total_cache_creation,
            "turns": self.turns,
        }


_session_accumulator: SessionCostAccumulator | None = None


def reset_session_cost() -> None:
    global _session_accumulator
    _session_accumulator = None


def get_session_cost() -> SessionCostAccumulator | None:
    return _session_accumulator


def record_turn(usage: TokenUsage, model: str) -> CostBreakdown:
    """Record one LLM turn's usage + cost. Lazily creates the session
    accumulator on first call."""
    global _session_accumulator
    if _session_accumulator is None:
        _session_accumulator = SessionCostAccumulator()
    breakdown = compute_cost(model, usage)
    _session_accumulator.add(usage, breakdown)
    return breakdown
