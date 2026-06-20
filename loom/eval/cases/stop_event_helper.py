"""Eval cases for schedule_init_sh_on_session_end stop_event support (Phase P2)."""

from __future__ import annotations

import stat
import tempfile
import threading
import time
from pathlib import Path

from loom.agent.config import HarnessConfig
from loom.agent.loop import schedule_init_sh_on_session_end
from loom.eval.runner import EvalCase, EvalResult


class StopEventTerminatesRunningInitSh(EvalCase):
    name = "stop-event-terminates-running-init-sh"
    description = (
        "schedule_init_sh_on_session_end with stop_event terminates "
        "the running init.sh subprocess and fires on_complete with error_msg='stopped'"
    )

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_sh = tmp_path / "init.sh"
            init_sh.write_text("#!/bin/sh\nsleep 60\n")
            init_sh.chmod(
                init_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

            config = HarnessConfig.from_defaults()
            stop_event = threading.Event()

            captured: list[tuple] = []

            def on_complete(
                result: object, error_msg: str
            ) -> None:
                captured.append((result, error_msg))

            thread = schedule_init_sh_on_session_end(
                tmp_path,
                config,
                stop_event=stop_event,
                on_complete=on_complete,
                timeout=120.0,
            )

            time.sleep(1.0)

            stop_event.set()

            thread.join(timeout=3.0)

            if thread.is_alive():
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        "Thread still alive 3s after stop_event.set() — "
                        "subprocess not terminated"
                    ),
                )

            if len(captured) != 1:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"Expected on_complete to be called once, "
                        f"got {len(captured)}"
                    ),
                )

            _result, error_msg = captured[0]
            if error_msg != "stopped":
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"Expected error_msg='stopped', got '{error_msg}'"
                    ),
                )

            return EvalResult(
                name=self.name,
                passed=True,
                detail=(
                    "stop_event terminated the running subprocess; "
                    "on_complete fired with error_msg='stopped'"
                ),
            )


class StopEventNoneBackwardCompat(EvalCase):
    name = "stop-event-none-backward-compat"
    description = (
        "schedule_init_sh_on_session_end without stop_event (None default) "
        "preserves backward-compatible subprocess.run behavior"
    )

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            init_sh = tmp_path / "init.sh"
            init_sh.write_text("#!/bin/sh\necho ok\n")
            init_sh.chmod(
                init_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

            config = HarnessConfig.from_defaults()

            captured: list[tuple] = []

            def on_complete(
                result: object, error_msg: str
            ) -> None:
                captured.append((result, error_msg))

            thread = schedule_init_sh_on_session_end(
                tmp_path,
                config,
                on_complete=on_complete,
                timeout=120.0,
            )

            thread.join(timeout=5.0)

            if thread.is_alive():
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="Thread still alive after 5s — subprocess.run should have finished",
                )

            if len(captured) != 1:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"Expected on_complete to be called once, "
                        f"got {len(captured)}"
                    ),
                )

            result, error_msg = captured[0]
            if error_msg != "":
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected error_msg='', got '{error_msg}'",
                )

            if result is None:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="Expected CompletedProcess result, got None",
                )

            return EvalResult(
                name=self.name,
                passed=True,
                detail=(
                    f"stop_event=None preserves subprocess.run: "
                    f"exit {result.returncode}, error_msg=''"
                ),
            )
