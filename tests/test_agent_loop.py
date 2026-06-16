"""Integration tests for agent_loop and spawn_subagent in main.py."""

from unittest.mock import MagicMock

import pytest
from anthropic.types import TextBlock, ToolUseBlock

import main
import hook
from hook import Hooks


@pytest.fixture(autouse=True)
def reset_hooks():
    """Reset HOOKS and main.hooks before each test."""
    hook.HOOKS.clear()
    for event in ("AgentStart", "PreToolUse", "PostToolUse", "AgentStop"):
        hook.HOOKS[event] = []
    main.hooks = Hooks()
    main.hooks.register_hook("PreToolUse", main.hooks.check_permission_hook)
    main.hooks.register_hook("PreToolUse", main.hooks.log_hook)
    main.hooks.register_hook("PostToolUse", main.hooks.log_hook)
    main.hooks.register_hook("AgentStart", main.hooks.log_hook)
    main.hooks.register_hook("AgentStop", main.hooks.log_hook)
    main.hooks.register_hook("AgentStop", main.context.microcompact)


class TestAgentLoopSingleTurn:
    def test_agent_loop_single_turn_no_tools(self, mocker):
        """Single turn with no tool calls: messages grow by 2, loop exits."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text="Hi there!")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_client.messages.create.return_value = mock_response

        main.llm_client.client = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        main.agent_loop(messages)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"][0].text == "Hi there!"


class TestAgentLoopStops:
    def test_agent_loop_stops_on_end_turn(self, mocker):
        """Agent stops when stop_reason != 'tool_use', no infinite loop."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text="Done")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_client.messages.create.return_value = mock_response

        main.llm_client.client = mock_client

        messages = [{"role": "user", "content": "Do something"}]
        main.agent_loop(messages)

        assert len(messages) == 2
        mock_client.messages.create.assert_called_once()


class TestAgentLoopToolUse:
    def test_agent_loop_tool_use_bash(self, mocker):
        """Bash tool execution flow: PreToolUse → execute → PostToolUse → result."""
        mock_result = MagicMock()
        mock_result.stdout = "hello\n"
        mock_result.stderr = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        hook_spy = mocker.spy(main.hooks, "trigger_hooks")

        tool_response = MagicMock()
        tool_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tu_001",
                name="bash",
                input={"command": "echo hello"},
            )
        ]
        tool_response.stop_reason = "tool_use"
        tool_response.usage.input_tokens = 100

        end_response = MagicMock()
        end_response.content = [TextBlock(type="text", text="Command executed")]
        end_response.stop_reason = "end_turn"
        end_response.usage.input_tokens = 100

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, end_response]
        main.llm_client.client = mock_client

        messages = [{"role": "user", "content": "Run echo hello"}]
        main.agent_loop(messages)

        assert len(messages) == 4

        tool_result_msg = messages[2]
        assert tool_result_msg["role"] == "user"
        assert isinstance(tool_result_msg["content"], list)
        assert tool_result_msg["content"][0]["type"] == "tool_result"
        assert tool_result_msg["content"][0]["content"] == "hello"
        assert tool_result_msg["content"][0]["is_error"] is False

        assert hook_spy.call_count >= 4

    def test_agent_loop_tool_use_blocked(self, mocker):
        """PreToolUse blocks tool → tool_result has Permission denied."""
        hook.HOOKS.clear()
        for event in ("AgentStart", "PreToolUse", "PostToolUse", "AgentStop"):
            hook.HOOKS[event] = []
        main.hooks = Hooks()

        def block_rm(event, block):
            if block.name == "bash" and "rm" in block.input.get("command", ""):
                return "Permission denied."
            return None

        main.hooks.register_hook("PreToolUse", block_rm)
        main.hooks.register_hook("PreToolUse", main.hooks.log_hook)
        main.hooks.register_hook("PostToolUse", main.hooks.log_hook)
        main.hooks.register_hook("AgentStart", main.hooks.log_hook)
        main.hooks.register_hook("AgentStop", main.hooks.log_hook)
        main.hooks.register_hook("AgentStop", main.context.microcompact)

        tool_response = MagicMock()
        tool_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tu_002",
                name="bash",
                input={"command": "rm file.txt"},
            )
        ]
        tool_response.stop_reason = "tool_use"
        tool_response.usage.input_tokens = 100

        end_response = MagicMock()
        end_response.content = [
            TextBlock(type="text", text="I tried to remove the file")
        ]
        end_response.stop_reason = "end_turn"
        end_response.usage.input_tokens = 100

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, end_response]
        main.llm_client.client = mock_client

        messages = [{"role": "user", "content": "Delete file.txt"}]
        main.agent_loop(messages)

        tool_result_msg = messages[2]
        assert tool_result_msg["content"][0]["content"] == "Permission denied."
        assert tool_result_msg["content"][0]["is_error"] is True

    def test_agent_loop_unknown_tool(self, mocker):
        """Unknown tool name → tool_result content is 'Unknown: xxx'."""
        tool_response = MagicMock()
        tool_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tu_003",
                name="nonexistent_tool",
                input={},
            )
        ]
        tool_response.stop_reason = "tool_use"
        tool_response.usage.input_tokens = 100

        end_response = MagicMock()
        end_response.content = [
            TextBlock(type="text", text="Fallback response")
        ]
        end_response.stop_reason = "end_turn"
        end_response.usage.input_tokens = 100

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, end_response]
        main.llm_client.client = mock_client

        messages = [{"role": "user", "content": "Use an unknown tool"}]
        main.agent_loop(messages)

        tool_result_msg = messages[2]
        assert tool_result_msg["content"][0]["content"] == "Unknown: nonexistent_tool"


class TestSpawnSubagent:
    def test_spawn_subagent_returns_summary(self, mocker):
        """spawn_subagent returns extracted text as non-empty string."""
        mock_response = MagicMock()
        mock_response.content = [
            TextBlock(type="text", text="Subagent completed the task successfully.")
        ]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        main.llm_client.client = mock_client

        result = main.spawn_subagent("Analyze the codebase")

        assert isinstance(result, str)
        assert len(result) > 0
        assert result == "Subagent completed the task successfully."
