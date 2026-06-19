from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from loom.agent.checkpoint import (
    CHECKPOINT_EVERY_TOKENS,
    CHECKPOINT_EVERY_TOOL_CALLS,
    default_path_for,
    exists,
    is_due,
    load,
    maybe_save,
    save,
)


class FakeContext:
    def __init__(self, last_input_tokens: int = 100, checked_at_index: int = 5):
        self.last_input_tokens = last_input_tokens
        self.checked_at_index = checked_at_index


class TestIsDue:
    def test_not_due_below_thresholds(self) -> None:
        assert not is_due(tool_call_count=5, new_tokens_since_checkpoint=2000)

    def test_due_at_tool_call_threshold(self) -> None:
        assert is_due(tool_call_count=CHECKPOINT_EVERY_TOOL_CALLS, new_tokens_since_checkpoint=0)

    def test_due_at_token_threshold(self) -> None:
        assert is_due(tool_call_count=0, new_tokens_since_checkpoint=CHECKPOINT_EVERY_TOKENS)

    def test_due_beyond_thresholds(self) -> None:
        assert is_due(tool_call_count=100, new_tokens_since_checkpoint=10000)


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "test-model"
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
        ]
        ctx = FakeContext(last_input_tokens=500, checked_at_index=2)

        path = save(tmp_path, messages, fake_llm, ctx, tool_call_count=12)
        assert path.exists()
        assert path == default_path_for(tmp_path)

        loaded = load(tmp_path)
        assert loaded is not None
        assert loaded["model"] == "test-model"
        assert loaded["messages"] == messages
        assert loaded["tool_call_count"] == 12
        assert loaded["last_input_tokens"] == 500
        assert loaded["checked_at_index"] == 2
        assert "saved_at" in loaded

    def test_load_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert load(tmp_path) is None

    def test_load_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        path = default_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json {{{", encoding="utf-8")
        assert load(tmp_path) is None

    def test_save_is_atomic_with_tmp_rename(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "test-model"
        save(tmp_path, [], fake_llm, FakeContext(), tool_call_count=0)
        assert not (default_path_for(tmp_path).with_suffix(".json.tmp")).exists()

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "first"
        save(tmp_path, [{"role": "user", "content": "1"}], fake_llm, FakeContext(), tool_call_count=1)

        fake_llm.model = "second"
        save(tmp_path, [{"role": "user", "content": "2"}], fake_llm, FakeContext(), tool_call_count=2)

        loaded = load(tmp_path)
        assert loaded is not None
        assert loaded["model"] == "second"
        assert loaded["tool_call_count"] == 2


class TestMaybeSave:
    def test_returns_none_when_not_due(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "x"
        assert maybe_save(tmp_path, [], fake_llm, FakeContext(), tool_call_count=2, new_tokens_since_checkpoint=100) is None

    def test_saves_when_due(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "x"
        result = maybe_save(tmp_path, [], fake_llm, FakeContext(), tool_call_count=20, new_tokens_since_checkpoint=100)
        assert result is not None
        assert result.exists()


class TestExists:
    def test_exists_false_initially(self, tmp_path: Path) -> None:
        assert not exists(tmp_path)

    def test_exists_true_after_save(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "x"
        save(tmp_path, [], fake_llm, FakeContext(), tool_call_count=0)
        assert exists(tmp_path)


class TestAtomicWriteContent:
    """Verify the JSON content matches what we'd expect."""

    def test_serialize_message_with_complex_content(self, tmp_path: Path) -> None:
        fake_llm = MagicMock()
        fake_llm.model = "x"
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "Running ls..."},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file1.txt\nfile2.txt"},
                ],
            },
        ]
        save(tmp_path, messages, fake_llm, FakeContext(), tool_call_count=1)
        loaded = load(tmp_path)
        assert loaded is not None
        assert loaded["messages"] == messages
        raw = default_path_for(tmp_path).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["messages"] == messages
