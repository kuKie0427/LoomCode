"""Unit tests for chat_log.py P1 helpers.

Covers:
- StreamingOverlay.update_content: passes text through _normalize_for_stream
- ChatLog.append_streaming_text: creates overlay on first call (not body)
- ChatLog._flush_stream_buffer: writes to overlay.update_content
- ChatLog._force_flush_stream_buffer: writes to overlay + stops timer
- ChatLog._finalize_streaming: replaces overlay with AssistantMessage
- ChatLog._finalize_streaming: no-op when no overlay
- ChatLog.add_tool_call_inline: calls _finalize_streaming first
- Multiple streaming segments: each gets own overlay then gets finalized
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from loop.tui.chat_log import (
    ChatLog,
    StreamingOverlay,
    ToolCallMarker,
    _normalize_for_stream,
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
    with (
        patch("loop.tui.chat_log.asyncio.create_task") as create_task,
        patch.object(ChatLog, "set_interval") as set_interval,
        patch.object(StreamingOverlay, "update_content") as update_content,
    ):
        timer_mock = MagicMock()
        set_interval.return_value = timer_mock
        chat._create_task_mock = create_task
        chat._set_interval_mock = set_interval
        chat._timer_mock = timer_mock
        chat._update_content_mock = update_content
        yield chat


class TestStreamingOverlay:
    def test_inherits_from_markdown(self):
        from textual.widgets import Markdown

        assert issubclass(StreamingOverlay, Markdown)

    def test_update_content_calls_update_with_normalized_text(self):
        overlay = StreamingOverlay()
        overlay.update = MagicMock()
        overlay.update_content("hello world")
        overlay.update.assert_called_once_with(_normalize_for_stream("hello world"))

    def test_update_content_normalizes_newlines(self):
        overlay = StreamingOverlay()
        overlay.update = MagicMock()
        overlay.update_content("a\nb\nc")
        overlay.update.assert_called_once_with(_normalize_for_stream("a\nb\nc"))


class TestAppendStreamingText:
    def test_first_call_creates_overlay(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        assert isinstance(log_no_async._current_overlay, StreamingOverlay)
        assert log_no_async._current_overlay is not None

    def test_first_call_does_not_create_body(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        assert log_no_async._current_body is None

    def test_subsequent_calls_keep_same_overlay(self, log_no_async):
        log_no_async.append_streaming_text("hello ")
        first = log_no_async._current_overlay
        log_no_async.append_streaming_text("world")
        assert log_no_async._current_overlay is first

    def test_subsequent_calls_accumulate_text(self, log_no_async):
        log_no_async.append_streaming_text("hello ")
        log_no_async.append_streaming_text("world")
        assert log_no_async._stream_full_text == "hello world"

    def test_creates_mount_task(self, log_no_async):
        log_no_async._create_task_mock.reset_mock()
        log_no_async.append_streaming_text("hi")
        assert log_no_async._create_task_mock.call_count == 1
        called_arg = log_no_async._create_task_mock.call_args[0][0]
        assert hasattr(called_arg, "__await__") or hasattr(called_arg, "send")

    def test_sets_flush_timer(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        log_no_async._set_interval_mock.assert_called_once()
        assert log_no_async._stream_flush_timer is log_no_async._timer_mock


class TestFlushStreamBuffer:
    def test_flush_writes_to_overlay(self, log_no_async):
        log_no_async.append_streaming_text("hello world")
        overlay = log_no_async._current_overlay
        overlay.update_content = MagicMock()
        log_no_async._stream_full_text = "hello world"
        log_no_async._flush_stream_buffer()
        overlay.update_content.assert_called_once_with("hello world")

    def test_flush_noop_when_no_text(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        overlay = log_no_async._current_overlay
        overlay.update_content = MagicMock()
        log_no_async._stream_full_text = ""
        log_no_async._flush_stream_buffer()
        overlay.update_content.assert_not_called()

    def test_flush_noop_when_no_overlay(self, log_no_async):
        log_no_async._stream_full_text = "orphan"
        log_no_async._current_overlay = None
        log_no_async._flush_stream_buffer()


class TestForceFlushStreamBuffer:
    def test_force_flush_writes_to_overlay(self, log_no_async):
        log_no_async.append_streaming_text("hello")
        overlay = log_no_async._current_overlay
        overlay.update_content = MagicMock()
        log_no_async._stream_full_text = "hello world"
        log_no_async._force_flush_stream_buffer()
        overlay.update_content.assert_called_once_with("hello world")

    def test_force_flush_stops_timer(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        log_no_async._timer_mock.stop.reset_mock()
        log_no_async._force_flush_stream_buffer()
        log_no_async._timer_mock.stop.assert_called_once()
        assert log_no_async._stream_flush_timer is None

    def test_force_flush_noop_when_no_overlay(self, log_no_async):
        log_no_async._current_overlay = None
        log_no_async._stream_full_text = "orphan"
        log_no_async._force_flush_stream_buffer()


class TestFinalizeStreaming:
    def test_finalize_noop_when_no_overlay(self, log_no_async):
        log_no_async._current_overlay = None
        log_no_async._stream_full_text = "stale"
        log_no_async._create_task_mock.reset_mock()
        log_no_async._finalize_streaming()
        assert log_no_async._current_overlay is None
        assert log_no_async._stream_full_text == "stale"
        log_no_async._create_task_mock.assert_not_called()

    def test_finalize_clears_overlay_and_text(self, log_no_async):
        log_no_async.append_streaming_text("hello world")
        log_no_async._current_overlay.update_content = MagicMock()
        log_no_async._finalize_streaming()
        assert log_no_async._current_overlay is None
        assert log_no_async._stream_full_text == ""

    def test_finalize_schedules_mount_task(self, log_no_async):
        log_no_async.append_streaming_text("hello world")
        log_no_async._current_overlay.update_content = MagicMock()
        log_no_async._create_task_mock.reset_mock()
        log_no_async._finalize_streaming()
        assert log_no_async._create_task_mock.call_count == 1

    def test_finalize_stops_flush_timer(self, log_no_async):
        log_no_async.append_streaming_text("hi")
        log_no_async._timer_mock.stop.reset_mock()
        log_no_async._finalize_streaming()
        log_no_async._timer_mock.stop.assert_called_once()
        assert log_no_async._stream_flush_timer is None

    def test_finalize_preserves_text_for_final_message(self, log_no_async):
        log_no_async._stream_full_text = "captured text"
        log_no_async._current_overlay = StreamingOverlay()
        log_no_async._current_overlay.update_content = MagicMock()
        log_no_async._create_task_mock.reset_mock()
        log_no_async._finalize_streaming()
        assert log_no_async._stream_full_text == ""
        assert log_no_async._create_task_mock.call_count == 1


class TestAddToolCallInline:
    def test_add_tool_call_triggers_finalize(self, log_no_async):
        log_no_async.append_streaming_text("streaming text")
        log_no_async._current_overlay.update_content = MagicMock()
        log_no_async._create_task_mock.reset_mock()
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        assert log_no_async._current_overlay is None
        assert log_no_async._stream_full_text == ""

    def test_add_tool_call_creates_marker(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        assert "tool_1" in log_no_async._tool_markers
        assert isinstance(log_no_async._tool_markers["tool_1"], ToolCallMarker)

    def test_add_tool_call_clears_stream(self, log_no_async):
        log_no_async.append_streaming_text("text")
        log_no_async.add_tool_call_inline("run_bash", {}, "tool_1")
        assert log_no_async._stream is None

    def test_add_tool_call_with_no_stream_yet(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"k": "v"}, "tool_x")
        assert "tool_x" in log_no_async._tool_markers


class TestMultipleStreamingSegments:
    def test_two_segments_each_get_own_overlay(self, log_no_async):
        log_no_async.append_streaming_text("first segment")
        first_overlay = log_no_async._current_overlay

        log_no_async._finalize_streaming()
        assert log_no_async._current_overlay is None

        log_no_async.append_streaming_text("second segment")
        assert isinstance(log_no_async._current_overlay, StreamingOverlay)
        assert log_no_async._current_overlay is not first_overlay

    def test_finalize_then_stream_accumulates_fresh(self, log_no_async):
        log_no_async.append_streaming_text("first")
        log_no_async._current_overlay.update_content = MagicMock()
        log_no_async._finalize_streaming()
        assert log_no_async._stream_full_text == ""

        log_no_async.append_streaming_text("second ")
        log_no_async.append_streaming_text("third")
        assert log_no_async._stream_full_text == "second third"

    def test_three_segments_three_overlays(self, log_no_async):
        seen = []
        for text in ["a", "b", "c"]:
            log_no_async.append_streaming_text(text)
            seen.append(log_no_async._current_overlay)
            log_no_async._current_overlay.update_content = MagicMock()
            log_no_async._finalize_streaming()

        assert seen[0] is not seen[1]
        assert seen[1] is not seen[2]
        assert seen[0] is not seen[2]
        assert all(isinstance(o, StreamingOverlay) for o in seen)
        assert log_no_async._current_overlay is None


class TestClearContent:
    def test_clear_removes_overlay_state(self, log_no_async):
        import asyncio

        log_no_async.append_streaming_text("text")

        with patch.object(log_no_async, "remove", new=AsyncMock()):
            asyncio.run(log_no_async.clear_content())

        assert log_no_async._current_overlay is None
        assert log_no_async._current_body is None
