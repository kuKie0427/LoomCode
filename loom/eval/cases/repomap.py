"""Harness eval cases for f-repomap-p4."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class RepomapModuleDefined(EvalCase):
    name = "repomap-module-defined"
    description = "loom.agent.repomap module exists with public API"

    def run(self) -> EvalResult:
        try:
            from loom.agent import repomap
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("build_repomap",):
            if not hasattr(repomap, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="build_repomap present")


class RepomapProducesNonEmptyForPopulatedWorkspace(EvalCase):
    name = "repomap-produces-non-empty-for-populated-workspace"
    description = "build_repomap on a workspace with .py files produces a non-empty map"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent.repomap import build_repomap
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.py").write_text("def alpha():\n    pass\n")
            (wd / "b.py").write_text("class Beta:\n    def method(self):\n        pass\n")
            out = build_repomap(wd)
            if "alpha" not in out or "Beta" not in out or "Beta.method" not in out:
                return EvalResult(name=self.name, passed=False, detail=f"missing symbols: {out[:200]}")
        return EvalResult(name=self.name, passed=True, detail="repomap includes top-level symbols and methods")


class RepomapLoopWired(EvalCase):
    name = "repomap-loop-wired"
    description = "system_prompt.py imports + uses build_repomap to inject codebase map"

    def run(self) -> EvalResult:
        from pathlib import Path
        src = Path("loom/agent/system_prompt.py").read_text()
        if "build_repomap" not in src:
            return EvalResult(name=self.name, passed=False, detail="build_repomap not used in system prompt")
        if "sp.add_memory(repomap)" not in src:
            return EvalResult(name=self.name, passed=False, detail="repomap not added to memory tier")
        return EvalResult(name=self.name, passed=True, detail="repomap wired into system prompt")
