from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class EvalBenchmarkResumeCliAvailable(EvalCase):
    name = "eval-benchmark-resume-cli-available"
    description = "loom eval --benchmark resume 是合法的 CLI 调用(不需要真跑 benchmark,只测 parser)"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp("cli-bench-test"))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "loom.cli", "eval", "--benchmark", "resume", "--help"],
                cwd=wd, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return EvalResult(name=self.name, passed=False,
                                  detail=f"exit {proc.returncode}: {proc.stderr[:200]}")
            if "--benchmark" not in (proc.stdout + proc.stderr):
                return EvalResult(name=self.name, passed=False, detail="--benchmark not in help output")
            return EvalResult(name=self.name, passed=True, detail="--benchmark resume in argparse help")
        finally:
            import shutil
            shutil.rmtree(wd, ignore_errors=True)
