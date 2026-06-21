"""Harness eval cases for f-autocompact-fallback-p1."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class AutocompactFallbackDefined(EvalCase):
    name = "autocompact-fallback-defined"
    description = "Context._raw_truncate_fallback method exists"

    def run(self) -> EvalResult:
        from loom.agent.context import Context
        if not hasattr(Context, "_raw_truncate_fallback"):
            return EvalResult(name=self.name, passed=False, detail="_raw_truncate_fallback missing")
        if not callable(Context._raw_truncate_fallback):
            return EvalResult(name=self.name, passed=False, detail="_raw_truncate_fallback not callable")
        return EvalResult(name=self.name, passed=True, detail="_raw_truncate_fallback defined")


class AutocompactFallbackUsedOnFailure(EvalCase):
    name = "autocompact-fallback-used-on-failure"
    description = "autocompact calls _raw_truncate_fallback when _generate_summary returns None"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent.context import Context
        src = inspect.getsource(Context.autocompact)
        if "_raw_truncate_fallback" not in src:
            return EvalResult(name=self.name, passed=False, detail="autocompact does not call _raw_truncate_fallback")
        if "if summary:" not in src and "if not summary:" not in src:
            return EvalResult(name=self.name, passed=False, detail="no branching on summary success/failure")
        return EvalResult(name=self.name, passed=True, detail="autocompact calls _raw_truncate_fallback on failure")


class AutocompactFallbackNotInfiniteLoop(EvalCase):
    name = "autocompact-fallback-not-infinite-loop"
    description = "After raw_truncate, last_input_tokens is reset so should_compact does not re-fire"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent.context import Context
        src = inspect.getsource(Context._raw_truncate_fallback)
        if "last_input_tokens = 0" not in src:
            return EvalResult(name=self.name, passed=False, detail="last_input_tokens not reset in fallback")
        if "checked_at_index = 0" not in src:
            return EvalResult(name=self.name, passed=False, detail="checked_at_index not reset in fallback")
        return EvalResult(name=self.name, passed=True, detail="counters reset, infinite loop prevented")
