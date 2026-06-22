"""Eval cases for f-cli-run-defaults-to-tui.

Verifies the CLI routing fix:
- `loom run` (no flags) routes to AgentTUIApp (TUI mode), not bare REPL
- `loom run --plain` flag exists and routes to bare REPL
- `loom tui` still works (backwards compat alias)
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


class CliRunDefaultsToTui(EvalCase):
    name = "cli-run-defaults-to-tui"
    description = "`loom run` (no flags) routes to AgentTUIApp, not run_repl"

    def run(self) -> EvalResult:
        import os

        from loom import cli as cli_mod
        os.environ.pop("LOOP_CALL_DEPTH", None)
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("loom.agent.run_repl") as mock_repl:
                rc = cli_mod.main(["run"])
        if rc != 0:
            return EvalResult(name=self.name, passed=False, detail=f"rc={rc}")
        if not mock_app.called:
            return EvalResult(name=self.name, passed=False, detail="AgentTUIApp not called")
        if mock_repl.called:
            return EvalResult(name=self.name, passed=False, detail="run_repl should not be called without --plain")
        return EvalResult(name=self.name, passed=True, detail="loom run → AgentTUIApp (TUI mode)")


class CliRunPlainFlagRoutesToRepl(EvalCase):
    name = "cli-run-plain-flag-routes-to-repl"
    description = "`loom run --plain` routes to run_repl (bare REPL for CI/scripts)"

    def run(self) -> EvalResult:
        import os

        from loom import cli as cli_mod
        os.environ.pop("LOOP_CALL_DEPTH", None)
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("loom.agent.run_repl") as mock_repl:
                rc = cli_mod.main(["run", "--plain"])
        if rc != 0:
            return EvalResult(name=self.name, passed=False, detail=f"rc={rc}")
        if not mock_repl.called:
            return EvalResult(name=self.name, passed=False, detail="run_repl not called")
        if mock_app.called:
            return EvalResult(name=self.name, passed=False, detail="AgentTUIApp should not be called with --plain")
        return EvalResult(name=self.name, passed=True, detail="loom run --plain → run_repl (bare REPL)")


class CliTuiStillWorksAsAlias(EvalCase):
    name = "cli-tui-still-works-as-alias"
    description = "`loom tui` still routes to AgentTUIApp (backwards compat preserved)"

    def run(self) -> EvalResult:
        import os

        from loom import cli as cli_mod
        os.environ.pop("LOOP_CALL_DEPTH", None)
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            rc = cli_mod.main(["tui"])
        if rc != 0:
            return EvalResult(name=self.name, passed=False, detail=f"rc={rc}")
        if not mock_app.called:
            return EvalResult(name=self.name, passed=False, detail="AgentTUIApp not called for loom tui")
        return EvalResult(name=self.name, passed=True, detail="loom tui → AgentTUIApp (backwards compat)")


class CliRunHelpMentionsPlain(EvalCase):
    name = "cli-run-help-mentions-plain"
    description = "`loom run --help` mentions --plain flag so users discover it"

    def run(self) -> EvalResult:
        import os
        env = os.environ.copy()
        env.pop("LOOP_CALL_DEPTH", None)
        result = subprocess.run(
            [sys.executable, "-m", "loom.cli", "run", "--help"],
            capture_output=True, text=True, timeout=10.0, env=env,
        )
        if result.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"rc={result.returncode}: {result.stderr[:200]}")
        if "--plain" not in result.stdout:
            return EvalResult(name=self.name, passed=False, detail="--plain not in help output")
        return EvalResult(name=self.name, passed=True, detail="--plain flag documented in loom run --help")
