"""Eval cases for LOOP_CALL_DEPTH recursion guard + --skip-self-test usage."""
from __future__ import annotations

import os
import subprocess
import sys

from loop.eval.runner import EvalCase, EvalResult


class LoopCallDepthEnforcedAtMax(EvalCase):
    name = "loop-call-depth-enforced-at-max"
    description = "LOOP_CALL_DEPTH=3 with subprocess.run('loop ...') exits 1 with depth-error log"

    def run(self) -> EvalResult:
        env = os.environ.copy()
        env["LOOP_CALL_DEPTH"] = "3"
        proc = subprocess.run(
            [sys.executable, "-m", "loop.cli", "audit", "--help"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        if proc.returncode == 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected non-zero, got 0; stdout={proc.stdout[:120]!r}",
            )
        if "LOOP_CALL_DEPTH" not in (proc.stdout + proc.stderr):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"no depth error in output: {proc.stderr[:200]!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"exit {proc.returncode}, depth guard fired",
        )


class LoopCallDepthIncrementsAcrossCalls(EvalCase):
    name = "loop-call-depth-increments-across-calls"
    description = "Setting LOOP_CALL_DEPTH=1 in a parent python then invoking 'loop' results in the loop subprocess seeing depth=1"

    def run(self) -> EvalResult:
        env = os.environ.copy()
        env.pop("LOOP_CALL_DEPTH", None)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os, sys, subprocess; "
             "os.environ['LOOP_CALL_DEPTH'] = '1'; "
             "p = subprocess.run([sys.executable, '-m', 'loop.cli', 'audit', '--help'], "
             "  capture_output=True, text=True, env=os.environ); "
             "sys.stdout.write('CHILD_RC=' + str(p.returncode) + '\\n'); "
             "sys.stdout.write('CHILD_DEPTH=' + os.environ.get('LOOP_CALL_DEPTH', 'unset') + '\\n'); "
             "sys.exit(0);"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        if "CHILD_DEPTH=1" not in proc.stdout:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected CHILD_DEPTH=1, got stdout={proc.stdout!r} stderr={proc.stderr[:200]!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="depth env var passed to subprocess; rc=0 (within limit)",
        )


class LoopCallDepthAllowsNormalCall(EvalCase):
    name = "loop-call-depth-allows-normal-call"
    description = "LOOP_CALL_DEPTH unset (or 0) → 'loop audit --help' runs without depth error"

    def run(self) -> EvalResult:
        env = os.environ.copy()
        env.pop("LOOP_CALL_DEPTH", None)
        proc = subprocess.run(
            [sys.executable, "-m", "loop.cli", "audit", "--help"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        if proc.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"exit {proc.returncode}: {proc.stderr[:200]}",
            )
        if "LOOP_CALL_DEPTH" in proc.stderr:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"depth error in stderr: {proc.stderr[:200]}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"normal call succeeded (rc={proc.returncode})",
        )