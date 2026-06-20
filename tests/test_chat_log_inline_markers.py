"""Unit tests for ChatLog inline subagent + todo markers (f-tui-inline-event-markers)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from loom.tui.chat_log import (
    ChatLog,
    SubagentMarker,
)


@pytest.fixture
def log():
    chat = ChatLog()
    chat.compose()
    return chat


@pytest.fixture
def log_no_async():
    chat = ChatLog()
    chat.compose()
    with patch("loom.tui.chat_log.asyncio.create_task") as create_task:
        chat._create_task_mock = create_task
        yield chat


# ── SubagentMarker widget ─────────────────────────────────────────────────────


class TestSubagentMarker:
    def test_inherits_from_static(self):
        from textual.widgets import Static

        assert issubclass(SubagentMarker, Static)

    def test_initial_glyph_is_half_circle(self):
        marker = SubagentMarker("toolu_abc", "extract schema")
        assert marker._subagent_id == "toolu_abc"
        assert marker._description == "extract schema"

    def test_initial_text_contains_description(self):
        marker = SubagentMarker("id1", "my task")
        rendered = str(marker.render())
        assert "my task" in rendered
        assert rendered.startswith("◐")

    def test_css_classes_not_set_initially(self):
        marker = SubagentMarker("id1", "desc")
        assert not marker.has_class("marker-done")
        assert not marker.has_class("marker-error")


# ── ChatLog.add_subagent_marker ───────────────────────────────────────────────


class TestAddSubagentMarker:
    def test_adds_to_dict(self, log_no_async):
        log_no_async.add_subagent_marker("toolu_abc", "extract schema")
        assert "toolu_abc" in log_no_async._subagent_markers
        marker = log_no_async._subagent_markers["toolu_abc"]
        assert isinstance(marker, SubagentMarker)
        assert marker._description == "extract schema"

    def test_flushes_stream(self, log_no_async):
        log_no_async._force_flush_stream_buffer = MagicMock()
        log_no_async._finalize_streaming = MagicMock()
        log_no_async.add_subagent_marker("id1", "task")
        log_no_async._force_flush_stream_buffer.assert_called_once()
        log_no_async._finalize_streaming.assert_called_once()

    def test_mounts_marker(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "task")
        log_no_async._create_task_mock.assert_called()

    def test_overwrites_same_id(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "first")
        log_no_async.add_subagent_marker("id1", "second")
        assert len(log_no_async._subagent_markers) == 1
        assert log_no_async._subagent_markers["id1"]._description == "second"


# ── ChatLog.complete_subagent_marker ──────────────────────────────────────────


class TestCompleteSubagentMarker:
    def test_done_updates_text_and_css(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "extract")
        marker = log_no_async._subagent_markers["id1"]
        with patch.object(marker, "update") as mock_update:
            log_no_async.complete_subagent_marker("id1", 3.2, "done")
            mock_update.assert_called_once()
            text = mock_update.call_args[0][0]
            assert "done" in text
            assert "3.2s" in text
        assert marker.has_class("marker-done")
        assert not marker.has_class("marker-error")

    def test_error_updates_text_and_css(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "extract")
        marker = log_no_async._subagent_markers["id1"]
        with patch.object(marker, "update") as mock_update:
            log_no_async.complete_subagent_marker("id1", 2.1, "error")
            mock_update.assert_called_once()
            text = mock_update.call_args[0][0]
            assert "error" in text
            assert "2.1s" in text
            assert "⊗" in text
        assert marker.has_class("marker-error")

    def test_unknown_id_is_noop(self, log_no_async):
        log_no_async.complete_subagent_marker("nonexistent", 1.0, "done")
        assert "nonexistent" not in log_no_async._subagent_markers

    def test_long_elapsed_shows_seconds(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "task")
        marker = log_no_async._subagent_markers["id1"]
        with patch.object(marker, "update") as mock_update:
            log_no_async.complete_subagent_marker("id1", 125.0, "done")
            text = mock_update.call_args[0][0]
            assert "125s" in text


# ── ChatLog.emit_todo_note ────────────────────────────────────────────────────


class TestEmitTodoNote:
    def test_first_emit_stores_summary(self, log_no_async):
        log_no_async.emit_todo_note("1 done, 2 active, 0 pending")
        assert log_no_async._last_todo_summary == "1 done, 2 active, 0 pending"

    def test_same_state_dedup(self, log_no_async):
        log_no_async.emit_todo_note("1 done, 2 active, 0 pending")
        log_no_async.emit_todo_note("1 done, 2 active, 0 pending")
        assert log_no_async._last_todo_summary == "1 done, 2 active, 0 pending"

    def test_different_state_updates(self, log_no_async):
        log_no_async.emit_todo_note("1 done, 2 active, 0 pending")
        log_no_async.emit_todo_note("2 done, 1 active, 0 pending")
        assert log_no_async._last_todo_summary == "2 done, 1 active, 0 pending"


# ── ChatLog.clear_content ─────────────────────────────────────────────────────


class TestClearContentClearsSubagentMarkers:
    def test_clear_resets_markers(self, log_no_async):
        log_no_async.add_subagent_marker("id1", "task1")
        log_no_async.add_subagent_marker("id2", "task2")
        assert len(log_no_async._subagent_markers) == 2
        log_no_async._create_task_mock.reset_mock()
        import asyncio

        asyncio.get_event_loop().run_until_complete(log_no_async.clear_content())
        assert len(log_no_async._subagent_markers) == 0
        assert log_no_async._last_todo_summary == ""


# ── Regression: HIGH bug fix (review pass) ────────────────────────────────────


class TestTimelinePersistsAcrossUserTurns:
    """Regression for the bug found during review:
    ChatLog.append_user_message must NOT reset _subagent_markers or
    _last_todo_summary — those are timeline state that persists across
    user turns (mirroring _tool_markers). Only clear_content() (the
    /clear slash command) should reset them.
    """

    def test_subagent_markers_persist_across_append_user_message(self, log_no_async):
        import asyncio

        log_no_async.add_subagent_marker("id1", "extract schema")
        log_no_async.add_subagent_marker("id2", "research topic")
        assert len(log_no_async._subagent_markers) == 2

        with patch.object(log_no_async, "mount", new=AsyncMock()):
            asyncio.run(log_no_async.append_user_message("follow-up question"))

        assert "id1" in log_no_async._subagent_markers, (
            "subagent marker 'id1' must persist across user turns"
        )
        assert "id2" in log_no_async._subagent_markers, (
            "subagent marker 'id2' must persist across user turns"
        )

    def test_last_todo_summary_persists_across_append_user_message(self, log_no_async):
        import asyncio

        log_no_async.emit_todo_note("1 done, 2 active, 0 pending")
        assert log_no_async._last_todo_summary == "1 done, 2 active, 0 pending"

        with patch.object(log_no_async, "mount", new=AsyncMock()):
            asyncio.run(log_no_async.append_user_message("next turn"))

        assert log_no_async._last_todo_summary == "1 done, 2 active, 0 pending", (
            "_last_todo_summary must persist across user turns — only"
            " clear_content() should reset it (used for dedup)"
        )
