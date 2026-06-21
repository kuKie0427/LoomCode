"""Harness eval cases for f-microcompact-token-counter-p1."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class MicrocompactCounterDefined(EvalCase):
    name = "microcompact-counter-defined"
    description = "Context.microcompact updates self.last_input_tokens after clearing content"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent import context as ctx_mod
        src = inspect.getsource(ctx_mod.Context.microcompact)
        if "last_input_tokens" not in src:
            return EvalResult(name=self.name, passed=False, detail="microcompact does not touch last_input_tokens")
        if "bytes_cleared" not in src and "tokens_saved" not in src:
            return EvalResult(name=self.name, passed=False, detail="no bytes/tokens tracking")
        return EvalResult(name=self.name, passed=True, detail="microcompact tracks cleared bytes and updates counter")


class MicrocompactCounterInvalidatesCache(EvalCase):
    name = "microcompact-counter-invalidates-cache"
    description = "microcompact invalidates _token_cache so next count is fresh"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent import context as ctx_mod
        src = inspect.getsource(ctx_mod.Context.microcompact)
        if "_token_cache" not in src and "count_tokens_cache" not in src:
            return EvalResult(name=self.name, passed=False, detail="no cache invalidation in microcompact")
        return EvalResult(name=self.name, passed=True, detail="_token_cache invalidated after clearing")
