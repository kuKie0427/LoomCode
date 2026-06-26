"""Unit tests for loom.tui.slash_commands module.

Tests registration, query helpers, and handler signatures — no real LLM
calls, no real Textual app, no modal interactions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from loom.tui.chat_log import ChatLog
from loom.tui.slash_commands import SLASH_COMMANDS, find_command


def test_registry_has_eleven_commands() -> None:
    """SLASH_COMMANDS has exactly 11 entries (help, init, clear, model,
    sessions, new, connect, resume, status, thinking, quit)."""
    assert len(SLASH_COMMANDS) == 11


def test_quit_aliases() -> None:
    """"q", "quit", and "exit" all resolve to the same SlashCommand."""
    cmd_q = find_command("q")
    cmd_quit = find_command("quit")
    cmd_exit = find_command("exit")
    assert cmd_q is not None
    assert cmd_quit is not None
    assert cmd_exit is not None
    assert cmd_q is cmd_quit is cmd_exit
    assert cmd_q.name == "quit"


def test_find_command_case_insensitive() -> None:
    """find_command is case-insensitive — "HELP" finds the help command."""
    cmd = find_command("HELP")
    assert cmd is not None
    assert cmd.name == "help"


def test_unknown_command_returns_none() -> None:
    """An unrecognized command returns None."""
    assert find_command("nope") is None


def test_run_slash_command_empty_input_is_noop() -> None:
    """run_slash_command("") must not crash — typing "/" alone used to
    raise IndexError because parts[0] was accessed on an empty list.

    Regression: see traceback ending in `parts[0].lower()` → IndexError.
    """
    import asyncio
    from unittest.mock import MagicMock

    from loom.tui.app import AgentTUIApp

    app = MagicMock(spec=AgentTUIApp)
    # run_slash_command is async — call the unbound method with the mock.
    asyncio.run(AgentTUIApp.run_slash_command(app, ""))
    # Also verify whitespace-only input is a no-op.
    asyncio.run(AgentTUIApp.run_slash_command(app, "   "))
    # query_one should never have been called for empty input.
    app.query_one.assert_not_called()


def test_each_command_has_description() -> None:
    """Every SlashCommand entry has a non-empty description."""
    for cmd in SLASH_COMMANDS:
        assert cmd.description, f"{cmd.name!r} has empty description"


def test_handler_is_callable() -> None:
    """Every SlashCommand entry has a callable handler."""
    for cmd in SLASH_COMMANDS:
        assert callable(cmd.handler), f"{cmd.name!r} handler is not callable"


def test_help_handler_emits_commands_note() -> None:
    """The /help handler appends a system note listing available commands."""
    help_cmd = find_command("help")
    assert help_cmd is not None

    mock_app = MagicMock()
    mock_chat_log = MagicMock()
    mock_app.query_one.return_value = mock_chat_log

    async def run() -> None:
        await help_cmd.handler(mock_app, "")

    asyncio.run(run())

    mock_app.query_one.assert_called_once_with(ChatLog)
    mock_chat_log.append_system_note.assert_called_once()

    note_text = mock_chat_log.append_system_note.call_args[0][0]
    assert "/help" in note_text
    assert "/model" in note_text
    assert "/init" in note_text
    assert "/quit" in note_text


def test_init_handler_calls_init_and_displays_results() -> None:
    init_cmd = find_command("init")
    assert init_cmd is not None

    mock_app = MagicMock()
    mock_chat_log = MagicMock()
    mock_app.query_one.return_value = mock_chat_log

    with (
        patch("loom.init_cmd.init") as mock_init,
        patch("loom.detect.detect_project") as mock_detect,
        patch("loom.init_cmd.format_results") as mock_format,
    ):
        mock_init.return_value = []
        mock_detect.return_value = MagicMock()
        mock_format.return_value = "WRITTEN  AGENTS.md\nWRITTEN  init.sh"

        async def run() -> None:
            await init_cmd.handler(mock_app, "")

        asyncio.run(run())

        mock_app.query_one.assert_called_once_with(ChatLog)
        mock_chat_log.append_system_note.assert_called_once()
        note_text = mock_chat_log.append_system_note.call_args[0][0]
        assert "/init" in note_text
        assert "AGENTS.md" in note_text
        assert "init.sh" in note_text
        mock_init.assert_called_once()
        mock_detect.assert_called_once()
        mock_format.assert_called_once()
