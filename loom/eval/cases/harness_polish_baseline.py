"""Additional harness eval cases for f-harness-as-product-polish-p4
(loom eval --baseline / --diff-baseline / eval init).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class HarnessPolishEvalBaselineRoundtrip(EvalCase):
    name = "harness-polish-eval-baseline-roundtrip"
    description = "save_baseline + load_baseline round-trip preserves pass/fail per case"

    def run(self) -> EvalResult:
        from loom.eval.baseline import load_baseline, save_baseline
        from loom.eval.runner import EvalResult as _R

        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            results = [_R("a", True), _R("b", False), _R("c", True)]
            save_baseline(wd, results)
            loaded = load_baseline(wd)
            if loaded != {"a": True, "b": False, "c": True}:
                return EvalResult(name=self.name, passed=False, detail=f"got {loaded}")
        return EvalResult(name=self.name, passed=True, detail="round-trip preserves pass/fail")


class HarnessPolishEvalDiffDetectsRegression(EvalCase):
    name = "harness-polish-eval-diff-detects-regression"
    description = "diff_against_baseline flags cases that flipped True->False as regressed"

    def run(self) -> EvalResult:
        from loom.eval.baseline import diff_against_baseline, save_baseline
        from loom.eval.runner import EvalResult as _R

        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            save_baseline(wd, [_R("auth", True), _R("flask", True)])
            diff = diff_against_baseline(wd, [_R("auth", False), _R("flask", True)])
            if diff is None:
                return EvalResult(name=self.name, passed=False, detail="no diff returned")
            if "auth" not in diff.regressed:
                return EvalResult(name=self.name, passed=False, detail=f"auth not in {diff.regressed}")
        return EvalResult(name=self.name, passed=True, detail="regression detected correctly")


class HarnessPolishEvalInitSubcommand(EvalCase):
    name = "harness-polish-eval-init-subcommand"
    description = "`loom eval init <target>` scaffolds the harness starter files"

    def run(self) -> EvalResult:
        import subprocess
        from sys import executable
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "newproject"
            proc = subprocess.run(
                [executable, "-m", "loom.cli", "eval", "init", str(target)],
                cwd=str(Path.cwd()),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return EvalResult(name=self.name, passed=False, detail=f"exit {proc.returncode}: {proc.stderr}")
            if not (target / "AGENTS.md").exists():
                return EvalResult(name=self.name, passed=False, detail="AGENTS.md not scaffolded")
            if not (target / ".github" / "workflows" / "loom-eval.yml").exists():
                return EvalResult(name=self.name, passed=False, detail="workflow not scaffolded")
        return EvalResult(name=self.name, passed=True, detail="eval init scaffolds full harness")


class HarnessPolishEvalBaselineCliFlag(EvalCase):
    name = "harness-polish-eval-baseline-cli-flag"
    description = "`loom eval --baseline` writes .minicode/eval-baseline.json"

    def run(self) -> EvalResult:
        import subprocess
        from sys import executable
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            proc = subprocess.run(
                [executable, "-m", "loom.cli", "eval", "--baseline", "--kind", "harness", "--filter", "harness-polish"],
                cwd=str(wd),
                capture_output=True,
                text=True,
                timeout=120,
            )
            baseline_file = wd / ".minicode" / "eval-baseline.json"
            if not baseline_file.exists():
                return EvalResult(name=self.name, passed=False, detail=f"baseline not written: {proc.stderr}")
            data = json.loads(baseline_file.read_text())
            if "cases" not in data:
                return EvalResult(name=self.name, passed=False, detail=f"no 'cases' key: {data}")
            if not data["cases"]:
                return EvalResult(name=self.name, passed=False, detail="empty cases dict")
        return EvalResult(name=self.name, passed=True, detail="--baseline writes JSON correctly")