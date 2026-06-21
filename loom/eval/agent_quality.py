"""Agent-quality eval framework.

`AgentQualityCase` exercises the real loom agent end-to-end:
1. Sets up an isolated tmp workspace (small fixture repo, git-init'd).
2. Spawns `python -m loom.cli run` as a subprocess with a fixed user prompt.
3. Captures the resulting `git diff` in the workspace.
4. Asserts the diff matches an expected outcome (exact / contains / LLM-judge).

Cases live in `loom.eval.cases.agent_quality`. They are discovered through
the same `EvalCase` subclass-walking mechanism as harness evals, but they
declare `kind = "agent-quality"` so the CLI `--kind` selector can route.

Invariants:
- Each case gets a fresh tmp workdir under `loop-eval/agent-quality/<case-name>/`.
- Subprocess timeout is 120s by default (configurable per case via `timeout_s`).
- Subprocess inherits ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / MODEL from parent env.
- Subprocess runs with `LOOP_CALL_DEPTH` reset so it doesn't trip the recursion guard.
- A non-zero exit code from `loom run` is NOT automatic failure — the agent might
  have done correct work and then crashed on EOF; we judge on the diff, not exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from loom.eval.runner import EvalCase, EvalResult

DEFAULT_AGENT_TIMEOUT_S = 120


@dataclass
class AgentRunOutcome:
    returncode: int
    stdout: str
    stderr: str
    diff: str
    workspace: Path
    elapsed_s: float
    files_after: dict[str, str] = field(default_factory=dict)


def _agent_quality_root() -> Path:
    root = Path(tempfile.gettempdir()) / "loop-eval" / "agent-quality"
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_agent_workspace(case_name: str, files: dict[str, str]) -> Path:
    """Create a fresh git-initialized workspace seeded with `files`.

    Idempotent: wipes any pre-existing workspace for `case_name` first. The
    initial commit gives every later `capture_diff()` a clean baseline.
    """
    wd = _agent_quality_root() / case_name
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    for relpath, content in files.items():
        target = wd / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(wd, "init", "-q", "-b", "main")
    _git(wd, "config", "user.email", "eval@loom.local")
    _git(wd, "config", "user.name", "loom-eval")
    _git(wd, "add", "-A")
    _git(wd, "commit", "-q", "-m", "baseline")
    return wd


def _git(wd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=wd,
        capture_output=True,
        text=True,
        check=False,
    )


def capture_diff(wd: Path) -> str:
    _git(wd, "add", "-N", ".")
    return _git(wd, "diff", "HEAD").stdout


def run_agent(wd: Path, user_prompt: str, timeout_s: int = DEFAULT_AGENT_TIMEOUT_S) -> AgentRunOutcome:
    """Spawn `python -m loom.cli run` with `user_prompt` on stdin.

    The agent runs ONE turn (until EOF closes stdin), then exits. Captures
    stdout/stderr/diff for `judge()`.
    """
    env = os.environ.copy()
    env.pop("LOOP_CALL_DEPTH", None)
    env["LOOM_EVAL_MODE"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "loom.cli", "run"],
            cwd=wd,
            input=user_prompt + "\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
        )
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        stderr = (exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "") + f"\n[TIMEOUT after {timeout_s}s]"
    elapsed_s = time.monotonic() - t0

    diff = capture_diff(wd)
    files_after = {
        str(p.relative_to(wd)): p.read_text(encoding="utf-8", errors="replace")
        for p in wd.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }

    return AgentRunOutcome(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        diff=diff,
        workspace=wd,
        elapsed_s=elapsed_s,
        files_after=files_after,
    )


class AgentQualityCase(EvalCase):
    """Base class for evals that exercise the real loom agent.

    Subclasses MUST override `name`, `description`, `files`, `user_prompt`,
    and `judge(outcome) -> (passed, detail)`. MAY override `timeout_s` (120 default).
    """

    kind: ClassVar[str] = "agent-quality"
    timeout_s: ClassVar[int] = DEFAULT_AGENT_TIMEOUT_S
    files: ClassVar[dict[str, str]] = {}
    user_prompt: ClassVar[str] = ""

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        raise NotImplementedError

    def run(self) -> EvalResult:
        if not self.user_prompt:
            return EvalResult(name=self.name, passed=False, detail="case has empty user_prompt")
        if not self.files:
            return EvalResult(name=self.name, passed=False, detail="case has empty files fixture")

        wd = make_agent_workspace(self.name, self.files)
        try:
            outcome = run_agent(wd, self.user_prompt, timeout_s=self.timeout_s)
        except Exception as exc:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"run_agent raised {type(exc).__name__}: {exc}"[:300],
            )

        try:
            passed, detail = self.judge(outcome)
        except Exception as exc:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"judge raised {type(exc).__name__}: {exc}"[:300],
            )

        meta = f"[{outcome.elapsed_s:.1f}s rc={outcome.returncode}]"
        return EvalResult(name=self.name, passed=passed, detail=f"{meta} {detail}"[:300])


def diff_contains(outcome: AgentRunOutcome, *needles: str) -> tuple[bool, str]:
    missing = [n for n in needles if n not in outcome.diff]
    if missing:
        return False, f"diff missing: {missing}"
    return True, f"all {len(needles)} needles present in diff"


def file_contains(outcome: AgentRunOutcome, path: str, *needles: str) -> tuple[bool, str]:
    content = outcome.files_after.get(path)
    if content is None:
        return False, f"file {path!r} not in workspace after run"
    missing = [n for n in needles if n not in content]
    if missing:
        return False, f"{path}: missing {missing}"
    return True, f"{path} contains all {len(needles)} needles"


def file_lacks(outcome: AgentRunOutcome, path: str, *needles: str) -> tuple[bool, str]:
    content = outcome.files_after.get(path)
    if content is None:
        return False, f"file {path!r} not in workspace after run"
    present = [n for n in needles if n in content]
    if present:
        return False, f"{path}: unexpected {present}"
    return True, f"{path} correctly lacks all {len(needles)} forbidden strings"
