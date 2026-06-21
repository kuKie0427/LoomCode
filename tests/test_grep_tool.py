"""Tests for the native grep tool (f-grep-tool-p0).

Covers the public contract: structured output format, workspace boundary,
ripgrep / python-fallback parity, glob filter, case insensitivity, truncation,
empty/missing patterns. No LLM cost — these are unit tests.
"""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

import loom.agent.tools as main


def test_grep_finds_simple_match(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("def hello():\n    return 42\n")
    out = main.run_grep("def hello")
    assert "a.py:1:def hello():" in out


def test_grep_returns_no_matches_for_absent_pattern(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("hello\n")
    assert main.run_grep("nothere") == "(no matches)"


def test_grep_rejects_empty_pattern(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    assert "Error" in main.run_grep("")


def test_grep_rejects_path_escape(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    out = main.run_grep("anything", path="../etc")
    assert "Error" in out


def test_grep_rejects_nonexistent_path(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    out = main.run_grep("anything", path="no_such_dir")
    assert "Error" in out


def test_grep_glob_filter_restricts_to_matching_files(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("needle\n")
    (temp_workdir / "b.txt").write_text("needle\n")
    out = main.run_grep("needle", glob="*.py")
    assert "a.py" in out
    assert "b.txt" not in out


def test_grep_case_insensitive(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("CamelCase = 1\n")
    assert "CamelCase" in main.run_grep("camelcase", case_insensitive=True)
    assert "CamelCase" not in main.run_grep("camelcase", case_insensitive=False)


def test_grep_truncates_long_lines(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    long_line = "x" * 1000
    (temp_workdir / "a.py").write_text(f"{long_line}\n")
    out = main.run_grep("xxxxxx")
    # Content should be truncated to ~200 chars + "..." suffix
    assert "..." in out
    assert "x" * 1000 not in out


def test_grep_caps_total_matches_with_footer(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    body = "match_me\n" * (main.GREP_MAX_MATCHES + 50)
    (temp_workdir / "a.py").write_text(body)
    with patch.object(main, "_grep_ripgrep", return_value=None):
        out = main.run_grep("match_me")
    assert f"[...50 more matches truncated at limit {main.GREP_MAX_MATCHES}]" in out


def test_grep_skips_dotfiles_and_dotdirs(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / ".hidden").write_text("needle\n")
    (temp_workdir / "visible.py").write_text("needle\n")
    out = main.run_grep("needle")
    assert "visible.py" in out
    assert ".hidden" not in out


def test_grep_skips_oversized_files(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "big.py").write_text("needle")
    with patch.object(main, "GREP_LARGE_FILE_BYTES", 1), \
         patch.object(main, "_grep_ripgrep", return_value=None):
        assert main.run_grep("needle") == "(no matches)"


def test_grep_uses_ripgrep_when_available(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("hello\n")
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    out = main.run_grep("hello")
    assert "a.py:1:" in out
    assert "hello" in out


def test_grep_falls_back_to_python_when_ripgrep_unavailable(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("hello\n")
    with patch.object(main, "_grep_ripgrep", return_value=None):
        out = main.run_grep("hello")
    assert "a.py:1:hello" in out


def test_grep_relative_paths_in_output(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    sub = temp_workdir / "src"
    sub.mkdir()
    (sub / "mod.py").write_text("x = 1\n")
    out = main.run_grep("x = 1")
    assert "src/mod.py" in out
    # Should not contain absolute path
    assert str(temp_workdir) not in out


def test_grep_works_across_multiple_files(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("foo\n")
    (temp_workdir / "b.py").write_text("foo bar\n")
    (temp_workdir / "c.py").write_text("bar\n")
    out = main.run_grep("foo")
    assert "a.py:1:foo" in out
    assert "b.py:1:foo bar" in out
    assert "c.py" not in out


def test_grep_invalid_regex_returns_no_matches(monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    (temp_workdir / "a.py").write_text("hello\n")
    assert main.run_grep("[invalid(") == "(no matches)"


def test_grep_registered_in_tool_registry():
    from loom.agent.tools import TOOL_REGISTRY
    assert "grep" in TOOL_REGISTRY.names()
    tool = TOOL_REGISTRY.get("grep")
    assert tool is not None
    assert tool.is_read_only is True
    assert tool.is_concurrent_safe is True
    assert "pattern" in tool.input_schema["required"]


def test_grep_in_subagent_tools():
    from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS
    sub_names = {t["name"] for t in SUB_TOOLS}
    assert "grep" in sub_names
    assert "grep" in SUB_HANDLERS
