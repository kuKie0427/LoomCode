"""Eval cases for SessionEnd init.sh mandatory execution (Phase A3)."""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from loom.agent.config import HarnessConfig
from loom.eval.runner import EvalCase, EvalResult


class SessionEndSkipWhenNoInitSh(EvalCase):
    name = "session-end-skip-when-no-init-sh"
    description = "tmpdir without init.sh, REPL exits cleanly with no init.sh warnings in stderr"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "-m", "loom.cli", "run"],
                input="exit\n",
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            stderr = result.stderr
            if "init.sh exited" in stderr or "init.sh timed out" in stderr:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"stderr contains init.sh warning: {stderr[:300]!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"REPL exited cleanly (rc={result.returncode}), no init.sh warnings in stderr",
            )


class SessionEndRunsInitShWhenExists(EvalCase):
    name = "session-end-runs-init-sh-when-exists"
    description = "tmpdir has executable init.sh that writes marker, REPL exit triggers init.sh"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            marker = tmp_path / "marker-session-end-ran.txt"
            init_sh = tmp_path / "init.sh"
            init_sh.write_text(f"#!/bin/sh\ntouch {marker}\n")
            init_sh.chmod(init_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            result = subprocess.run(
                [sys.executable, "-m", "loom.cli", "run"],
                input="exit\n",
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if not marker.exists():
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Marker {marker} not created. stdout tail: {result.stdout[-300:]!r}, stderr tail: {result.stderr[-300:]!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"init.sh executed on SessionEnd, marker {marker} created",
            )


class SessionEndWarnsOnInitShFailure(EvalCase):
    name = "session-end-warns-on-init-sh-failure"
    description = "init.sh exits 1, REPL warns but does not fail (exit code 0)"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            init_sh = tmp_path / "init.sh"
            init_sh.write_text("#!/bin/sh\nexit 1\n")
            init_sh.chmod(init_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            result = subprocess.run(
                [sys.executable, "-m", "loom.cli", "run"],
                input="exit\n",
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected exit code 0 (warn-only), got {result.returncode}",
                )
            if "init.sh exited" not in result.stderr and "init.sh" not in result.stderr:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected init.sh warning in stderr. Got: {result.stderr[:300]!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"init.sh failure warned (rc={result.returncode}), stderr contains init.sh warning",
            )


class SessionEndSkippedWhenOptOut(EvalCase):
    name = "session-end-skipped-when-opt-out"
    description = "run_init_sh_on_session_end=False prevents init.sh from being checked"

    _saved_config: HarnessConfig | None = None

    def setup(self) -> None:
        import loom.agent.loop as agent_loop

        self._saved_config = agent_loop._active_config

    def teardown(self) -> None:
        import loom.agent.loop as agent_loop

        if self._saved_config is not None:
            agent_loop.apply_config(self._saved_config)

    def run(self) -> EvalResult:
        import loom.agent.loop as agent_loop

        # Test 1: Default is True
        cfg_default = HarnessConfig.from_defaults()
        if not cfg_default.run_init_sh_on_session_end:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="Default run_init_sh_on_session_end should be True",
            )

        # Test 2: apply_config with False persists correctly
        cfg_off = HarnessConfig(
            policy=cfg_default.policy,
            checkpoint=cfg_default.checkpoint,
            run_init_sh_on_session_end=False,
        )
        agent_loop.apply_config(cfg_off)
        if agent_loop._active_config.run_init_sh_on_session_end:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="apply_config with run_init_sh_on_session_end=False did not set flag to False",
            )

        # Test 3: explicit True also persists
        cfg_on = HarnessConfig(
            policy=cfg_default.policy,
            checkpoint=cfg_default.checkpoint,
            run_init_sh_on_session_end=True,
        )
        if not cfg_on.run_init_sh_on_session_end:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="Explicit run_init_sh_on_session_end=True not preserved",
            )

        return EvalResult(
            name=self.name,
            passed=True,
            detail="run_init_sh_on_session_end correctly defaults True, configurable to False via apply_config",
        )
