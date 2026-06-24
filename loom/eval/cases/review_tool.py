"""Harness eval cases for f-review-tool: tool registration, safety, and verdict parsing."""

from __future__ import annotations

from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


class ReviewToolRegisteredInToolRegistry(EvalCase):
    name = "review-tool-registered-in-tool-registry"
    description = "review tool is registered in TOOL_REGISTRY and exported via TOOLS"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY, TOOLS

        if not any(t["name"] == "review" for t in TOOLS):
            return EvalResult(
                name=self.name, passed=False, detail="review not in TOOLS"
            )
        if TOOL_REGISTRY.get("review") is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="review not in TOOL_REGISTRY",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="review registered in TOOLS + TOOL_REGISTRY",
        )


class ReviewToolNotInSubTools(EvalCase):
    name = "review-tool-not-in-sub-tools"
    description = "R1 contract: review tool is NOT in SUB_TOOLS (prevents subagent recursion)"

    def run(self) -> EvalResult:
        from loom.agent.tools import SUB_TOOLS

        if any(t["name"] == "review" for t in SUB_TOOLS):
            return EvalResult(
                name=self.name, passed=False,
                detail="review found in SUB_TOOLS (would allow subagent recursion)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="review excluded from SUB_TOOLS",
        )


class ReviewToolReviewToolsWhitelistExcludesWrite(EvalCase):
    name = "review-tool-review-tools-whitelist-excludes-write"
    description = "REVIEW_TOOLS tuple contains only read-only tools, excludes write tools"

    def run(self) -> EvalResult:
        from loom.agent.tools import REVIEW_TOOLS

        if not isinstance(REVIEW_TOOLS, tuple):
            return EvalResult(
                name=self.name, passed=False,
                detail="REVIEW_TOOLS is not a tuple",
            )

        actual_names = {t["name"] for t in REVIEW_TOOLS}
        read_tools = {"read_file", "grep", "glob", "bash"}
        write_tools = {"write_file", "edit_file", "multi_edit", "edit_lines", "task", "review"}

        missing_read = read_tools - actual_names
        if missing_read:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"REVIEW_TOOLS missing read-only tools: {missing_read}",
            )

        present_write = write_tools & actual_names
        if present_write:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"write tools found in REVIEW_TOOLS: {present_write}",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="REVIEW_TOOLS is a tuple with only read-only tools",
        )


class ReviewToolFailClosedOnLlmFailure(EvalCase):
    name = "review-tool-fail-closed-on-llm-failure"
    description = "R2: run_review returns unknown when LLM call fails (no exception propagates)"

    def run(self) -> EvalResult:
        from loom.agent.review import run_review_legacy_str

        with patch("loom.agent.tools.spawn_subagent", side_effect=Exception("LLM down")):
            result = run_review_legacy_str("f-test", "desc")

        if "unknown" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 'unknown' in result, got: {result!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="fail-closed: returns unknown on LLM failure, no exception",
        )


class ReviewToolParsesVerdictTag(EvalCase):
    name = "review-tool-parses-verdict-tag"
    description = "R3: run_review extracts [review: pass] from <verdict> tag in subagent output"

    def run(self) -> EvalResult:
        from loom.agent.review import run_review_legacy_str

        verdict_xml = (
            '<verdict>{"status":"pass","summary":"ok",'
            '"evidence":[],"recommendations":[]}</verdict>'
        )
        with patch("loom.agent.tools.spawn_subagent", return_value=verdict_xml):
            result = run_review_legacy_str("f-test", "desc")

        if "[review: pass]" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected '[review: pass]' in result, got: {result!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="parsed [review: pass] from <verdict> tag",
        )
