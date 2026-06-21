"""Minimal TDD-mode helpers for f-tdd-agent-mode-p4.

Scope (deliberately small):
  - run_pytest(args): subprocess pytest + capture stdout/stderr/exit code
  - is_test_file(path): quick predicate for anti-reward-hacking guard
  - build_focused_prompt(test_path, failure): builds a fixed string prompt
    that tells the model: identify the failing test, find the source code
    under test, apply minimal fix, re-run pytest. **Do NOT edit the test
    file itself.**

Out of scope (deliberately deferred): actually running the agent in a
loop here. This module only provides the *building blocks* the REPL or
TUI will wire up. The agent loop already has the test-fix retry logic
from f-tool-error-semantics-p2; this just exposes a hand-off seam.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TESTS_DIR_PARTS = ("tests/", "test/")
_TEST_SUFFIXES = ("test_", "_test.py", "test.py")


def is_test_file(path: str | Path) -> bool:
    """True if `path` looks like a test file (anti-reward-hacking guard).

    Heuristic, not a full AST scan — we want cheap & obvious, because the
    agent will try to game it.
    """
    p = Path(path)
    parts = [part.lower() for part in p.parts]
    if any(part in _TEST_SUFFIXES for part in parts):
        return True
    name = p.name.lower()
    if name.startswith("test_") or name.endswith("_test.py") or name == "test.py":
        return True
    if any(d in p.parts for d in ("tests", "test")) and name.endswith(".py"):
        return True
    return False


def run_pytest(
    test_path: str | Path,
    *,
    cwd: str | Path | None = None,
    timeout: float = 120.0,
    extra_args: tuple[str, ...] = (),
) -> "PytestRun":
    """Run pytest on `test_path` and capture exit + tail of output.

    Returns a `PytestRun` namedtuple-ish dataclass so callers don't
    have to deal with subprocess.CompletedProcess directly.
    """
    cmd = ["python", "-m", "pytest", str(test_path), "-x", "--tb=short", *extra_args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return PytestRun(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            command=cmd,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        def _decode(b: object) -> str:
            if isinstance(b, bytes):
                return b.decode("utf-8", errors="replace")
            return b or ""
        return PytestRun(
            exit_code=-1,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr) + f"\n[timeout after {timeout}s]",
            command=cmd,
            timed_out=True,
        )


class PytestRun:
    """Result of `run_pytest`. Plain class, not dataclass, to avoid runtime deps."""

    def __init__(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        command: list[str],
        timed_out: bool,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command
        self.timed_out = timed_out

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def tail(self) -> str:
        """Last ~80 lines of combined stdout+stderr — enough to debug, cheap to ship."""
        combined = (self.stdout + self.stderr).splitlines()
        return "\n".join(combined[-80:])

    def __repr__(self) -> str:
        return f"PytestRun(exit={self.exit_code}, timeout={self.timed_out})"


def build_focused_prompt(test_path: str | Path, failure: str, max_iterations: int = 5) -> str:
    """Build a focused prompt the agent will see in TDD mode.

    The key invariant: NEVER instruct the model to edit the test file.
    The reward-hacking guard is encoded here, not in a separate
    validator, because the prompt is the cheapest enforcement layer.
    """
    return (
        f"You are in TDD mode. ONE test file is failing: `{test_path}`.\n\n"
        f"Pytest output:\n```\n{failure}\n```\n\n"
        f"Your job:\n"
        f"1. Read `{test_path}` to understand what behavior is expected.\n"
        f"2. Use `grep`/`read` to find the production code that this test exercises.\n"
        f"3. Apply a MINIMAL fix to the production code (NOT the test).\n"
        f"4. Re-run pytest with: `uv run pytest {test_path} -x --tb=short`\n"
        f"5. Iterate up to {max_iterations} times until the test passes.\n\n"
        f"ANTI-REWARD-HACKING GUARD: You MUST NOT edit `{test_path}`.\n"
        f"Modifying a failing test to make it pass defeats the purpose of TDD.\n"
        f"If the test seems wrong, surface that as a final user-facing message\n"
        f"instead of silently changing the test.\n"
    )