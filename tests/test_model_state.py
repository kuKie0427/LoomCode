"""Tests for loom/agent/model_state.py.

Verifies:
  - ModelRef: str, to_dict/from_dict roundtrip, frozen+hashable
  - ModelState: recent MRU add/dedup, max cap, default set/get, atomic write, dir chmod
  - ProjectConfig: local config read, upward walk, missing returns None, atomic write
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from loom.agent.model_state import ModelRef, ModelState, ProjectConfig


class TestModelRef:
    def test_str_returns_provider_model(self) -> None:
        ref = ModelRef("anthropic", "claude-sonnet-4-5")
        assert str(ref) == "anthropic/claude-sonnet-4-5"

    def test_to_dict_roundtrip(self) -> None:
        ref = ModelRef("openai", "gpt-4o")
        d = ref.to_dict()
        assert d == {"provider_id": "openai", "model_id": "gpt-4o"}
        assert ModelRef.from_dict(d) == ref

    def test_frozen_and_hashable(self) -> None:
        ref = ModelRef("a", "b")
        s = {ref}
        assert ref in s


class TestModelState:
    def test_recent_adds_and_dedups(self, tmp_path: Path) -> None:
        ms = ModelState(tmp_path)
        ms.add_recent("anthropic", "claude-sonnet-4-5")
        ms.add_recent("openai", "gpt-4o")
        ms.add_recent("anthropic", "claude-sonnet-4-5")  # bump
        recent = ms.recent()
        assert len(recent) == 2
        assert recent[0] == ModelRef("anthropic", "claude-sonnet-4-5")  # bumped to top
        assert recent[1] == ModelRef("openai", "gpt-4o")

    def test_recent_max_10_caps(self, tmp_path: Path) -> None:
        ms = ModelState(tmp_path)
        for i in range(15):
            ms.add_recent("p", f"m{i}")
        assert len(ms.recent()) == 10

    def test_default_set_and_get(self, tmp_path: Path) -> None:
        ms = ModelState(tmp_path)
        assert ms.default_model() is None
        ms.set_default("anthropic", "claude-sonnet-4-5")
        assert ms.default_model() == "anthropic/claude-sonnet-4-5"

    def test_atomic_write_chmod(self, tmp_path: Path) -> None:
        ms = ModelState(tmp_path)
        ms.add_recent("anthropic", "claude-sonnet-4-5")
        path = tmp_path / ".minicode" / "state" / "model.json"
        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_creates_directory_chmod_0o700(self, tmp_path: Path) -> None:
        ModelState(tmp_path)
        path = tmp_path / ".minicode" / "state"
        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o700


class TestProjectConfig:
    def test_reads_local_minicode_config(self, tmp_path: Path) -> None:
        (tmp_path / ".minicode").mkdir(exist_ok=True)
        (tmp_path / ".minicode" / "config.json").write_text(
            json.dumps({"model": "anthropic/claude-sonnet-4-5"})
        )
        pc = ProjectConfig(tmp_path)
        assert pc.model == "anthropic/claude-sonnet-4-5"

    def test_walks_upward_to_home(self, tmp_path: Path) -> None:
        (tmp_path / ".minicode").mkdir(exist_ok=True)
        (tmp_path / ".minicode" / "config.json").write_text(
            json.dumps({"model": "deepseek/deepseek-chat"})
        )
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        pc = ProjectConfig(sub)
        assert pc.model == "deepseek/deepseek-chat"

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        pc = ProjectConfig(tmp_path)
        assert pc.model is None

    def test_atomic_write(self, tmp_path: Path) -> None:
        pc = ProjectConfig(tmp_path)
        pc.model = "openai/gpt-4o"
        pc.save()
        path = tmp_path / ".minicode" / "config.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["model"] == "openai/gpt-4o"
