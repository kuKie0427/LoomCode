"""Unit tests for ToolCallMarker 1Hz glyph cycle behavior (P2a).

Covers:
- Default idle state with base glyph
- Glyph cycling sequence ⊙ → ⊚ → ◎ → ⊙
- set_complete() freezes cycle
- set_complete(is_error=True) shows ⊗ + tool-error class
- State change stops cycle timer
- ChatLog propagates engine_state to markers
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


# ── Test 1: default idle state ─────────────────────────────────────────────────


def test_tool_marker_default_idle():
    """New ToolCallMarker starts in idle state with base glyph ⊙."""
    marker = ToolCallMarker("bash", '{"cmd": "ls"}')
    assert marker.engine_state == "idle"
    assert marker.render() == "⊙ bash · running"
    assert marker._cycle_idx == 0
    assert marker._cycle_timer is None


# ── Test 2: glyph cycle sequence ───────────────────────────────────────────────


def test_tool_marker_executing_cycle():
    """engine_state='executing' + 3 ticks → glyph sequence ⊙ → ⊚ → ◎ → ⊙."""
    marker = ToolCallMarker("bash", '{"cmd": "ls"}')
    # Patch set_interval to avoid needing a running event loop
    with patch.object(marker, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        marker.engine_state = "executing"
    # Tick 1: idx 0 → 1, glyph ⊚
    marker._tick_cycle()
    assert marker._cycle_idx == 1
    assert marker.render() == "⊚ bash · running"
    # Tick 2: idx 1 → 2, glyph ◎
    marker._tick_cycle()
    assert marker._cycle_idx == 2
    assert marker.render() == "◎ bash · running"
    # Tick 3: idx 2 → 0, glyph ⊙ (wraparound)
    marker._tick_cycle()
    assert marker._cycle_idx == 0
    assert marker.render() == "⊙ bash · running"


# ── Test 3: set_complete(success) freezes cycle ────────────────────────────────


def test_tool_marker_complete_freezes_cycle():
    """set_complete() stops the cycle timer and freezes glyph at ⊙ success."""
    marker = ToolCallMarker("bash", '{"cmd": "ls"}')
    with patch.object(marker, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        marker.engine_state = "executing"
    marker._tick_cycle()  # idx 1
    marker._tick_cycle()  # idx 2
    assert marker._cycle_idx == 2
    marker.set_complete("output text", is_error=False)
    assert marker._complete is True
    assert marker._cycle_timer is None
    assert marker.render() == "⊙ bash · done"
    # _tick_cycle should be no-op after set_complete due to _complete guard
    marker._tick_cycle()
    assert marker._cycle_idx == 2, "idx should not advance after set_complete"


# ── Test 4: set_complete(error) freezes with ⊗ ────────────────────────────────


def test_tool_marker_complete_error_freezes():
    """set_complete(is_error=True) freezes at ⊗ glyph + tool-error class."""
    marker = ToolCallMarker("bash", '{"cmd": "ls"}')
    with patch.object(marker, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        marker.engine_state = "executing"
    marker.set_complete("error msg", is_error=True)
    assert marker._complete is True
    assert marker._cycle_timer is None
    assert marker.render() == "⊗ bash · error"
    assert marker.has_class("tool-error")


# ── Test 5: state change stops cycle ───────────────────────────────────────────


def test_tool_marker_state_change_stops_cycle():
    """executing → streaming (or any non-executing) stops cycle timer."""
    marker = ToolCallMarker("bash", '{"cmd": "ls"}')
    with patch.object(marker, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        marker.engine_state = "executing"
    assert marker._cycle_timer is not None, "cycle should start when entering executing"
    marker.engine_state = "streaming"
    assert marker._cycle_timer is None, "cycle should stop on non-executing state"
    assert marker._cycle_idx == 0, "idx should reset to 0"
    assert marker.render() == "⊙ bash · running"


# ── Test 6: ChatLog propagates to markers ──────────────────────────────────────


def test_chatlog_propagates_engine_state_to_markers(log_no_async):
    """ChatLog.engine_state propagates to all live markers via watch_engine_state."""
    log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
    marker = log_no_async._tool_markers["tool_1"]
    assert marker.engine_state == "idle"  # default from add_tool_call_inline sync
    with patch.object(marker, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        log_no_async.engine_state = "executing"
    assert marker.engine_state == "executing", "marker should inherit ChatLog state"
    log_no_async.engine_state = "idle"
    assert marker.engine_state == "idle", "marker should sync to new state"
