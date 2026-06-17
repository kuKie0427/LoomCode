"""Eval cases for Scope WIP=1 enforcement."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from loop.agent.scope import check_wip1
from loop.eval.runner import EvalCase, EvalResult


def _write_feature_list(wd: Path, features: list[dict]) -> None:
    (wd / "feature_list.json").write_text(
        json.dumps({"features": features}),
        encoding="utf-8",
    )


class ScopeCheckSilentOnMissingFeatureList(EvalCase):
    name = "scope-check-silent-on-missing-feature-list"
    description = "no feature_list.json → check_wip1 returns [] and logs nothing"

    def run(self) -> EvalResult:
        import shutil
        import tempfile
        wd = Path(tempfile.mkdtemp(prefix="scope-empty-"))
        try:
            result = check_wip1(wd)
            if result != []:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected [], got {result!r}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="missing file → empty list, no warning",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class ScopeCheckSilentOnZeroInProgress(EvalCase):
    name = "scope-check-silent-on-zero-in-progress"
    description = "feature_list with 0 in-progress → no warning"

    def run(self) -> EvalResult:
        import shutil
        import tempfile
        wd = Path(tempfile.mkdtemp(prefix="scope-zero-"))
        try:
            _write_feature_list(wd, [
                {"id": "f-a", "name": "A", "description": "x", "dependencies": [], "status": "done"},
                {"id": "f-b", "name": "B", "description": "y", "dependencies": [], "status": "not-started"},
            ])
            result = check_wip1(wd)
            if result != []:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected [], got {result!r}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="0 in-progress → empty list, no warning",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class ScopeCheckSilentOnOneInProgress(EvalCase):
    name = "scope-check-silent-on-one-in-progress"
    description = "1 in-progress is fine (WIP=1 satisfied) → no warning"

    def run(self) -> EvalResult:
        import shutil
        import tempfile
        wd = Path(tempfile.mkdtemp(prefix="scope-one-"))
        try:
            _write_feature_list(wd, [
                {"id": "f-only", "name": "Only", "description": "z", "dependencies": [], "status": "in-progress"},
            ])
            result = check_wip1(wd)
            if result != ["f-only"]:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected ['f-only'], got {result!r}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="1 in-progress is valid; no warning",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class ScopeCheckWarnsOnMultipleInProgress(EvalCase):
    name = "scope-check-warns-on-multiple-in-progress"
    description = "2+ in-progress → loguru warning captured with both IDs"

    def run(self) -> EvalResult:
        import shutil
        import tempfile

        from loguru import logger
        wd = Path(tempfile.mkdtemp(prefix="scope-multi-"))
        try:
            _write_feature_list(wd, [
                {"id": "f-a", "name": "A", "description": "x", "dependencies": [], "status": "in-progress"},
                {"id": "f-b", "name": "B", "description": "y", "dependencies": [], "status": "in-progress"},
                {"id": "f-c", "name": "C", "description": "z", "dependencies": [], "status": "done"},
            ])
            sink = io.StringIO()
            handler_id = logger.add(sink, level="WARNING")
            try:
                result = check_wip1(wd)
            finally:
                logger.remove(handler_id)
            if sorted(result) != ["f-a", "f-b"]:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected ['f-a', 'f-b'], got {result!r}",
                )
            captured = sink.getvalue()
            if "WIP=1" not in captured or "f-a" not in captured or "f-b" not in captured:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"warning missing IDs: {captured!r}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=f"warned with both IDs: {captured.strip()[:80]!r}",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class ScopeCheckCliInvocationWarns(EvalCase):
    name = "scope-check-cli-invocation-warns"
    description = "End-to-end: 'loop audit' in a tmpdir with 2 in-progress → WIP=1 warning in CLI output (stdout)"

    def run(self) -> EvalResult:
        import shutil
        import tempfile
        wd = Path(tempfile.mkdtemp(prefix="scope-cli-"))
        try:
            _write_feature_list(wd, [
                {"id": "f-x", "name": "X", "description": "x", "dependencies": [], "status": "in-progress"},
                {"id": "f-y", "name": "Y", "description": "y", "dependencies": [], "status": "in-progress"},
            ])
            env_disabled = {"LOOP_CALL_DEPTH": "0"}
            proc = subprocess.run(
                [sys.executable, "-m", "loop.cli", "audit", "--skip-self-test", "--min-score", "0", str(wd)],
                capture_output=True, text=True, env=env_disabled, timeout=10,
                cwd=str(wd),
            )
            combined = proc.stdout + proc.stderr
            if "WIP=1" not in combined or "f-x" not in combined or "f-y" not in combined:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"missing WIP=1 in CLI output: {combined[:300]!r}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="CLI entry warns for 2 in-progress features",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)
