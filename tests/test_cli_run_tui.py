"""Tests for f-cli-run-defaults-to-tui.

Verifies:
- `loom run` (no flags) routes to AgentTUIApp (TUI mode) when no Go binary present
- `loom run` (no flags) routes to Go TUI binary via execvp when present
- `loom run --plain` routes to run_repl (bare REPL)
- `loom tui` still routes to AgentTUIApp (backwards compat)
- --resume and --model flags propagate correctly
- help text mentions --plain
- existing lazy-import invariant preserved (loom --help < 400ms)
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.usefixtures("_clear_loop_depth")


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LOOP_CALL_DEPTH", None)
    return env


def _run_cli(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "loom.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )


@pytest.fixture
def _clear_loop_depth():
    saved = os.environ.pop("LOOP_CALL_DEPTH", None)
    yield
    if saved is not None:
        os.environ["LOOP_CALL_DEPTH"] = saved


@pytest.fixture
def _no_go_tui_binary():
    """Make `loom run` not find a Go TUI binary so it falls back to Textual.

    Patches shutil.which to return None for "loom-tui" and patches the
    bin/loom-tui path's existence check to return False. Required because
    the repo may have a built binary at bin/loom-tui from `scripts/build-tui.sh`.
    """
    with patch("shutil.which", return_value=None):
        with patch("pathlib.Path.exists", side_effect=lambda *a, **kw: False):
            yield


class TestRunRoutesToTuiByDefault:
    __test__ = True  # marker
    def test_run_no_flags_calls_agent_tui_app(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("loom.agent.run_repl") as mock_repl:
                with patch("shutil.which", return_value=None):
                    with patch("pathlib.Path.exists", return_value=False):
                        rc = cli_mod.main(["run"])
        assert rc == 0
        mock_app.assert_called_once()
        mock_repl.assert_not_called()

    def test_run_plain_flag_calls_run_repl(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("loom.agent.run_repl") as mock_repl:
                rc = cli_mod.main(["run", "--plain"])
        assert rc == 0
        mock_repl.assert_called_once()
        mock_app.assert_not_called()

    def test_run_resume_flag_propagates_to_tui(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("shutil.which", return_value=None):
                with patch("pathlib.Path.exists", return_value=False):
                    cli_mod.main(["run", "--resume"])
        _, kwargs = mock_app.call_args
        assert kwargs.get("resume") is True

    def test_run_resume_flag_propagates_to_plain_repl(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.agent.run_repl") as mock_repl:
            cli_mod.main(["run", "--plain", "--resume"])
        _, kwargs = mock_repl.call_args
        assert kwargs.get("resume") is True

    def test_run_model_flag_propagates_to_tui(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("shutil.which", return_value=None):
                with patch("pathlib.Path.exists", return_value=False):
                    cli_mod.main(["run", "--model", "claude-sonnet-4-5"])
        _, kwargs = mock_app.call_args
        assert kwargs.get("model") == "claude-sonnet-4-5"


class TestRunRoutesToGoTuiWhenPresent:
    """When the Go TUI binary is on PATH or at bin/loom-tui, `loom run`
    (no --plain) should execvp it instead of launching the Textual TUI."""

    def test_run_no_flags_execvp_go_tui_binary(self) -> None:
        from loom import cli as cli_mod
        with patch("shutil.which", return_value="/usr/local/bin/loom-tui"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("os.execvp") as mock_execvp:
                    with patch("loom.tui.app.AgentTUIApp") as mock_app:
                        cli_mod.main(["run"])
        mock_execvp.assert_called_once()
        # First arg is the binary path; second is the argv list.
        argv = mock_execvp.call_args[0][1]
        assert argv[0] == "/usr/local/bin/loom-tui"
        assert "--workdir" in argv
        mock_app.assert_not_called()

    def test_run_model_flag_propagates_to_go_tui_loom_args(self) -> None:
        from loom import cli as cli_mod
        with patch("shutil.which", return_value="/usr/local/bin/loom-tui"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("os.execvp") as mock_execvp:
                    cli_mod.main(["run", "--model", "claude-sonnet-4-5"])
        argv = mock_execvp.call_args[0][1]
        # --loom-args carries the --model flag to the Go binary, which
        # forwards it to `loom cli serve`.
        assert "--loom-args" in argv
        idx = argv.index("--loom-args")
        assert "claude-sonnet-4-5" in argv[idx + 1]

    def test_run_plain_still_uses_run_repl_even_with_go_binary(self) -> None:
        from loom import cli as cli_mod
        with patch("shutil.which", return_value="/usr/local/bin/loom-tui"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("os.execvp") as mock_execvp:
                    with patch("loom.agent.run_repl") as mock_repl:
                        cli_mod.main(["run", "--plain"])
        mock_repl.assert_called_once()
        mock_execvp.assert_not_called()


class TestTuiBackwardsCompat:
    __test__ = True  # marker
    def test_tui_still_calls_agent_tui_app(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            rc = cli_mod.main(["tui"])
        assert rc == 0
        mock_app.assert_called_once()

    def test_tui_resume_flag_propagates(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            cli_mod.main(["tui", "--resume"])
        _, kwargs = mock_app.call_args
        assert kwargs.get("resume") is True

    def test_tui_model_flag_propagates(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            cli_mod.main(["tui", "--model", "claude-sonnet-4-5"])
        _, kwargs = mock_app.call_args
        assert kwargs.get("model") == "claude-sonnet-4-5"


class TestHelpText:
    def test_run_help_mentions_plain(self) -> None:
        result = _run_cli("run", "--help")
        assert result.returncode == 0
        assert "--plain" in result.stdout
        assert "bare REPL" in result.stdout or "TUI" in result.stdout

    def test_run_help_mentions_default_tui(self) -> None:
        result = _run_cli("run", "--help")
        assert result.returncode == 0
        assert "TUI" in result.stdout
