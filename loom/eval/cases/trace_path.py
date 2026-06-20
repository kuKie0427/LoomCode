"""Eval case: `loom trace path` returns a valid path (regression guard).

Background: P3 review surfaced a pre-existing bug where the `trace_path`
subparser had no `--workdir` argument, while `trace_show` did. The shared
handler at loom/cli.py accessed `args.workdir`, raising AttributeError.
Fix: add `--workdir` to `trace_path` subparser (default Path(".")).
This case locks the contract so the bug cannot regress.
"""

from __future__ import annotations

import subprocess

from loom.eval.runner import EvalCase, EvalResult


class TracePathPrintsValidPath(EvalCase):
    name = "loom-trace-path-prints-valid-path"
    description = (
        "`loom trace path` exits 0 and prints a path ending in "
        "trace.jsonl (regression guard for missing --workdir arg)"
    )

    def run(self) -> EvalResult:
        result = subprocess.run(
            ["uv", "run", "python", "-m", "loom.cli", "trace", "path"],
            capture_output=True, text=True, timeout=30.0,
        )
        if result.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"loom trace path exited {result.returncode}. "
                    f"stderr: {result.stderr[:300]}"
                ),
            )
        path_str = result.stdout.strip()
        if not path_str.endswith("trace.jsonl"):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"unexpected output (not a trace.jsonl path): {path_str!r}",
            )

        result2 = subprocess.run(
            ["uv", "run", "python", "-m", "loom.cli", "trace", "path",
             "--workdir", "/tmp"],
            capture_output=True, text=True, timeout=30.0,
        )
        if result2.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"loom trace path --workdir /tmp exited {result2.returncode}. "
                    f"stderr: {result2.stderr[:300]}"
                ),
            )
        path2 = result2.stdout.strip()
        if "/tmp" not in path2 or not path2.endswith("trace.jsonl"):
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"--workdir not honored: expected path under /tmp, "
                    f"got {path2!r}"
                ),
            )

        return EvalResult(
            name=self.name, passed=True,
            detail=(
                f"default → {path_str}; --workdir /tmp → {path2}"
            ),
        )
