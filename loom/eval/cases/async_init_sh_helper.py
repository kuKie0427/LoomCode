"""Eval cases for schedule_init_sh_on_session_end fire-and-forget helper (Phase P1)."""

from __future__ import annotations

import stat
import tempfile
import threading
import time
from pathlib import Path

from loom.agent.config import HarnessConfig
from loom.agent.loop import schedule_init_sh_on_session_end
from loom.eval.runner import EvalCase, EvalResult


class HelperReturnsDaemonThread(EvalCase):
    name = "helper-returns-daemon-thread"
    description = "schedule_init_sh_on_session_end returns threading.Thread with daemon=True"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            thread = schedule_init_sh_on_session_end(
                Path(tmp), HarnessConfig.from_defaults(),
            )
            thread.join(timeout=0.5)
            if not isinstance(thread, threading.Thread):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"Expected threading.Thread, got {type(thread).__name__}",
                )
            if not thread.daemon:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="Thread.daemon is False; helper must spawn a daemon thread",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="helper returned threading.Thread with daemon=True",
            )


class HelperReturnsImmediately(EvalCase):
    name = "helper-spawns-thread-and-returns-immediately"
    description = (
        "schedule_init_sh_on_session_end returns in <0.5s even when init.sh "
        "would block for 60s (proves fire-and-forget contract)"
    )

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_sh = tmp_path / "init.sh"
            init_sh.write_text("#!/bin/sh\nsleep 60\n")
            init_sh.chmod(init_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            config = HarnessConfig.from_defaults()

            t_start = time.monotonic()
            thread = schedule_init_sh_on_session_end(tmp_path, config, timeout=120.0)
            elapsed = time.monotonic() - t_start
            thread.join(timeout=0.05)

            if elapsed >= 0.5:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"helper took {elapsed:.3f}s to return, expected <0.5s "
                        f"(fire-and-forget contract violated — init.sh ran synchronously)"
                    ),
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=(
                    f"helper returned in {elapsed:.3f}s (fire-and-forget). "
                    f"init.sh runs in daemon thread, doesn't block caller."
                ),
            )