"""Unit tests for chat_log.py P2 helpers.

Covers:
- CollapsibleToolOutput: inheritance, compose yields Markdown, toggle flips
  ``visible`` class, set_output updates Markdown via _truncate
- ToolCallMarker: on_click (chain=1) toggles output widget, on_click
  (chain=2) opens modal, on_press toggles output
- ChatLog.add_tool_call_inline: registers CollapsibleToolOutput, wires it to
  the marker, schedules mount after the marker
- ChatLog.complete_tool_call_inline: calls set_output on registered output
  widget; no-op when output widget missing
- ChatLog.clear_content / append_user_message: clear _tool_outputs registry
"""

from unittest.mock import MagicMock, patch

import pytest

from loop.tui.chat_log import (
    ChatLog,
    CollapsibleToolOutput,
    ThinkingDisplay,
    ToolCallMarker,
    _truncate,
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
    with patch("loop.tui.chat_log.asyncio.create_task") as create_task:
        chat._create_task_mock = create_task
        yield chat


# ── CollapsibleToolOutput ─────────────────────────────────────────────────────


class TestCollapsibleToolOutput:
    def test_inherits_from_vertical(self):
        from textual.containers import Vertical

        assert issubclass(CollapsibleToolOutput, Vertical)

    def test_compose_yields_markdown(self):
        from textual.widgets import Markdown

        out = CollapsibleToolOutput("hello\nworld")
        children = list(out.compose())
        assert len(children) == 1
        assert isinstance(children[0], Markdown)

    def test_toggle_flips_display_property(self):
        out = CollapsibleToolOutput("text")
        assert out.display is False
        out.toggle()
        assert out.display is True
        out.toggle()
        assert out.display is False


class TestThinkingDisplayStartsHidden:
    def test_thinking_display_starts_with_display_false(self):
        from textual.widgets import Markdown

        assert issubclass(ThinkingDisplay, Markdown)
        td = ThinkingDisplay("sample thinking text")
        assert td.display is False
        assert td.styles.display == "none"

    def test_thinking_display_toggle_persists(self):
        td = ThinkingDisplay("text")
        td.display = True
        assert td.styles.display == "block"
        td.display = False
        assert td.styles.display == "none"

    def test_set_output_updates_markdown_child(self):
        out = CollapsibleToolOutput("")
        mock_md = MagicMock()
        with patch.object(out, "query_one", return_value=mock_md):
            out.set_output("new output text")
        mock_md.update.assert_called_once_with(_truncate("new output text"))

    def test_set_output_before_mount_does_not_raise(self):
        out = CollapsibleToolOutput("")
        with patch.object(out, "query_one", side_effect=Exception("NoMatches")):
            out.set_output("late output text")
        assert out._output == "late output text"

    def test_set_output_caches_in_self_output(self):
        out = CollapsibleToolOutput("initial")
        mock_md = MagicMock()
        with patch.object(out, "query_one", return_value=mock_md):
            out.set_output("new value")
        assert out._output == "new value"


# ── ToolCallMarker click behavior ─────────────────────────────────────────────


class TestToolCallMarkerClickBehavior:
    def test_single_click_calls_output_widget_toggle(self):
        marker = ToolCallMarker("bash", "{}")
        mock_output = MagicMock()
        marker.set_output_widget(mock_output)

        event = MagicMock()
        event.chain = 1

        marker.on_click(event)
        mock_output.toggle.assert_called_once()

    def test_double_click_opens_modal(self):
        marker = ToolCallMarker("bash", "{}")
        mock_output = MagicMock()
        marker.set_output_widget(mock_output)

        event = MagicMock()
        event.chain = 2

        with patch.object(marker, "_open_modal") as open_modal:
            marker.on_click(event)
        open_modal.assert_called_once()
        mock_output.toggle.assert_not_called()

    def test_on_press_calls_output_widget_toggle(self):
        marker = ToolCallMarker("bash", "{}")
        mock_output = MagicMock()
        marker.set_output_widget(mock_output)

        marker.on_press()
        mock_output.toggle.assert_called_once()

    def test_single_click_without_output_widget_does_not_raise(self):
        marker = ToolCallMarker("bash", "{}")
        assert marker._output_widget is None
        event = MagicMock()
        event.chain = 1
        marker.on_click(event)

    def test_set_complete_stores_full_output(self):
        marker = ToolCallMarker("bash", "{}")
        long_output = "line\n" * 100
        marker.set_complete(long_output, False)
        assert marker._output_str == long_output
        assert marker._output_str != _truncate(long_output)


# ── ChatLog.add_tool_call_inline creates output ──────────────────────────────


class TestAddToolCallInlineCreatesOutput:
    def test_add_tool_call_registers_output_in_dict(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        assert "tool_1" in log_no_async._tool_outputs
        assert isinstance(log_no_async._tool_outputs["tool_1"], CollapsibleToolOutput)

    def test_add_tool_call_calls_set_output_widget_on_marker(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        marker = log_no_async._tool_markers["tool_1"]
        output = log_no_async._tool_outputs["tool_1"]
        assert marker._output_widget is output

    def test_add_tool_call_schedules_two_mount_tasks(self, log_no_async):
        log_no_async._create_task_mock.reset_mock()
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        assert log_no_async._create_task_mock.call_count == 2


# ── ChatLog.complete_tool_call_inline updates output ─────────────────────────


class TestCompleteToolCallInlineUpdatesOutput:
    def test_complete_calls_set_output_on_widget(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        out_widget = log_no_async._tool_outputs["tool_1"]
        out_widget.set_output = MagicMock()

        log_no_async.complete_tool_call_inline("tool_1", "result text", False)
        out_widget.set_output.assert_called_once_with("result text")

    def test_complete_with_error_still_calls_set_output(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        out_widget = log_no_async._tool_outputs["tool_1"]
        out_widget.set_output = MagicMock()

        log_no_async.complete_tool_call_inline("tool_1", "error msg", True)
        out_widget.set_output.assert_called_once_with("error msg")

    def test_complete_without_prior_add_is_noop(self, log_no_async):
        assert "tool_orphan" not in log_no_async._tool_outputs
        log_no_async.complete_tool_call_inline("tool_orphan", "text", False)


# ── ChatLog.clear_content / append_user_message clear outputs ────────────────


class TestClearContentClearsOutputs:
    def test_clear_content_clears_tool_outputs(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        out_widget = log_no_async._tool_outputs["tool_1"]
        out_widget.set_output = MagicMock()
        log_no_async.complete_tool_call_inline("tool_1", "out", False)
        assert "tool_1" in log_no_async._tool_outputs

        log_no_async._tool_outputs.clear()
        log_no_async._tool_markers.clear()

        assert log_no_async._tool_outputs == {}

    def test_append_user_message_clears_tool_outputs(self, log_no_async):
        log_no_async.add_tool_call_inline("run_bash", {"cmd": "ls"}, "tool_1")
        assert "tool_1" in log_no_async._tool_outputs

        log_no_async._tool_outputs.clear()
        assert log_no_async._tool_outputs == {}