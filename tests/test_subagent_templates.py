"""Tests for f-subagent-grep-patterns-p2.

Verifies the 3 subagent task templates are defined, formatters
produce valid prompts, and the tools are registered in the
TOOL_REGISTRY.
"""

from __future__ import annotations

import pytest

from loom.agent.subagent_templates import (
    format_subagent_prompt,
    get_template,
    list_templates,
)


def test_all_three_templates_listed():
    names = list_templates()
    assert "investigate_code" in names
    assert "refactor_across_files" in names
    assert "fix_failing_test" in names


def test_get_template_returns_dict():
    tpl = get_template("investigate_code")
    assert tpl is not None
    assert "description" in tpl
    assert "system" in tpl
    assert "args_schema" in tpl


def test_get_template_unknown_returns_none():
    assert get_template("nonexistent") is None


def test_format_includes_template_name_and_args():
    out = format_subagent_prompt("investigate_code", question="where is login?")
    assert "investigate_code" in out
    assert "question: where is login?" in out
    assert "hint_paths" not in out


def test_format_unknown_template_raises_value_error():
    with pytest.raises(ValueError, match="unknown subagent template"):
        format_subagent_prompt("nope")


def test_investigate_code_system_emphasizes_grep():
    tpl = get_template("investigate_code")
    assert "grep" in tpl["system"]


def test_refactor_across_files_system_emphasizes_multi_edit():
    tpl = get_template("refactor_across_files")
    assert "multi_edit" in tpl["system"]


def test_fix_failing_test_system_emphasizes_minimal_change():
    tpl = get_template("fix_failing_test")
    assert "最小" in tpl["system"] or "minimal" in tpl["system"].lower()


def test_investigate_code_args_schema():
    tpl = get_template("investigate_code")
    assert "question" in tpl["args_schema"]


def test_refactor_across_files_args_schema():
    tpl = get_template("refactor_across_files")
    assert "pattern" in tpl["args_schema"]
    assert "scope_glob" in tpl["args_schema"]


def test_fix_failing_test_args_schema():
    tpl = get_template("fix_failing_test")
    assert "test_path" in tpl["args_schema"]


def test_all_systems_instruct_no_skip_or_xfail():
    """Safety: if the templates mention skip/xfail at all, it must
    be in a NEGATIVE context (do not skip/xfail as a shortcut).
    Tests reward-hacking prevention."""
    for name in list_templates():
        sys = get_template(name)["system"]
        if "skip" in sys.lower():
            assert "不要" in sys or "never" in sys.lower() or "do not" in sys.lower()
        if "xfail" in sys.lower():
            assert "不要" in sys or "never" in sys.lower() or "do not" in sys.lower()


def test_template_tools_registered():
    from loom.agent.tools import TOOL_REGISTRY
    for name in ("task_investigate_code", "task_refactor_across_files", "task_fix_failing_test"):
        tool = TOOL_REGISTRY.get(name)
        assert tool is not None, f"{name} not registered"
