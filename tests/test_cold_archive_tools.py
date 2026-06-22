"""Tests for cold_archive + cold_load tool wrappers (loom.agent.tools)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_WORKDIR", str(tmp_path))
    return tmp_path


def _set_workdir(monkeypatch, p: Path) -> None:
    monkeypatch.setattr("loom.agent.tools.WORKDIR", p)


def test_run_cold_archive_writes_to_minicode(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    turns_json = json.dumps([{"role": "user", "content": f"t{i}"} for i in range(20)])
    out = tools.run_cold_archive(turns_json=turns_json)
    assert "archived 20 turns" in out
    assert (tmp_path / ".minicode" / "cold-storage" / "manifest.json").exists()


def test_run_cold_archive_rejects_non_json(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    out = tools.run_cold_archive(turns_json="not json")
    assert "Error" in out


def test_run_cold_archive_rejects_non_array(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    out = tools.run_cold_archive(turns_json=json.dumps({"a": 1}))
    assert "Error" in out and "array" in out


def test_run_cold_load_returns_archived_turns(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    turns = [{"role": "user", "content": f"t{i}"} for i in range(10)]
    tools.run_cold_archive(turns_json=json.dumps(turns))
    out = tools.run_cold_load(start_turn=3, end_turn=7)
    loaded = json.loads(out)
    assert len(loaded) == 4
    assert loaded[0]["content"] == "t3"
    assert loaded[-1]["content"] == "t6"


def test_run_cold_load_returns_error_when_no_archive(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    out = tools.run_cold_load(start_turn=0, end_turn=5)
    assert "Error" in out


def test_run_cold_load_returns_error_on_bad_range(monkeypatch, tmp_path):
    from loom.agent import tools
    _set_workdir(monkeypatch, tmp_path)
    turns = [{"role": "user", "content": f"t{i}"} for i in range(5)]
    tools.run_cold_archive(turns_json=json.dumps(turns))
    out = tools.run_cold_load(start_turn=3, end_turn=3)
    assert "Error" in out


def test_cold_archive_and_load_registered_in_tool_registry():
    from loom.agent.tools import TOOL_REGISTRY
    assert "cold_archive" in TOOL_REGISTRY.names()
    assert "cold_load" in TOOL_REGISTRY.names()