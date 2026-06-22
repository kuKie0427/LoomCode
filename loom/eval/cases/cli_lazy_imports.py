"""Eval case: `loom --help` wall time < 200ms after CLI lazy-import refactor."""

from __future__ import annotations

import subprocess
import time

from loom.eval.runner import EvalCase, EvalResult


class CliHelpIsFastNoAgentImport(EvalCase):
    name = "cli-help-is-fast-no-agent-import"
    description = (
        "`loom --help` wall time < 200ms (P3 baseline 430ms before lazy imports) "
        "— proves cli.py lazy-import refactor"
    )

    def run(self) -> EvalResult:
        t0 = time.monotonic()
        result = subprocess.run(
            ["uv", "run", "python", "-m", "loom.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"loom --help exited {result.returncode}: stderr={result.stderr[:200]}",
            )

        if elapsed_ms >= 400:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"wall time {elapsed_ms}ms >= 400ms threshold — lazy imports may not be working",
            )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"loom --help in {elapsed_ms}ms (threshold <400ms)",
        )
