"""Eval case: `loom run` (REPL mode) exits in <3s even with a 60s init.sh.

Proves run_repl SessionEnd uses fire-and-forget schedule_init_sh_on_session_end
instead of the synchronous run_init_sh_on_session_end (which would block for 60s).
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class LoomRunQuitDoesNotBlockOnInitSh(EvalCase):
    name = "loom-run-quit-does-not-block-on-init-sh"
    description = (
        "`loom run` (REPL mode) exits in <3s even when init.sh takes 60s "
        "— proves run_repl SessionEnd uses fire-and-forget helper"
    )

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = "test-key-for-eval"

    def teardown(self) -> None:
        if self._old_key:
            os.environ["ANTHROPIC_API_KEY"] = self._old_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_sh = tmp_path / "init.sh"
            init_sh.write_text("#!/bin/sh\nsleep 60\n")
            init_sh.chmod(
                init_sh.stat().st_mode
                | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

            t0 = time.monotonic()
            result = subprocess.run(
                ["uv", "run", "python", "-m", "loom.cli", "run"],
                input="exit\n",
                text=True,
                capture_output=True,
                cwd=tmp_path,
                timeout=10.0,
            )
            elapsed = time.monotonic() - t0

            if result.returncode != 0:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"loom run exited {result.returncode} "
                        f"(elapsed {elapsed:.2f}s). stderr: {result.stderr[:300]}"
                    ),
                )

            if elapsed >= 3.0:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"loom run took {elapsed:.2f}s (\u2265 3s threshold) \u2014 "
                        "blocked on init.sh instead of fire-and-forget"
                    ),
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=(
                    f"loom run (REPL) exited in {elapsed:.2f}s even with "
                    "init.sh=sleep 60 (fire-and-forget confirmed)"
                ),
            )
