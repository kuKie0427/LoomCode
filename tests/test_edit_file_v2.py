"""Tests for f-edit-file-v2-p0.

Covers run_edit (extended with multi-match error + fuzzy fallback),
run_multi_edit (atomic multi-edit with all-or-nothing semantics),
and run_edit_lines (1-indexed line-range replacement). All tests
are mock-only, no LLM cost.
"""

from __future__ import annotations

import loom.agent.tools as main


def test_run_edit_exact_unique_match_succeeds(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.txt").write_text("hello world\n")
    out = main.run_edit("a.txt", "hello", "goodbye")
    assert "Edited" in out
    assert (temp_workdir / "a.txt").read_text() == "goodbye world\n"


def test_run_edit_returns_error_on_multiple_matches(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.txt").write_text("foo bar foo bar foo")
    out = main.run_edit("a.txt", "foo", "X")
    assert "Error" in out
    assert "multiple_matches" in out
    assert (temp_workdir / "a.txt").read_text() == "foo bar foo bar foo"


def test_run_edit_short_old_text_no_fuzzy_fallback(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.txt").write_text("hello world")
    out = main.run_edit("a.txt", "hellox", "goodbye")
    assert "Error" in out
    assert "not_found" in out


def test_run_edit_fuzzy_match_applies_on_very_close_text(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    snippet = "def calculate_total(items: list[float]) -> float:" + " " * 60 + "return sum(items)\n"
    (temp_workdir / "a.py").write_text(snippet)
    fuzzy_old = "def calculate_total(items: List[float]) -> float:" + " " * 60 + "return sum(items)\n"
    out = main.run_edit("a.py", fuzzy_old, "# replaced\n")
    assert "Edited" in out
    assert "fuzzy" in out
    assert (temp_workdir / "a.py").read_text().startswith("# replaced\n")


def test_run_edit_fuzzy_match_fails_below_ratio(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    long_text = "the quick brown fox jumps over the lazy dog " * 3
    (temp_workdir / "a.txt").write_text(long_text)
    completely_different = "a" * 60
    out = main.run_edit("a.txt", completely_different, "X")
    assert "Error" in out
    assert "not_found" in out


def test_run_edit_returns_unified_diff_in_output(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.txt").write_text("line1\nline2\nline3\n")
    out = main.run_edit("a.txt", "line2", "LINE_TWO")
    assert "--- diff ---" in out
    assert "-line2" in out
    assert "+LINE_TWO" in out


def test_run_edit_path_escape_rejected(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    out = main.run_edit("../escape.txt", "x", "y")
    assert "Error" in out
    assert "escapes workspace" in out


def test_run_edit_empty_old_text_rejected(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.txt").write_text("anything")
    out = main.run_edit("a.txt", "", "x")
    assert "Error" in out
    assert "empty" in out


def test_run_multi_edit_applies_all_in_order(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a = 1\nb = 2\nc = 3\n")
    out = main.run_multi_edit("a.py", [
        {"old_text": "a = 1", "new_text": "A = 10"},
        {"old_text": "c = 3", "new_text": "C = 30"},
    ])
    assert "Multi-edited" in out
    assert "2 edits applied" in out
    content = (temp_workdir / "a.py").read_text()
    assert content == "A = 10\nb = 2\nC = 30\n"


def test_run_multi_edit_atomic_on_failure(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a = 1\nb = 2\n")
    out = main.run_multi_edit("a.py", [
        {"old_text": "a = 1", "new_text": "A = 10"},
        {"old_text": "nonexistent", "new_text": "X = 99"},
    ])
    assert "Error" in out
    assert "edits[1]" in out
    assert "not_found" in out
    assert (temp_workdir / "a.py").read_text() == "a = 1\nb = 2\n"


def test_run_multi_edit_rejects_empty_list(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("x")
    out = main.run_multi_edit("a.py", [])
    assert "Error" in out
    assert "non-empty" in out


def test_run_multi_edit_rejects_missing_field(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("x")
    out = main.run_multi_edit("a.py", [{"old_text": "x"}])
    assert "Error" in out
    assert "new_text" in out


def test_run_edit_lines_replaces_range(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("line1\nline2\nline3\nline4\nline5\n")
    out = main.run_edit_lines("a.py", 2, 3, "REPLACED")
    assert "Replaced lines 2..3" in out
    content = (temp_workdir / "a.py").read_text()
    assert content == "line1\nREPLACED\nline4\nline5\n"


def test_run_edit_lines_inclusive_end_equals_start(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\nc\nd\n")
    main.run_edit_lines("a.py", 2, 2, "ONLY_TWO")
    content = (temp_workdir / "a.py").read_text()
    assert content == "a\nONLY_TWO\nc\nd\n"


def test_run_edit_lines_rejects_zero_start(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\n")
    out = main.run_edit_lines("a.py", 0, 1, "X")
    assert "Error" in out
    assert "invalid line range" in out


def test_run_edit_lines_rejects_start_beyond_eof(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\n")
    out = main.run_edit_lines("a.py", 99, 100, "X")
    assert "Error" in out
    assert "start_line" in out
    assert "total" in out


def test_run_edit_lines_caps_end_at_eof(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\nc\n")
    out = main.run_edit_lines("a.py", 1, 99, "ALL")
    assert "Replaced" in out
    assert (temp_workdir / "a.py").read_text() == "ALL\n"


def test_run_edit_lines_appends_newline_to_content(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\nc\n")
    main.run_edit_lines("a.py", 2, 2, "REPLACED")
    content = (temp_workdir / "a.py").read_text()
    assert content == "a\nREPLACED\nc\n"


def test_run_edit_lines_empty_new_content_deletes_line(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("a\nb\nc\n")
    main.run_edit_lines("a.py", 2, 2, "")
    content = (temp_workdir / "a.py").read_text()
    assert content == "a\nc\n"


def test_tools_registered_in_registry():
    from loom.agent.tools import TOOL_REGISTRY
    TOOL_REGISTRY.names()
    for tool in ("edit_file", "multi_edit", "edit_lines"):
        t = TOOL_REGISTRY.get(tool)
        assert t is not None, f"{tool} not registered"
        assert t.is_read_only is False, f"{tool} is not read-only but should be writable"


def test_tools_in_subagent_registry():
    from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS
    sub_names = {t["name"] for t in SUB_TOOLS}
    for tool in ("edit_file", "multi_edit", "edit_lines"):
        assert tool in sub_names
        assert tool in SUB_HANDLERS
