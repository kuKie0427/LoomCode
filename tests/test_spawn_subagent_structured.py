from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from anthropic.types import TextBlock

from loop.agent.tools import spawn_subagent


@pytest.fixture
def mock_subagent_client(monkeypatch) -> MagicMock:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [
        TextBlock(type="text", text="Task completed successfully."),
    ]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 50
    mock_client.client.messages.create.return_value = mock_response
    return mock_client


class TestStructuredReturn:
    def test_returns_done_metadata_prefix(self, mock_subagent_client):
        """spawn_subagent returns '[done: N turns, M tool calls]\\n<summary>'."""
        result = spawn_subagent("Do X", llm_client=mock_subagent_client)
        assert result.startswith("[done: ")
        assert "turns," in result
        assert "tool calls]" in result
        assert "Task completed successfully." in result

    def test_counts_turns(self, mock_subagent_client):
        """Single end_turn response = 1 turn."""
        result = spawn_subagent("x", llm_client=mock_subagent_client)
        assert "[done: 1 turns," in result

    def test_counts_tool_calls(self, monkeypatch):
        """Each tool_use in the response increments tool_call_count."""
        tool_response = MagicMock()
        tool_response.stop_reason = "tool_use"
        tool_response.usage.input_tokens = 100
        tool_response.content = [
            MagicMock(type="tool_use", id="tu1", name="bash", input={"command": "ls"}),
        ]
        end_response = MagicMock()
        end_response.stop_reason = "end_turn"
        end_response.usage.input_tokens = 100
        end_response.content = [
            TextBlock(type="text", text="Done."),
        ]
        mock_client = MagicMock()
        mock_client.client.messages.create.side_effect = [tool_response, end_response]

        result = spawn_subagent("x", llm_client=mock_client)
        assert "[done: 2 turns, 1 tool calls]" in result

    def test_zero_tool_calls_when_no_tool_use(self, mock_subagent_client):
        """No tool_use blocks → 0 tool calls."""
        result = spawn_subagent("x", llm_client=mock_subagent_client)
        assert "0 tool calls]" in result
