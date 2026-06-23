"""Tests for f-cost-telemetry-p2.

Verifies per-turn cost computation against known pricing, the
session accumulator lifecycle, and the trace event integration.
"""

from __future__ import annotations

from loom.agent.cost import (
    CostBreakdown,
    SessionCostAccumulator,
    TokenUsage,
    compute_cost,
    get_session_cost,
    record_turn,
    reset_session_cost,
    usage_from_response,
)


def test_compute_cost_sonnet_known_pricing():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = compute_cost("claude-sonnet-4-5", usage)
    assert cost.input_cost == 3.0
    assert cost.output_cost == 15.0
    assert cost.total_usd == 18.0


def test_compute_cost_cache_read_uses_10pct_rate():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0, cache_read_tokens=1_000_000)
    cost = compute_cost("claude-sonnet-4-5", usage)
    assert cost.input_cost == 3.0
    assert cost.cache_read_cost == 0.30
    assert cost.total_usd == 3.30


def test_compute_cost_cache_write_uses_125pct_rate():
    usage = TokenUsage(cache_creation_tokens=1_000_000)
    cost = compute_cost("claude-sonnet-4-5", usage)
    assert cost.cache_write_cost == 3.75


def test_compute_cost_unknown_model_returns_zero():
    usage = TokenUsage(input_tokens=1_000_000)
    cost = compute_cost("totally-unknown-model", usage)
    assert cost.total_usd == 0.0


def test_compute_cost_deepseek_is_zero():
    usage = TokenUsage(input_tokens=10_000_000, output_tokens=10_000_000)
    cost = compute_cost("deepseek-v4-flash", usage)
    assert cost.total_usd == 0.0


def test_usage_from_response_extracts_cache_fields():
    class FakeUsage:
        input_tokens = 100
        output_tokens = 50
        cache_read_input_tokens = 30
        cache_creation_input_tokens = 20
    usage = usage_from_response(FakeUsage())
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cache_read_tokens == 30
    assert usage.cache_creation_tokens == 20


def test_usage_from_response_handles_missing_cache_fields():
    class FakeUsage:
        input_tokens = 100
        output_tokens = 50
    usage = usage_from_response(FakeUsage())
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens == 0


def test_session_accumulator_add():
    sess = SessionCostAccumulator()
    usage = TokenUsage(input_tokens=1_000, output_tokens=500, cache_read_tokens=200)
    cost = compute_cost("claude-sonnet-4-5", usage)
    sess.add(usage, cost)
    assert sess.turns == 1
    assert sess.total_input == 1000
    assert sess.total_output == 500
    assert sess.total_cache_read == 200
    assert sess.total_usd > 0
    sess.add(usage, cost)
    assert sess.turns == 2
    assert sess.total_input == 2000
    assert sess.total_usd == round(cost.total_usd * 2, 6)


def test_session_accumulator_as_dict():
    sess = SessionCostAccumulator()
    usage = TokenUsage(input_tokens=500)
    cost = compute_cost("claude-sonnet-4-5", usage)
    sess.add(usage, cost)
    d = sess.as_dict()
    assert d["turns"] == 1
    assert d["total_input_tokens"] == 500
    assert d["total_usd"] > 0


def test_record_turn_creates_accumulator_lazily():
    reset_session_cost()
    assert get_session_cost() is None
    usage = TokenUsage(input_tokens=1000, output_tokens=500)
    cost = record_turn(usage, "claude-sonnet-4-5")
    assert cost.total_usd > 0
    sess = get_session_cost()
    assert sess is not None
    assert sess.turns == 1


def test_reset_session_cost_clears_state():
    record_turn(TokenUsage(input_tokens=1000), "claude-sonnet-4-5")
    assert get_session_cost() is not None
    reset_session_cost()
    assert get_session_cost() is None


def test_cost_breakdown_defaults_to_zero():
    cb = CostBreakdown()
    assert cb.total_usd == 0
