"""Tests for f-tui-diff-viewer-p3 (minimal scope).

Verifies colorize_diff() applies the right colors to added/removed/hunk
lines, DiffPane renders the same content as a Static widget, and
empty diff shows the placeholder.
"""

from __future__ import annotations

from loom.tui.diff_pane import DiffPane, colorize_diff

DIFF_SAMPLE = (
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,3 +1,4 @@\n"
    " def add(a, b):\n"
    "     return a + b\n"
    "+# new comment\n"
    " def subtract(a, b):\n"
    "     return a - b\n"
)


def test_colorize_marks_added_lines_green():
    out = colorize_diff("+hello world")
    assert "[$success]" in out
    assert "+hello world" in out


def test_colorize_marks_removed_lines_red():
    out = colorize_diff("-old text")
    assert "[$error]" in out
    assert "-old text" in out


def test_colorize_marks_hunk_headers_yellow():
    out = colorize_diff("@@ -1,3 +1,4 @@")
    assert "[$warning]" in out
    assert "@@" in out


def test_colorize_leaves_context_unchanged():
    out = colorize_diff(" def add(a, b):")
    assert "def add(a, b):" in out
    assert "[" not in out.split("def")[0]


def test_colorize_handles_full_diff():
    out = colorize_diff(DIFF_SAMPLE)
    assert "[$success]" in out
    assert "[$error]" in out
    assert "[$warning]" in out
    ADDED_LINE = "+# new comment"
    CONTEXT_LINE = " def add(a, b):"
    assert ADDED_LINE in out
    assert CONTEXT_LINE in out
    assert "--- a/calc.py" in out
    assert "+++ b/calc.py" in out


def test_colorize_handles_empty_string():
    out = colorize_diff("")
    assert out == ""


def test_colorize_handles_unified_diff_metadata_lines():
    out = colorize_diff("--- a/file.py\n+++ b/file.py\n")
    assert "--- a/file.py" in out
    assert "+++ b/file.py" in out


def test_diff_pane_placeholder_when_empty():
    pane = DiffPane()
    assert pane.PLACEHOLDER in pane.render()


def test_diff_pane_renders_diff():
    pane = DiffPane()
    pane.diff_text = DIFF_SAMPLE
    out = pane.render()
    assert "[$success]" in out
    assert "[$error]" in out
    assert "def add" in out


def test_diff_pane_renders_empty_string_as_placeholder():
    pane = DiffPane()
    pane.diff_text = "   \n  \n"
    rendered = pane.render()
    assert pane.PLACEHOLDER in rendered


def test_diff_pane_uses_loom_ink_color_palette():
    pane = DiffPane()
    pane.diff_text = "+added\n-removed\n@@ hunk\ncontext\n"
    rendered = pane.render()
    assert "[$success]+added" in rendered
    assert "[$error]-removed" in rendered
    assert "[$warning]@@ hunk" in rendered


def test_diff_pane_reactive_watch_triggers_update():
    """When diff_text changes from initial empty, update() is called with
    the new content (at least once for the change, possibly more for the
    initial reactive mount — we just check the LAST call has the new content)."""
    pane = DiffPane()
    update_calls = []
    original_update = pane.update
    def fake_update(content):
        update_calls.append(content)
        original_update(content)
    pane.update = fake_update
    pane.diff_text = "+new line"
    assert len(update_calls) >= 1
    assert pane.diff_text in update_calls[-1]


def test_colorize_preserves_line_order():
    diff = "line1\n+added\nline3\n-removed\nline5\n"
    out = colorize_diff(diff)
    lines = out.split("\n")
    assert "line1" in lines[0]
    assert "added" in lines[1]
    assert "line3" in lines[2]
    assert "removed" in lines[3]
    assert "line5" in lines[4]
