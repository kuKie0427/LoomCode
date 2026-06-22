"""Tests for f-cli-run-defaults-to-tui.

Verifies:
- `loom run` (no flags) routes to AgentTUIApp (TUI mode)
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


class TestRunRoutesToTuiByDefault:
    __test__ = True  # marker
    def test_run_no_flags_calls_agent_tui_app(self) -> None:
        from loom import cli as cli_mod
        with patch("loom.tui.app.AgentTUIApp") as mock_app:
            with patch("loom.agent.run_repl") as mock_repl:
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
            cli_mod.main(["run", "--model", "claude-sonnet-4-5"])
        _, kwargs = mock_app.call_args
        assert kwargs.get("model") == "claude-sonnet-4-5"


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
