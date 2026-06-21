"""Unit tests for ChatLog.engine_state reactive behavior (P2a primitive 2).

Covers:
- ChatLog default engine_state is "idle"
- add_tool_call_inline syncs engine_state to new markers BEFORE mount
- ChatLog.engine_state = X propagates to all live markers via watch_engine_state
"""

from unittest.mock import MagicMock, patch

import pytest

from loom.tui.chat_log import ChatLog, ToolCallMarker


@pytest.fixture
def log_no_async():
    chat = ChatLog()
    chat.compose()
    with patch("loom.tui.chat_log.asyncio.create_task") as create_task:
        chat._create_task_mock = create_task
        yield chat


def test_chatlog_engine_state_default_idle():
    """ChatLog initial engine_state == 'idle'."""
    chat = ChatLog()
    chat.compose()
    assert chat.engine_state == "idle"


def test_chatlog_engine_state_propagates_to_new_marker(log_no_async):
    """add_tool_call_inline sets new marker's engine_state = self.engine_state BEFORE mount.

    The race-avoidance pattern (P2a learning): sync the marker's engine_state from
    ChatLog.engine_state immediately after creation but BEFORE the asyncio.create_task
    mount, so the marker has the correct state the moment it's mounted.
    """
    log_no_async.engine_state = "executing"
    # Patch to avoid RuntimeError from marker.watch_engine_state → _start_cycle_timer → set_interval
    with patch.object(ToolCallMarker, "set_interval", return_value=MagicMock()):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
    marker = log_no_async._tool_markers["tool_1"]
    assert marker.engine_state == "executing", (
        f"new marker should inherit ChatLog.engine_state at creation, "
        f"got {marker.engine_state!r}"
    )


def test_chatlog_engine_state_propagates_to_existing_markers(log_no_async):
    """ChatLog.engine_state = X propagates to ALL live markers via watch_engine_state."""
    # Mount 3 markers first
    log_no_async.add_tool_call_inline("bash", {"cmd": "ls"}, "tool_1")
    log_no_async.add_tool_call_inline("read", {"path": "/tmp"}, "tool_2")
    log_no_async.add_tool_call_inline("edit", {"path": "/tmp/x"}, "tool_3")
    # All should start in 'idle' (ChatLog default)
    for tid in ("tool_1", "tool_2", "tool_3"):
        assert log_no_async._tool_markers[tid].engine_state == "idle"
    # Now set ChatLog state — should propagate to all.
    # Patch ToolCallMarker.set_interval to avoid RuntimeError from
    # marker.watch_engine_state → _start_cycle_timer → self.set_interval()
    # when no event loop is running.
    with patch.object(ToolCallMarker, "set_interval", return_value=MagicMock()):
        log_no_async.engine_state = "executing"
    for tid in ("tool_1", "tool_2", "tool_3"):
        assert log_no_async._tool_markers[tid].engine_state == "executing", (
            f"{tid} should sync to 'executing', got "
            f"{log_no_async._tool_markers[tid].engine_state!r}"
        )
    # Set back to idle
    log_no_async.engine_state = "idle"
    for tid in ("tool_1", "tool_2", "tool_3"):
        assert log_no_async._tool_markers[tid].engine_state == "idle"
