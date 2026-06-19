"""Eval cases for Task 2: real token counter.

Four cases exercise the real path through ``Context.should_compact`` and the
private ``_count_tokens_accurate`` helper:

- ``tokens-real-counter-used-near-threshold`` — large messages trigger the SDK call
- ``tokens-cheap-estimate-used-far-from-threshold`` — small messages skip the SDK
- ``tokens-counter-failure-falls-back-to-heuristic`` — SDK exception → char/4
- ``tokens-cache-hit-on-same-content`` — same list object → no second SDK call

The Anthropic client is patched via ``unittest.mock`` — we never hit the network.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


def _big_messages(target_chars: int) -> list:
    """Return a one-message list whose text length is ~target_chars."""
    body = "x" * target_chars
    return [{"role": "user", "content": body}]


class TokensRealCounterUsedNearThreshold(EvalCase):
    name = "tokens-real-counter-used-near-threshold"
    description = "should_compact near threshold calls _count_tokens_accurate (real SDK), not just char/4"

    def run(self) -> EvalResult:
        from loom.agent import context as ctx_mod

        ctx_mod._token_cache.clear()

        ctx = ctx_mod.Context()
        # context_window=10000, threshold=8500, gate=7650.
        # last_input=8000 + 200 chars/4=50 → cheap=8050 >= 7650 → triggers accurate path.
        ctx.last_input_tokens = 8000
        ctx.checked_at_index = 0
        messages = _big_messages(200)

        with patch.object(ctx_mod, "Anthropic") as MockAnthropic:
            mock_instance = MockAnthropic.return_value
            mock_instance.messages.count_tokens.return_value = SimpleNamespace(input_tokens=7600)

            ctx.should_compact(messages, context_window=10_000, model="claude-haiku-4-5")

        if mock_instance.messages.count_tokens.call_count != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 1 SDK call near threshold, got {mock_instance.messages.count_tokens.call_count}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="SDK count_tokens invoked exactly once near threshold (gate triggered)",
        )


class TokensCheapEstimateUsedFarFromThreshold(EvalCase):
    name = "tokens-cheap-estimate-used-far-from-threshold"
    description = "should_compact far from threshold uses char/4 heuristic; NO SDK call"

    def run(self) -> EvalResult:
        from loom.agent import context as ctx_mod

        ctx_mod._token_cache.clear()

        ctx = ctx_mod.Context()
        ctx.last_input_tokens = 100
        ctx.checked_at_index = 0
        messages = _big_messages(100)

        with patch.object(ctx_mod, "Anthropic") as MockAnthropic:
            mock_instance = MockAnthropic.return_value

            ctx.should_compact(messages, context_window=128_000, model="claude-haiku-4-5")

        if mock_instance.messages.count_tokens.call_count != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 0 SDK calls far from threshold, got {mock_instance.messages.count_tokens.call_count}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="far-from-threshold → cheap heuristic only, no HTTP roundtrip",
        )


class TokensCounterFailureFallsBackToHeuristic(EvalCase):
    name = "tokens-counter-failure-falls-back-to-heuristic"
    description = "When SDK raises, should_compact falls back to char/4 heuristic (does NOT raise)"

    def run(self) -> EvalResult:
        from loom.agent import context as ctx_mod

        ctx_mod._token_cache.clear()

        ctx = ctx_mod.Context()
        ctx.last_input_tokens = 7500
        ctx.checked_at_index = 0
        messages = _big_messages(200)

        with patch.object(ctx_mod, "Anthropic") as MockAnthropic:
            mock_instance = MockAnthropic.return_value
            mock_instance.messages.count_tokens.side_effect = RuntimeError("network down")

            try:
                result = ctx.should_compact(messages, context_window=10_000, model="claude-haiku-4-5")
            except Exception as e:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"should_compact raised on SDK failure (should fall back): {type(e).__name__}: {e}",
                )

        if result is not False:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected False (7550 < 8500 threshold), got {result}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="SDK exception → char/4 fallback used, no exception propagated",
        )


class TokensCacheHitOnSameContent(EvalCase):
    name = "tokens-cache-hit-on-same-content"
    description = "Same list object passed twice → SDK called once (cached)"

    def run(self) -> EvalResult:
        from loom.agent import context as ctx_mod

        ctx_mod._token_cache.clear()

        ctx = ctx_mod.Context()
        ctx.last_input_tokens = 8000
        ctx.checked_at_index = 0
        messages = _big_messages(200)

        with patch.object(ctx_mod, "Anthropic") as MockAnthropic:
            mock_instance = MockAnthropic.return_value
            mock_instance.messages.count_tokens.return_value = SimpleNamespace(input_tokens=7600)

            ctx.should_compact(messages, context_window=10_000, model="claude-haiku-4-5")
            call_count_after_first = mock_instance.messages.count_tokens.call_count
            ctx.should_compact(messages, context_window=10_000, model="claude-haiku-4-5")
            call_count_after_second = mock_instance.messages.count_tokens.call_count

        if call_count_after_first != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"first call expected 1 SDK call, got {call_count_after_first}",
            )
        if call_count_after_second != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"second call (same list) expected still 1 SDK call, got {call_count_after_second}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="second call hit cache; SDK not re-invoked",
        )