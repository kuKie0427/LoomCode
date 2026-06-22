"""Tests for f-session-mutable-prompt-p1.

Verifies the system prompt is rebuilt when MEMORY.md / AGENTS.md
change mtime, and on explicit invalidation from memory_write /
load_skill.
"""

from __future__ import annotations

from unittest.mock import patch

from loom.agent.system_prompt import (
    build_fresh,
    get_system_prompt,
    invalidate_system_prompt,
    mark_dirty,
)


def test_invalidate_clears_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("loom.agent.system_prompt._read_mtimes", lambda wd: {"a": 0.0, "b": 0.0, "c": 0.0})
    first = get_system_prompt(tmp_path)
    assert "version 1" not in first
    with patch("loom.agent.system_prompt.build_fresh", return_value="--- version 2 ---") as m:
        invalidate_system_prompt()
        second = get_system_prompt(tmp_path)
        assert m.call_count == 1
        assert second == "--- version 2 ---"


def test_cache_returns_same_string_when_mtimes_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr("loom.agent.system_prompt._read_mtimes", lambda wd: {"a": 0.0, "b": 0.0, "c": 0.0})
    invalidate_system_prompt()
    first = get_system_prompt(tmp_path)
    with patch("loom.agent.system_prompt.build_fresh", return_value="DIFFERENT") as m:
        second = get_system_prompt(tmp_path)
        assert first == second
        assert m.call_count == 0


def test_cache_rebuilds_on_mtime_change(tmp_path, monkeypatch):
    mtimes = {"a": 1.0, "b": 2.0, "c": 3.0}

    def fake_mtimes(wd):
        return dict(mtimes)

    monkeypatch.setattr("loom.agent.system_prompt._read_mtimes", fake_mtimes)
    invalidate_system_prompt()
    get_system_prompt(tmp_path)
    mtimes["b"] = 2.5
    with patch("loom.agent.system_prompt.build_fresh", return_value="REBUILT") as m:
        third = get_system_prompt(tmp_path)
        assert m.call_count == 1
        assert third == "REBUILT"


def test_mark_dirty_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("loom.agent.system_prompt._read_mtimes", lambda wd: {"a": 0.0, "b": 0.0, "c": 0.0})
    invalidate_system_prompt()
    get_system_prompt(tmp_path)
    mark_dirty("test reason")
    with patch("loom.agent.system_prompt.build_fresh", return_value="AFTER DIRTY") as m:
        out = get_system_prompt(tmp_path)
        assert m.call_count == 1
        assert out == "AFTER DIRTY"


def test_invalidate_accepts_none_reason(tmp_path):
    invalidate_system_prompt()
    invalidate_system_prompt(None)
    get_system_prompt(tmp_path)
    invalidate_system_prompt()
    with patch("loom.agent.system_prompt.build_fresh", return_value="x") as m:
        get_system_prompt(tmp_path)
        assert m.call_count == 1


def test_invalidate_clears_all_workdirs(tmp_path, monkeypatch):
    monkeypatch.setattr("loom.agent.system_prompt._read_mtimes", lambda wd: {"a": 0.0, "b": 0.0, "c": 0.0})
    wd1 = tmp_path / "a"
    wd2 = tmp_path / "b"
    wd1.mkdir()
    wd2.mkdir()
    invalidate_system_prompt()
    get_system_prompt(wd1)
    get_system_prompt(wd2)
    invalidate_system_prompt()
    with patch("loom.agent.system_prompt.build_fresh", return_value="REBUILT") as m:
        get_system_prompt(wd1)
        get_system_prompt(wd2)
        assert m.call_count == 2


def test_build_fresh_includes_agents_md(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("# Test Rules\nAlways say please.\n")
    monkeypatch.setattr("loom.agent.prompt.SUB_TOOLS", [], raising=False)
    prompt = build_fresh(tmp_path)
    assert "Test Rules" in prompt
    assert "Always say please" in prompt


def test_build_fresh_runs_in_isolated_tempdir(tmp_path):
    """End-to-end: build_fresh against a real workdir without mocking
    any layer, asserting the produced string contains the expected
    role + workdir sections."""
    prompt = build_fresh(tmp_path)
    assert "LoomCode" in prompt
    assert str(tmp_path) in prompt


def test_memory_write_invalidates_prompt_cache(tmp_path, monkeypatch):
    from loom.agent import tools as tools_mod
    memory_dir = tmp_path / ".minicode" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("seed")
    monkeypatch.setattr(tools_mod, "WORKDIR", tmp_path)
    invalidate_system_prompt()
    get_system_prompt(tmp_path)
    with patch("loom.agent.system_prompt.build_fresh", return_value="AFTER WRITE") as m:
        result = tools_mod.run_memory_write("new entry")
        assert "new entry" in result or "chars" in result
        after = get_system_prompt(tmp_path)
        assert m.call_count == 1
        assert after == "AFTER WRITE"


def test_load_skill_invalidates_prompt_cache(tmp_path, monkeypatch):
    from loom.agent import tools as tools_mod
    skills_dir = tmp_path / ".minicode" / "skills" / "demo"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# demo\n\n## Triggers\n- test\n\n## Steps\n1. do it\n")
    monkeypatch.setattr(tools_mod, "WORKDIR", tmp_path)
    invalidate_system_prompt()
    get_system_prompt(tmp_path)
    with patch("loom.agent.system_prompt.build_fresh", return_value="AFTER LOAD") as m:
        body = tools_mod.run_load_skill("demo")
        assert "do it" in body
        after = get_system_prompt(tmp_path)
        assert m.call_count == 1
        assert after == "AFTER LOAD"
