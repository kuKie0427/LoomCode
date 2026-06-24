"""Eval cases for init.sh polish: Python ruff/mypy detection, generic skeleton, marker injection."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class InitShPolishPythonDetectsRuff(EvalCase):
    name = "init-sh-polish-python-detects-ruff"
    description = "verification_plan() detects [tool.ruff] in pyproject.toml and prepends ruff check . to full"

    def run(self) -> EvalResult:
        from loom.detect import ProjectInfo, verification_plan

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "pyproject.toml").write_text("[tool.ruff]\n")
            proj = ProjectInfo(root=td_path, stack="python")
            plan = verification_plan(proj)

            if "ruff check ." not in plan.full:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"'ruff check .' not found in full plan: {plan.full!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"full plan contains 'ruff check .': {plan.full!r}",
            )


class InitShPolishGenericReturnsSkeleton(EvalCase):
    name = "init-sh-polish-generic-returns-skeleton"
    description = "verification_plan() for generic stack returns skeleton with TODO and STEP markers"

    def run(self) -> EvalResult:
        from loom.detect import ProjectInfo, verification_plan

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            proj = ProjectInfo(root=td_path, stack="generic")
            plan = verification_plan(proj)

            full_str = " ".join(plan.full)
            if "TODO" not in full_str:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"No 'TODO' found in full plan: {plan.full!r}",
                )
            if "STEP 1: tests" not in full_str:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"No 'STEP 1: tests' found in full plan: {plan.full!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"full plan contains 'TODO' and 'STEP 1: tests': {plan.full!r}",
            )


class InitShPolishMarkerInjectionSkipsExisting(EvalCase):
    name = "init-sh-polish-marker-injection-skips-existing"
    description = "_maybe_inject_pytest_markers() returns None when pyproject.toml already has [tool.pytest.ini_options] with slow/snapshot markers (R5)"

    def run(self) -> EvalResult:
        from loom.init_cmd import _maybe_inject_pytest_markers

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            td_path.joinpath("pyproject.toml").write_text(
                "[tool.pytest.ini_options]\nmarkers = [\n"
                '    "slow: marks tests as slow",\n'
                '    "snapshot: visual snapshot tests",\n'
                "]\n"
            )
            result = _maybe_inject_pytest_markers(td_path, False)

            if result is not None:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected None but got: {result!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail="_maybe_inject_pytest_markers returned None (correctly skipped existing config)",
            )
