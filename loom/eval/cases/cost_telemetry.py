"""Harness eval cases for f-cost-telemetry-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class CostModuleDefined(EvalCase):
    name = "cost-telemetry-module-defined"
    description = "loom.agent.cost module exists with all public API surface"

    def run(self) -> EvalResult:
        try:
            import loom.agent.cost as c
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("compute_cost", "record_turn", "reset_session_cost", "get_session_cost",
                     "TokenUsage", "CostBreakdown", "SessionCostAccumulator",
                     "usage_from_response", "DEFAULT_PRICING"):
            if not hasattr(c, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="all public API present")


class CostTelemetryLoopWired(EvalCase):
    name = "cost-telemetry-loop-wired"
    description = "loom/agent/loop.py records cost_usd in llm_response trace event"

    def run(self) -> EvalResult:
        from pathlib import Path
        src = Path("loom/agent/loop.py").read_text()
        if "record_turn" not in src:
            return EvalResult(name=self.name, passed=False, detail="record_turn not called from loop.py")
        if "cost_usd" not in src:
            return EvalResult(name=self.name, passed=False, detail="cost_usd not in any trace event")
        if "reset_session_cost" not in src:
            return EvalResult(name=self.name, passed=False, detail="reset_session_cost not called at session start")
        return EvalResult(name=self.name, passed=True, detail="cost telemetry fully wired into loop.py")


class CostTelemetrySonnetMatchesKnownPricing(EvalCase):
    name = "cost-telemetry-sonnet-matches-known-pricing"
    description = "compute_cost('claude-sonnet-4-5', 1M input + 1M output) == $18.00 (verified against public pricing)"

    def run(self) -> EvalResult:
        from loom.agent.cost import TokenUsage, compute_cost
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = compute_cost("claude-sonnet-4-5", usage)
        if abs(cost.total_usd - 18.0) > 0.001:
            return EvalResult(name=self.name, passed=False, detail=f"got {cost.total_usd}, expected 18.0")
        return EvalResult(name=self.name, passed=True, detail="pricing table matches known rates")
