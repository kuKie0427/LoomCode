"""Harness eval cases for f-web-fetch-tool-p1."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class WebFetchToolRegistered(EvalCase):
    name = "web-fetch-tool-registered"
    description = "web_fetch tool is registered with read_only + concurrent_safe flags"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY
        tool = TOOL_REGISTRY.get("web_fetch")
        if tool is None:
            return EvalResult(name=self.name, passed=False, detail="web_fetch not in registry")
        if not tool.is_read_only:
            return EvalResult(name=self.name, passed=False, detail="is_read_only must be True")
        if not tool.is_concurrent_safe:
            return EvalResult(name=self.name, passed=False, detail="is_concurrent_safe must be True")
        return EvalResult(name=self.name, passed=True, detail="web_fetch registered with correct flags")


class WebFetchToolExposedToSubagents(EvalCase):
    name = "web-fetch-tool-exposed-to-subagents"
    description = "web_fetch available in SUB_TOOLS and SUB_HANDLERS so subagents can fetch URLs"

    def run(self) -> EvalResult:
        from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS
        sub_names = {t["name"] for t in SUB_TOOLS}
        if "web_fetch" not in sub_names:
            return EvalResult(name=self.name, passed=False, detail="web_fetch not in SUB_TOOLS")
        if "web_fetch" not in SUB_HANDLERS:
            return EvalResult(name=self.name, passed=False, detail="web_fetch not in SUB_HANDLERS")
        return EvalResult(name=self.name, passed=True, detail="web_fetch available to subagents")


class WebFetchUrlValidation(EvalCase):
    name = "web-fetch-url-validation"
    description = "run_web_fetch validates URL: empty rejected, only http/https accepted"

    def run(self) -> EvalResult:
        import loom.agent.tools as main
        for bad in ("", "file:///x", "ftp://x", "javascript:alert(1)"):
            out = main.run_web_fetch(bad)
            if "Error" not in out:
                return EvalResult(name=self.name, passed=False, detail=f"accepted bad URL {bad!r}: {out[:60]}")
        return EvalResult(name=self.name, passed=True, detail="URL validation rejects bad inputs")
