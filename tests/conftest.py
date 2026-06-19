"""Shared pytest fixtures for the loom project."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from anthropic import Anthropic
from anthropic.types import MessageParam, TextBlock, ToolResultBlockParam


@pytest.fixture
def temp_workdir(tmp_path: Path) -> Path:
    """Return a temporary working directory path.

    Creates a ``workdir`` subdirectory inside pytest's ``tmp_path``
    so tests can read and write files without touching the real filesystem.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


@pytest.fixture
def sample_messages() -> list[MessageParam]:
    """Return three complete rounds of conversation as MessageParam objects.

    Round 1 & 2: plain user text → plain assistant text
    Round 3:     tool-result user message → plain assistant text
    """
    return [
        # Round 1 — user text → assistant text
        MessageParam(role="user", content="Hello, how are you?"),
        MessageParam(
            role="assistant",
            content=[TextBlock(text="I'm doing well, thanks for asking!", type="text")],
        ),
        # Round 2 — user text → assistant text
        MessageParam(role="user", content="Can you write a function for me?"),
        MessageParam(
            role="assistant",
            content=[
                TextBlock(
                    text="Sure! What kind of function do you need?",
                    type="text",
                )
            ],
        ),
        # Round 3 — tool result user message → assistant text
        MessageParam(
            role="user",
            content=[
                ToolResultBlockParam(
                    tool_use_id="tool_001",
                    content="Here is the result of the tool execution.",
                    type="tool_result",
                )
            ],
        ),
        MessageParam(
            role="assistant",
            content=[
                TextBlock(
                    text="Thanks for the tool result. I can proceed now.",
                    type="text",
                )
            ],
        ),
    ]


@pytest.fixture
def mock_anthropic_client(mocker) -> MagicMock:
    """Return a fully mocked Anthropic client.

    The mock's ``messages.create`` method returns a pre-configured response
    with known content, usage stats, and stop reason.  No real API calls
    are ever made.
    """
    mock_client = MagicMock(spec=Anthropic)
    mock_response = MagicMock()
    mock_response.content = [MagicMock(spec=TextBlock, text="Mock response text")]
    mock_response.usage.input_tokens = 100
    mock_response.stop_reason = "end_turn"
    mock_client.messages.create.return_value = mock_response
    return mock_client
