"""Token cost calculator.

Provides `compute_cost(model, usage) -> CostBreakdown` with
per-model pricing tables and Anthropic's cache-aware pricing
(cache reads are 90% cheaper than base input; cache writes
incur a 25% premium over base input for 5-min TTL).

Pricing data as of 2026-06-22 (per million tokens, USD).
Override via harness.toml [pricing.<model>] per_model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-1": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-sonnet-4-5": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-haiku-3-5": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
        "cache_write": 1.0,
    },
    "deepseek-v4-flash": {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    },
    "deepseek-v4-pro": {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
    },
}


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


def _pricing_for(model: str) -> dict[str, float]:
    return DEFAULT_PRICING.get(model, DEFAULT_PRICING.get("claude-sonnet-4-5", {}))


def compute_cost(model: str, usage: TokenUsage) -> CostBreakdown:
    p = _pricing_for(model)
    breakdown = CostBreakdown(
        input_cost=usage.input_tokens / 1_000_000 * p.get("input", 0.0),
        output_cost=usage.output_tokens / 1_000_000 * p.get("output", 0.0),
        cache_read_cost=usage.cache_read_tokens / 1_000_000 * p.get("cache_read", 0.0),
        cache_write_cost=usage.cache_creation_tokens / 1_000_000 * p.get("cache_write", 0.0),
    )
    breakdown.total_usd = (
        breakdown.input_cost
        + breakdown.output_cost
        + breakdown.cache_read_cost
        + breakdown.cache_write_cost
    )
    return breakdown


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
