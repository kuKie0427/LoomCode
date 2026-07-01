"""Tests for P0-H: tool_result integrity.

Verifies that ``_run_tool_block`` ALWAYS returns a tool_result dict,
even when the tool handler raises an exception. This prevents the
"assistant message with tool_calls not followed by tool messages"
error that DeepSeek/OpenAI reject with 400.

Covers:
- Non-subagent tool raises ValueError → tool_result returned (not re-raised)
- Non-subagent tool raises TypeError → tool_result returned
- Non-subagent tool raises OSError → tool_result returned
- Subagent tool raises Exception → tool_result returned (not re-raised)
- Subagent tool error still fires on_subagent_end with state="error"
- Serialization: user message with only tool_results → no empty user msg
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from loom.agent.loop import _run_tool_block
from loom.agent.providers._openai_shared import _serialize_messages


class _FakeBlock:
    """Mimic a tool_use content block."""

    def __init__(self, name: str, input: dict | None = None, id: str = "t1") -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input or {}
        self.id = id


class _FakeHooks:
    """Minimal hooks stub that does nothing."""

    def trigger_hooks(self, event, *args):
        return None


# ---------------------------------------------------------------------------
# _run_tool_block — non-subagent tools
# ---------------------------------------------------------------------------


def test_non_subagent_value_error_returns_tool_result():
    """非子代理工具抛 ValueError → 应返回 tool_result，不 re-raise。"""
    block = _FakeBlock("bash", {"command": "ls"})

    def boom(**kwargs):
        raise ValueError("something went wrong")

    with patch("loom.agent.loop.get_tool_handlers", return_value={"bash": boom}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "t1"
    assert "ValueError" in result["content"]
    assert "something went wrong" in result["content"]


def test_non_subagent_type_error_returns_tool_result():
    """非子代理工具抛 TypeError → 应返回 tool_result。"""
    block = _FakeBlock("bash", {"command": "ls"})

    def boom(**kwargs):
        raise TypeError("missing argument")

    with patch("loom.agent.loop.get_tool_handlers", return_value={"bash": boom}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["type"] == "tool_result"
    assert "TypeError" in result["content"]


def test_non_subagent_os_error_returns_tool_result():
    """非子代理工具抛 OSError → 应返回 tool_result。"""
    block = _FakeBlock("read_file", {"path": "/nonexistent"})

    def boom(**kwargs):
        raise OSError("disk full")

    with patch("loom.agent.loop.get_tool_handlers", return_value={"read_file": boom}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["type"] == "tool_result"
    assert "OSError" in result["content"]
    assert "disk full" in result["content"]


# ---------------------------------------------------------------------------
# _run_tool_block — subagent tools
# ---------------------------------------------------------------------------


def test_subagent_error_returns_tool_result_not_reraise():
    """子代理工具抛异常 → 应返回 tool_result，不 re-raise。"""
    block = _FakeBlock("task", {"description": "do something"})

    def boom(**kwargs):
        raise RuntimeError("subagent crashed")

    mock_cb = {
        "on_subagent_start": MagicMock(),
        "on_subagent_end": MagicMock(),
    }
    with patch("loom.agent.loop.get_tool_handlers", return_value={"task": boom}):
        with patch("loom.agent.loop._active_callbacks", mock_cb):
            result = _run_tool_block(block, _FakeHooks())

    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "t1"
    assert "RuntimeError" in result["content"]
    assert "subagent crashed" in result["content"]


def test_subagent_error_fires_on_subagent_end_with_error_state():
    """子代理工具异常时 on_subagent_end 应被调用且 state="error"。"""
    block = _FakeBlock("task", {"description": "do something"})

    def boom(**kwargs):
        raise RuntimeError("subagent crashed")

    mock_cb = {
        "on_subagent_start": MagicMock(),
        "on_subagent_end": MagicMock(),
    }
    with patch("loom.agent.loop.get_tool_handlers", return_value={"task": boom}):
        with patch("loom.agent.loop._active_callbacks", mock_cb):
            _run_tool_block(block, _FakeHooks())

    mock_cb["on_subagent_end"].assert_called_once()
    call_args = mock_cb["on_subagent_end"].call_args
    # (block.id, elapsed, state)
    assert call_args[0][0] == "t1"
    assert call_args[0][2] == "error"


def test_subagent_success_fires_on_subagent_end_with_done_state():
    """子代理工具成功时 on_subagent_end 应被调用且 state="done"。"""
    block = _FakeBlock("task", {"description": "do something"})

    def ok(**kwargs):
        return "done"

    mock_cb = {
        "on_subagent_start": MagicMock(),
        "on_subagent_end": MagicMock(),
    }
    with patch("loom.agent.loop.get_tool_handlers", return_value={"task": ok}):
        with patch("loom.agent.loop._active_callbacks", mock_cb):
            result = _run_tool_block(block, _FakeHooks())

    assert result["type"] == "tool_result"
    assert result["content"] == "done"
    mock_cb["on_subagent_end"].assert_called_once()
    call_args = mock_cb["on_subagent_end"].call_args
    assert call_args[0][2] == "done"


# ---------------------------------------------------------------------------
# Serialization — no empty user message after tool_results
# ---------------------------------------------------------------------------


def test_serialize_skips_empty_user_after_tool_results():
    """user 消息只含 tool_result → 序列化后不应留下空 user 消息。"""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "output"}]},
    ]
    serialized = _serialize_messages(messages)

    # Should be: [assistant(tool_calls), tool_msg]
    # NOT: [assistant(tool_calls), tool_msg, empty_user]
    roles = [m["role"] for m in serialized]
    assert "tool" in roles
    assert roles.count("user") == 0, f"不应有空 user 消息, got roles: {roles}"


def test_serialize_keeps_user_message_with_text_and_tool_results():
    """user 消息含 tool_result + text → 保留 text 部分，不丢弃。"""
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "output"},
            {"type": "text", "text": "请继续"},
        ]},
    ]
    serialized = _serialize_messages(messages)

    # Should be: [tool_msg, user_msg_with_text]
    roles = [m["role"] for m in serialized]
    assert "tool" in roles
    assert "user" in roles
    user_msg = [m for m in serialized if m["role"] == "user"][0]
    assert "请继续" in str(user_msg["content"])


def test_serialize_mixed_tool_results_preserve_order():
    """多个 tool_result 应保持顺序，且不留下空 user 消息。"""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "read_file", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "out1"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "out2"},
        ]},
    ]
    serialized = _serialize_messages(messages)

    roles = [m["role"] for m in serialized]
    # assistant, tool, tool (no empty user)
    assert roles == ["assistant", "tool", "tool"], f"got: {roles}"


def test_serialize_string_user_message_preserved():
    """string content 的 user 消息应原样保留。"""
    messages = [
        {"role": "user", "content": "继续"},
    ]
    serialized = _serialize_messages(messages)
    assert len(serialized) == 1
    assert serialized[0]["role"] == "user"
    assert serialized[0]["content"] == "继续"
