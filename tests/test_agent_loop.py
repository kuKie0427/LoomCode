"""Integration tests for agent_loop and spawn_subagent in main.py."""

from unittest.mock import MagicMock

import pytest
from anthropic.types import TextBlock, ToolUseBlock

import loom.agent.hooks as hook
import loom.agent.loop as main
import loom.agent.tools
from loom.agent.hooks import Hooks


@pytest.fixture(autouse=True)
def reset_hooks():
    """Reset HOOKS and main.hooks before each test."""
    hook.HOOKS.clear()
    for event in ("SessionStart", "AgentStart", "PreToolUse", "PostToolUse", "AgentStop", "SessionEnd"):
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
        for event in ("SessionStart", "AgentStart", "PreToolUse", "PostToolUse", "AgentStop", "SessionEnd"):
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
    def test_spawn_subagent_returns_summary_with_metadata(self, mocker):
        """spawn_subagent returns structured result: '[done: N turns, M tool calls]\\n<summary>'."""
        mock_response = MagicMock()
        mock_response.content = [
            TextBlock(type="text", text="Subagent completed the task successfully.")
        ]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        main.llm_client.client = mock_client

        result = loom.agent.tools.spawn_subagent("Analyze the codebase")

        assert isinstance(result, str)
        assert "Subagent completed the task successfully." in result
        assert result.startswith("[done: ")
        assert "turns," in result
        assert "tool calls]" in result


# ── f-tui-subagent-click-jump refactor tests ───────────────────────────────


class TestRunToolBlockSubagentCallbacks:
    def test_run_tool_block_fires_subagent_start_with_block_id(self, mocker):
        """For task tools, _run_tool_block fires on_subagent_start using
        block.id (tool_use_id) as the subagent_id and the description from
        block.input."""
        captured: list[tuple] = []

        def cb_start(subagent_id: str, description: str) -> None:
            captured.append(("start", subagent_id, description))

        def cb_end(subagent_id: str, elapsed: float, state: str) -> None:
            captured.append(("end", subagent_id, elapsed, state))

        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS,
            {"task": lambda description: "stub-result"},
        )

        block = MagicMock()
        block.id = "toolu_abc123"
        block.name = "task"
        block.input = {"description": "Extract MCP schema"}

        main.set_active_callbacks(
            {"on_subagent_start": cb_start, "on_subagent_end": cb_end}
        )
        try:
            main._run_tool_block(block, main.hooks)
        finally:
            main.clear_active_callbacks()

        assert len(captured) == 2, (
            f"expected start + end callback, got {captured}"
        )
        assert captured[0] == ("start", "toolu_abc123", "Extract MCP schema"), (
            f"on_subagent_start should fire with (block.id, description), got {captured[0]}"
        )
        assert captured[1][0] == "end"
        assert captured[1][1] == "toolu_abc123", (
            f"on_subagent_end should fire with same block.id, got {captured[1][1]}"
        )
        assert captured[1][3] == "done"

    def test_run_tool_block_fires_subagent_end_with_error_state_on_exception(self, mocker):
        """When the task handler raises, on_subagent_end fires with state='error'."""
        captured: list[tuple] = []

        def cb_start(subagent_id: str, description: str) -> None:
            captured.append(("start", subagent_id))

        def cb_end(subagent_id: str, elapsed: float, state: str) -> None:
            captured.append(("end", subagent_id, state))

        def fail_handler(description: str) -> str:
            raise RuntimeError("subagent exploded")

        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS, {"task": fail_handler}
        )

        block = MagicMock()
        block.id = "toolu_xyz"
        block.name = "task"
        block.input = {"description": "will fail"}

        main.set_active_callbacks(
            {"on_subagent_start": cb_start, "on_subagent_end": cb_end}
        )
        try:
            try:
                main._run_tool_block(block, main.hooks)
            except RuntimeError:
                pass
        finally:
            main.clear_active_callbacks()

        assert len(captured) == 2
        assert captured[1] == ("end", "toolu_xyz", "error"), (
            f"on_subagent_end should fire with state='error' on exception, got {captured[1]}"
        )

    def test_run_tool_block_does_not_fire_subagent_for_non_task_tools(self, mocker):
        """Non-task tools (e.g., bash) must not fire on_subagent_start / on_subagent_end."""
        captured: list[tuple] = []

        def cb_start(subagent_id: str, description: str) -> None:
            captured.append(("start", subagent_id))

        def cb_end(subagent_id: str, elapsed: float, state: str) -> None:
            captured.append(("end", subagent_id, state))

        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS,
            {"bash": lambda command: "ls output"},
        )

        block = MagicMock()
        block.id = "tu_001"
        block.name = "bash"
        block.input = {"command": "ls"}

        main.set_active_callbacks(
            {"on_subagent_start": cb_start, "on_subagent_end": cb_end}
        )
        try:
            main._run_tool_block(block, main.hooks)
        finally:
            main.clear_active_callbacks()

        assert captured == [], (
            f"non-task tools must NOT fire subagent callbacks, got {captured}"
        )

    def test_run_tool_block_no_active_callbacks_is_silent(self, mocker):
        """Without set_active_callbacks, _run_tool_block is silent for subagent path."""
        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS,
            {"task": lambda description: "result"},
        )

        main.clear_active_callbacks()
        block = MagicMock()
        block.id = "toolu_silent"
        block.name = "task"
        block.input = {"description": "no callbacks"}

        result = main._run_tool_block(block, main.hooks)
        assert result["content"] == "result"


# ── LOW-2: _run_tool_block description truncation with `…` indicator ──────────


class TestRunToolBlockDescriptionTruncation:
    def test_long_description_truncates_with_ellipsis(self, mocker):
        """80-char description → result description is exactly 60 chars (59 + U+2026)
        and ends with `…`. Locks in LOW-2 truncation behavior."""
        captured: list[tuple] = []

        def cb_start(subagent_id: str, description: str) -> None:
            captured.append(("start", subagent_id, description))

        def cb_end(subagent_id: str, elapsed: float, state: str) -> None:
            captured.append(("end", subagent_id, elapsed, state))

        raw = "a" * 80
        expected_truncated = raw[:59] + "…"

        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS,
            {"task": lambda description: "stub-result"},
        )

        block = MagicMock()
        block.id = "toolu_long"
        block.name = "task"
        block.input = {"description": raw}

        main.set_active_callbacks(
            {"on_subagent_start": cb_start, "on_subagent_end": cb_end}
        )
        try:
            main._run_tool_block(block, main.hooks)
        finally:
            main.clear_active_callbacks()

        assert len(captured) >= 1, f"expected start callback, got {captured}"
        assert captured[0][0] == "start"
        assert captured[0][1] == "toolu_long"
        assert captured[0][2] == expected_truncated, (
            f"long description should be truncated to 59 chars + '…', "
            f"got {captured[0][2]!r}"
        )
        assert len(captured[0][2]) == 60, (
            f"truncated description length should be 60 (59 + U+2026), "
            f"got {len(captured[0][2])}"
        )
        assert captured[0][2].endswith("…"), (
            f"truncated description must end with U+2026 '…', got {captured[0][2]!r}"
        )

    def test_short_description_passes_through(self, mocker):
        """30-char description → result is the exact 30-char string, NO `…` appended.
        Locks in the pass-through branch of the truncation ternary."""
        captured: list[tuple] = []

        def cb_start(subagent_id: str, description: str) -> None:
            captured.append(("start", subagent_id, description))

        def cb_end(subagent_id: str, elapsed: float, state: str) -> None:
            captured.append(("end", subagent_id, elapsed, state))

        raw = "a" * 30

        mocker.patch.dict(
            loom.agent.tools.TOOL_HANDLERS,
            {"task": lambda description: "stub-result"},
        )

        block = MagicMock()
        block.id = "toolu_short"
        block.name = "task"
        block.input = {"description": raw}

        main.set_active_callbacks(
            {"on_subagent_start": cb_start, "on_subagent_end": cb_end}
        )
        try:
            main._run_tool_block(block, main.hooks)
        finally:
            main.clear_active_callbacks()

        assert len(captured) >= 1, f"expected start callback, got {captured}"
        assert captured[0][2] == raw, (
            f"short description should pass through unchanged, "
            f"got {captured[0][2]!r}"
        )
        assert "…" not in captured[0][2], (
            f"short description must NOT have ellipsis appended, "
            f"got {captured[0][2]!r}"
        )
