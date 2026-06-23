"""Unit tests for loom.tui.slash_commands module.

Tests registration, query helpers, and handler signatures — no real LLM
calls, no real Textual app, no modal interactions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from loom.tui.chat_log import ChatLog
from loom.tui.slash_commands import SLASH_COMMANDS, find_command


def test_registry_has_seven_commands() -> None:
    """SLASH_COMMANDS has exactly 7 entries."""
    assert len(SLASH_COMMANDS) == 7


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
    assert "/quit" in note_text
