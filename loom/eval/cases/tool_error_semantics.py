"""Harness eval cases for f-tool-error-semantics-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class ToolErrorsModuleDefined(EvalCase):
    name = "tool-errors-module-defined"
    description = "loom.agent.tool_errors module exists with public API"

    def run(self) -> EvalResult:
        try:
            import loom.agent.tool_errors as t
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("detect_repeated_failures", "build_retry_guidance",
                     "extract_tool_use_blocks", "extract_tool_result_blocks"):
            if not hasattr(t, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="all public API present")


class ToolErrorsDetectionWorks(EvalCase):
    name = "tool-errors-detection-works"
    description = "detect_repeated_failures correctly identifies 3 consecutive same-tool errors"

    def run(self) -> EvalResult:
        from loom.agent.tool_errors import detect_repeated_failures
        from loom.agent.tool_errors import extract_tool_use_blocks
        from loom.agent.tool_errors import extract_tool_result_blocks

        messages = []
        for i in range(3):
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "bash", "input": {"command": "rm x"}}
            ]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True, "content": "fail"}
            ]})
        d = detect_repeated_failures(messages)
        if d is None or d["tool"] != "bash" or d["failure_count"] != 3:
            return EvalResult(name=self.name, passed=False, detail=f"got {d}")
        return EvalResult(name=self.name, passed=True, detail="3 consecutive errors detected")


class ToolErrorsGuidanceContainsToolName(EvalCase):
    name = "tool-errors-guidance-contains-tool-name"
    description = "build_retry_guidance mentions the failing tool and the failure count"

    def run(self) -> EvalResult:
        from loom.agent.tool_errors import build_retry_guidance
        text = build_retry_guidance({"tool": "edit_file", "input_repr": "x", "failure_count": 4})
        if "edit_file" not in text:
            return EvalResult(name=self.name, passed=False, detail="tool name missing")
        if "4 times" not in text:
            return EvalResult(name=self.name, passed=False, detail="failure count missing")
        return EvalResult(name=self.name, passed=True, detail="guidance contains required info")


class ToolErrorsLoopWired(EvalCase):
    name = "tool-errors-loop-wired"
    description = "loom/agent/loop.py calls detect_repeated_failures each iteration"

    def run(self) -> EvalResult:
        from pathlib import Path
        src = Path("loom/agent/loop.py").read_text()
        if "detect_repeated_failures" not in src:
            return EvalResult(name=self.name, passed=False, detail="not called from loop.py")
        if "build_retry_guidance" not in src:
            return EvalResult(name=self.name, passed=False, detail="guidance not injected from loop.py")
        return EvalResult(name=self.name, passed=True, detail="retry-detection wired into loop")
