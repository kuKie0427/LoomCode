"""Harness eval cases for f-tdd-agent-mode-p4."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class TDDModuleDefined(EvalCase):
    name = "tdd-module-defined"
    description = "loom.agent.tdd module exposes the TDD primitives"

    def run(self) -> EvalResult:
        try:
            from loom.agent import tdd
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("PytestRun", "is_test_file", "run_pytest", "build_focused_prompt"):
            if not hasattr(tdd, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="TDD public API complete")


class TDDIsTestFileGuard(EvalCase):
    name = "tdd-is-test-file-guard"
    description = "is_test_file identifies tests/*, test_*, *_test.py; rejects source files"

    def run(self) -> EvalResult:
        from loom.agent.tdd import is_test_file
        positives = ["tests/test_foo.py", "test_bar.py", "foo_test.py", "tests/sub/test_x.py"]
        negatives = ["loom/agent/loop.py", "README.md", "tests/data.json"]
        for p in positives:
            if not is_test_file(p):
                return EvalResult(name=self.name, passed=False, detail=f"false negative on {p}")
        for p in negatives:
            if is_test_file(p):
                return EvalResult(name=self.name, passed=False, detail=f"false positive on {p}")
        return EvalResult(name=self.name, passed=True, detail="test-file detection correct")


class TDDBuildFocusedPrompt(EvalCase):
    name = "tdd-build-focused-prompt"
    description = "build_focused_prompt embeds test path + failure + reward-hacking guard"

    def run(self) -> EvalResult:
        from loom.agent.tdd import build_focused_prompt
        prompt = build_focused_prompt("tests/test_x.py", "AssertionError: foo", max_iterations=7)
        if "tests/test_x.py" not in prompt:
            return EvalResult(name=self.name, passed=False, detail="missing test path")
        if "AssertionError" not in prompt:
            return EvalResult(name=self.name, passed=False, detail="missing failure excerpt")
        if "MUST NOT edit" not in prompt:
            return EvalResult(name=self.name, passed=False, detail="missing reward-hacking guard")
        if "7 times" not in prompt:
            return EvalResult(name=self.name, passed=False, detail="missing iteration count")
        return EvalResult(name=self.name, passed=True, detail="focused prompt template complete")


class TDDRunPytestRoundtrip(EvalCase):
    name = "tdd-run-pytest-roundtrip"
    description = "run_pytest captures pass/fail/timeout correctly"

    def run(self) -> EvalResult:
        from loom.agent.tdd import run_pytest
        with tempfile.TemporaryDirectory() as d:
            passing = Path(d) / "test_pass.py"
            passing.write_text("def test_ok(): assert True\n")
            r_pass = run_pytest(passing, cwd=d)
            if not r_pass.passed:
                return EvalResult(name=self.name, passed=False, detail=f"passing test reported fail: {r_pass}")
            failing = Path(d) / "test_fail.py"
            failing.write_text("def test_boom(): assert False\n")
            r_fail = run_pytest(failing, cwd=d)
            if r_fail.passed:
                return EvalResult(name=self.name, passed=False, detail="failing test reported pass")
        return EvalResult(name=self.name, passed=True, detail="run_pytest pass/fail distinction correct")