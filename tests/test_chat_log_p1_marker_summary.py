"""Unit tests for chat_log.py P1: ToolCallMarker argument summary.

Covers:
- `_summarize_tool_args` — all tool branches + defensive cases
- `_normalize_tool_name` — display-name mapping
- `_truncate_summary` — length cap + ellipsis
- `ToolCallMarker` init with ``tool_input`` produces summary in render text
- Backward compat: marker without ``tool_input`` still shows bare status
"""

from loom.tui.chat_log import (
    ToolCallMarker,
    _normalize_tool_name,
    _summarize_tool_args,
    _truncate_summary,
)

# ── _truncate_summary ─────────────────────────────────────────────────────────


class TestTruncateSummary:
    def test_short_text_passes_through(self):
        assert _truncate_summary("hello") == "hello"
        assert _truncate_summary("") == ""

    def test_exact_max_len(self):
        text = "a" * 50
        assert _truncate_summary(text) == text

    def test_longer_than_max_truncates_with_ellipsis(self):
        text = "a" * 55
        result = _truncate_summary(text)
        assert result == "a" * 50 + "\u2026"

    def test_strips_whitespace(self):
        assert _truncate_summary("  hello  ") == "hello"

    def test_strip_then_truncate(self):
        text = "  " + "a" * 55
        result = _truncate_summary(text)
        assert len(result) == 51
        assert result.endswith("\u2026")

    def test_short_with_whitespace_only(self):
        assert _truncate_summary("   ") == ""

    def test_unicode_truncation(self):
        text = "你好" * 30  # 60 chars
        result = _truncate_summary(text)
        assert len(result) == 51
        assert result.endswith("\u2026")


# ── _normalize_tool_name ──────────────────────────────────────────────────────


class TestNormalizeToolName:
    def test_read_file_becomes_read(self):
        assert _normalize_tool_name("read_file") == "read"

    def test_write_file_becomes_write(self):
        assert _normalize_tool_name("write_file") == "write"

    def test_edit_file_becomes_edit(self):
        assert _normalize_tool_name("edit_file") == "edit"

    def test_bash_passes_through(self):
        assert _normalize_tool_name("bash") == "bash"

    def test_glob_passes_through(self):
        assert _normalize_tool_name("glob") == "glob"

    def test_todo_write_passes_through(self):
        assert _normalize_tool_name("todo_write") == "todo_write"

    def test_unknown_passes_through(self):
        assert _normalize_tool_name("unknown_tool") == "unknown_tool"


# ── _summarize_tool_args ──────────────────────────────────────────────────────


class TestSummarizeToolArgs:
    def test_bash_returns_command(self):
        result = _summarize_tool_args("bash", {"command": "npm test"})
        assert result == "npm test"

    def test_bash_long_command_truncated(self):
        long_cmd = "x" * 60
        result = _summarize_tool_args("bash", {"command": long_cmd})
        assert result == "x" * 50 + "\u2026"

    def test_bash_missing_command(self):
        result = _summarize_tool_args("bash", {})
        assert result == ""

    def test_bash_command_not_string(self):
        result = _summarize_tool_args("bash", {"command": ["ls"]})
        assert result == ""

    def test_read_file_returns_filename(self):
        result = _summarize_tool_args("read_file", {"path": "/home/user/src/app.py"})
        assert result == "app.py"

    def test_read_file_long_filename_truncated(self):
        long_name = "a" * 60 + ".py"
        result = _summarize_tool_args("read_file", {"path": f"/tmp/{long_name}"})
        assert result == "a" * 50 + "\u2026"

    def test_read_file_missing_path(self):
        result = _summarize_tool_args("read_file", {})
        assert result == ""

    def test_read_file_path_not_string(self):
        result = _summarize_tool_args("read_file", {"path": None})
        assert result == ""

    def test_read_file_empty_path_string(self):
        result = _summarize_tool_args("read_file", {"path": ""})
        assert result == ""

    def test_write_file_returns_filename(self):
        result = _summarize_tool_args("write_file", {"path": "output.txt"})
        assert result == "output.txt"

    def test_write_file_nested_path(self):
        result = _summarize_tool_args("write_file", {"path": "src/output.txt"})
        # Path("src/output.txt").name → "output.txt"
        assert result == "output.txt"

    def test_edit_file_returns_filename(self):
        result = _summarize_tool_args("edit_file", {"path": "loom/tui/app.py"})
        assert result == "app.py"

    def test_glob_returns_pattern(self):
        result = _summarize_tool_args("glob", {"pattern": "**/*.py"})
        assert result == "**/*.py"

    def test_glob_long_pattern_truncated(self):
        long_pat = "x" * 60
        result = _summarize_tool_args("glob", {"pattern": long_pat})
        assert result == "x" * 50 + "\u2026"

    def test_glob_missing_pattern(self):
        result = _summarize_tool_args("glob", {})
        assert result == ""

    def test_glob_pattern_not_string(self):
        result = _summarize_tool_args("glob", {"pattern": 42})
        assert result == ""

    def test_todo_write_returns_task_count(self):
        result = _summarize_tool_args("todo_write", {"todos": [{"text": "a"}]})
        assert result == "1 tasks"

    def test_todo_write_multiple_tasks(self):
        result = _summarize_tool_args("todo_write", {"todos": [{"text": "a"}, {"text": "b"}, {"text": "c"}]})
        assert result == "3 tasks"

    def test_todo_write_empty_todos(self):
        result = _summarize_tool_args("todo_write", {"todos": []})
        assert result == "0 tasks"

    def test_todo_write_missing_todos(self):
        result = _summarize_tool_args("todo_write", {})
        assert result == ""

    def test_todo_write_todos_not_list(self):
        result = _summarize_tool_args("todo_write", {"todos": "not-a-list"})
        assert result == ""

    def test_unknown_tool_returns_empty(self):
        result = _summarize_tool_args("unknown_tool", {"some": "data"})
        assert result == ""

    def test_none_input_returns_empty(self):
        result = _summarize_tool_args("bash", None)  # type: ignore[arg-type]
        assert result == ""

    def test_non_dict_input_returns_empty(self):
        result = _summarize_tool_args("bash", "not-a-dict")  # type: ignore[arg-type]
        assert result == ""


# ── ToolCallMarker with tool_input ────────────────────────────────────────────


class TestToolCallMarkerSummary:
    def _render_str(self, marker: ToolCallMarker) -> str:
        """Return the plain-text render of a marker (Content → str)."""
        return str(marker.render())

    def test_marker_with_tool_input_shows_summary(self):
        marker = ToolCallMarker("bash", '{"command": "npm test"}', tool_input={"command": "npm test"})
        text = self._render_str(marker)
        assert "npm test" in text
        # Running state: summary replaces "running"
        assert "· npm test" in text

    def test_marker_with_tool_input_done_shows_summary(self):
        marker = ToolCallMarker("bash", '{"command": "ls -la"}', tool_input={"command": "ls -la"})
        marker.set_complete("output", is_error=False)
        text = self._render_str(marker)
        assert "· ls -la" in text
        assert "⊙" in text

    def test_marker_with_tool_input_error_shows_summary_but_error_glyph(self):
        marker = ToolCallMarker("bash", '{"command": "rm -rf /"}', tool_input={"command": "rm -rf /"})
        marker.set_complete("permission denied", is_error=True)
        # Error is distinguishable via ⊗ + tool-error class
        text = self._render_str(marker)
        assert "⊗" in text
        assert marker.has_class("tool-error")
        # Summary still appears (error is NOT conveyed by summary alone)
        assert "· rm -rf /" in text

    def test_marker_without_tool_input_backward_compat(self):
        marker = ToolCallMarker("bash", "{}")
        assert marker.render() == "⊙ bash · running"

    def test_marker_without_tool_input_done_backward_compat(self):
        marker = ToolCallMarker("bash", "{}")
        marker.set_complete("output", is_error=False)
        assert marker.render() == "⊙ bash · done"

    def test_marker_without_tool_input_error_backward_compat(self):
        marker = ToolCallMarker("bash", "{}")
        marker.set_complete("error", is_error=True)
        assert marker.render() == "⊗ bash · error"

    def test_read_file_marker_shows_filename_only(self):
        marker = ToolCallMarker(
            "read_file", '{"path": "src/app.py"}',
            tool_input={"path": "src/app.py"},
        )
        text = self._render_str(marker)
        assert "· app.py" in text
        assert "src/" not in text

    def test_normalized_name_in_marker(self):
        marker = ToolCallMarker(
            "read_file", '{"path": "app.py"}',
            tool_input={"path": "app.py"},
        )
        text = self._render_str(marker)
        # Tool name is "read" (normalized), not "read_file"
        assert "read · app.py" in text
        assert "read_file" not in text

    def test_complete_with_summary_still_has_correct_class(self):
        marker = ToolCallMarker(
            "glob", '{"pattern": "**/*.py"}',
            tool_input={"pattern": "**/*.py"},
        )
        marker.set_complete("found 42 files", is_error=False)
        assert marker.has_class("tool-done")
        assert not marker.has_class("tool-error")

    def test_empty_summary_falls_back_to_running(self):
        """When tool_input is provided but yields empty summary, 'running' is shown."""
        marker = ToolCallMarker("bash", "{}", tool_input={})
        text = self._render_str(marker)
        assert "· running" in text

    def test_empty_summary_done_falls_back_to_status(self):
        marker = ToolCallMarker("bash", "{}", tool_input={})
        marker.set_complete("output", is_error=False)
        text = self._render_str(marker)
        assert "· done" in text
