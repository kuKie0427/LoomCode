"""Harness eval cases for the native grep tool (f-grep-tool-p0)."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class GrepToolRegistered(EvalCase):
    name = "grep-tool-registered"
    description = "grep tool is registered in TOOL_REGISTRY with read_only + concurrent_safe flags"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY
        tool = TOOL_REGISTRY.get("grep")
        if tool is None:
            return EvalResult(name=self.name, passed=False, detail="grep not in registry")
        if not tool.is_read_only:
            return EvalResult(name=self.name, passed=False, detail="grep.is_read_only must be True")
        if not tool.is_concurrent_safe:
            return EvalResult(name=self.name, passed=False, detail="grep.is_concurrent_safe must be True")
        return EvalResult(name=self.name, passed=True, detail="grep registered with correct flags")


class GrepToolExposedToSubagents(EvalCase):
    name = "grep-tool-exposed-to-subagents"
    description = "grep is available in SUB_TOOLS and SUB_HANDLERS so subagents can search"

    def run(self) -> EvalResult:
        from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS
        sub_names = {t["name"] for t in SUB_TOOLS}
        if "grep" not in sub_names:
            return EvalResult(name=self.name, passed=False, detail="grep not in SUB_TOOLS")
        if "grep" not in SUB_HANDLERS:
            return EvalResult(name=self.name, passed=False, detail="grep not in SUB_HANDLERS")
        return EvalResult(name=self.name, passed=True, detail="grep available to subagents")


class GrepToolStructuredOutput(EvalCase):
    name = "grep-tool-structured-output"
    description = "run_grep returns path:line:content format"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.py").write_text("hello world\nfoo bar\n")
            (wd / "b.py").write_text("baz hello\n")
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_grep("hello")
            finally:
                main.WORKDIR = original
        for line in out.splitlines():
            if line.startswith("["):
                continue
            if ":" not in line:
                return EvalResult(name=self.name, passed=False, detail=f"no colon in: {line!r}")
            parts = line.split(":", 2)
            if len(parts) < 3:
                return EvalResult(name=self.name, passed=False, detail=f"not 3-part: {line!r}")
            try:
                int(parts[1])
            except ValueError:
                return EvalResult(name=self.name, passed=False, detail=f"non-int line_no: {line!r}")
        if "a.py" not in out or "b.py" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"missing expected files: {out!r}")
        return EvalResult(name=self.name, passed=True, detail="output matches path:line:content contract")


class GrepToolWorkspaceBoundary(EvalCase):
    name = "grep-tool-workspace-boundary"
    description = "run_grep rejects paths outside WORKDIR"

    def run(self) -> EvalResult:
        import loom.agent.tools as main
        out = main.run_grep("anything", path="../etc")
        if "Error" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"path escape allowed: {out!r}")
        return EvalResult(name=self.name, passed=True, detail="path escape rejected")
