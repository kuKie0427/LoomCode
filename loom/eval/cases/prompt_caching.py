"""Harness eval cases for f-prompt-caching-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class PromptCachingHelpersDefined(EvalCase):
    name = "prompt-caching-helpers-defined"
    description = "with_cache_control and with_tool_cache_control are exported from loom.agent.llm"

    def run(self) -> EvalResult:
        from loom.agent import llm
        if not hasattr(llm, "with_cache_control"):
            return EvalResult(name=self.name, passed=False, detail="with_cache_control missing")
        if not hasattr(llm, "with_tool_cache_control"):
            return EvalResult(name=self.name, passed=False, detail="with_tool_cache_control missing")
        return EvalResult(name=self.name, passed=True, detail="both helpers present")


class PromptCachingLoopUsesHelpers(EvalCase):
    name = "prompt-caching-loop-uses-helpers"
    description = "loom/agent/loop.py references both cache-control helpers in its SDK call sites"

    def run(self) -> EvalResult:
        from pathlib import Path
        src = Path("loom/agent/loop.py").read_text()
        if "with_cache_control(" not in src:
            return EvalResult(name=self.name, passed=False, detail="with_cache_control not used in loop.py")
        if "with_tool_cache_control(" not in src:
            return EvalResult(name=self.name, passed=False, detail="with_tool_cache_control not used in loop.py")
        return EvalResult(name=self.name, passed=True, detail="both helpers wired into loop.py")


class PromptCachingWrapsSystemCorrectly(EvalCase):
    name = "prompt-caching-wraps-system-correctly"
    description = "with_cache_control wraps a string into a single text block with cache_control: ephemeral"

    def run(self) -> EvalResult:
        from loom.agent.llm import with_cache_control
        out = with_cache_control("test prompt")
        if not isinstance(out, list) or len(out) != 1:
            return EvalResult(name=self.name, passed=False, detail=f"expected single-block list, got {type(out)}")
        block = out[0]
        if block.get("type") != "text" or block.get("text") != "test prompt":
            return EvalResult(name=self.name, passed=False, detail=f"block malformed: {block}")
        if block.get("cache_control") != {"type": "ephemeral"}:
            return EvalResult(name=self.name, passed=False, detail=f"cache_control wrong: {block.get('cache_control')}")
        return EvalResult(name=self.name, passed=True, detail="system prompt correctly wrapped with cache_control")
