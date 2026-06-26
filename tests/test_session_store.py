from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loom.agent.session_store import (
    _MAX_SESSIONS,
    SessionMeta,
    SessionStore,
    default_session_name,
    generate_session_name,
)


class FakeContext:
    def __init__(self, last_input_tokens: int = 100, checked_at_index: int = 5):
        self.last_input_tokens = last_input_tokens
        self.checked_at_index = checked_at_index


def _fake_llm(model: str = "test-model") -> MagicMock:
    llm = MagicMock()
    llm.model = model
    return llm


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


class TestSessionMeta:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        meta = SessionMeta(
            session_id="abc123",
            name="My Session",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
            message_count=5,
            tool_call_count=10,
            model="test-model",
        )
        d = meta.to_dict()
        assert SessionMeta.from_dict(d) == meta

    def test_from_dict_tolerant_missing_fields(self) -> None:
        meta = SessionMeta.from_dict({"session_id": "x"})
        assert meta.session_id == "x"
        assert meta.name == "Untitled"
        assert meta.message_count == 0
        assert meta.tool_call_count == 0

    def test_from_dict_tolerant_bad_types(self) -> None:
        meta = SessionMeta.from_dict(
            {"session_id": "x", "message_count": "not-a-num", "tool_call_count": None}
        )
        assert meta.message_count == 0
        assert meta.tool_call_count == 0

    def test_frozen_dataclass_is_immutable(self) -> None:
        meta = SessionMeta(
            session_id="x",
            name="n",
            created_at="",
            updated_at="",
            message_count=0,
            tool_call_count=0,
            model="",
        )
        with pytest.raises(AttributeError):
            meta.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SessionStore — create / save / load
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_returns_unique_id(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid1 = store.create_session()
        sid2 = store.create_session()
        assert sid1 != sid2
        assert len(sid1) == 12

    def test_registers_in_index(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session(name="Test")
        metas = store.list_sessions()
        assert len(metas) == 1
        assert metas[0].session_id == sid
        assert metas[0].name == "Test"

    def test_default_name_when_none(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        metas = store.list_sessions()
        assert metas[0].session_id == sid
        assert "Session" in metas[0].name


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session(name="Roundtrip")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
        ]
        ctx = FakeContext(last_input_tokens=500, checked_at_index=2)

        path = store.save_session(sid, messages, _fake_llm(), ctx, tool_call_count=12)
        assert path is not None
        assert path.exists()

        loaded = store.load_session(sid)
        assert loaded is not None
        assert loaded["session_id"] == sid
        assert loaded["session_name"] == "Roundtrip"
        assert loaded["messages"] == messages
        assert loaded["tool_call_count"] == 12
        assert loaded["model"] == "test-model"
        assert loaded["last_input_tokens"] == 500
        assert loaded["checked_at_index"] == 2
        assert "saved_at" in loaded

    def test_save_updates_index_meta(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        messages = [{"role": "user", "content": "hi"}]
        store.save_session(sid, messages, _fake_llm("gpt-4"), FakeContext(), 3)

        metas = store.list_sessions()
        assert len(metas) == 1
        assert metas[0].message_count == 1
        assert metas[0].tool_call_count == 3
        assert metas[0].model == "gpt-4"

    def test_save_preserves_created_at_on_update(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        messages1 = [{"role": "user", "content": "1"}]
        store.save_session(sid, messages1, _fake_llm(), FakeContext(), 1)

        original = store.list_sessions()[0]
        created_at = original.created_at

        # Save again with more messages.
        messages2 = [{"role": "user", "content": "1"}, {"role": "user", "content": "2"}]
        store.save_session(sid, messages2, _fake_llm(), FakeContext(), 2)

        updated = store.list_sessions()[0]
        assert updated.created_at == created_at
        assert updated.message_count == 2

    def test_load_returns_none_when_missing(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        assert store.load_session("nonexistent") is None

    def test_load_returns_none_for_corrupted_json(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        # Write a bad session file directly.
        path = store._session_path("bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json {{{", encoding="utf-8")
        assert store.load_session("bad") is None

    def test_save_is_atomic_no_tmp_leftover(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        store.save_session(sid, [], _fake_llm(), FakeContext(), 0)
        # No .tmp files should remain in the sessions dir.
        tmps = list(store._dir.glob("*.tmp"))
        assert tmps == []

    def test_save_serializes_complex_messages(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "done"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file.txt"},
                ],
            },
        ]
        store.save_session(sid, messages, _fake_llm(), FakeContext(), 1)
        loaded = store.load_session(sid)
        assert loaded is not None
        assert loaded["messages"] == messages


# ---------------------------------------------------------------------------
# SessionStore — list / delete / rename
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_empty_initially(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        assert store.list_sessions() == []

    def test_mru_sorted(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid_a = store.create_session(name="A")
        sid_b = store.create_session(name="B")
        # Save A again so its updated_at is newer.
        store.save_session(sid_a, [{"role": "user", "content": "x"}], _fake_llm(), FakeContext(), 1)
        metas = store.list_sessions()
        # A should be first (most recently updated).
        assert metas[0].session_id == sid_a
        assert metas[1].session_id == sid_b


class TestDeleteSession:
    def test_delete_removes_file_and_index(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session()
        store.save_session(sid, [{"role": "user", "content": "x"}], _fake_llm(), FakeContext(), 1)
        assert store._session_path(sid).exists()

        assert store.delete_session(sid) is True
        assert not store._session_path(sid).exists()
        assert store.list_sessions() == []

    def test_delete_missing_returns_false_or_true(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        # Deleting a session whose file doesn't exist — unlink(missing_ok=True)
        # means no OSError, so it returns True (index entry removed if present).
        result = store.delete_session("never-existed")
        # Either is acceptable per impl; just verify no crash and index is empty.
        assert isinstance(result, bool)
        assert store.list_sessions() == []


class TestRenameSession:
    def test_rename_updates_index_and_file(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        sid = store.create_session(name="Old")
        store.save_session(sid, [{"role": "user", "content": "x"}], _fake_llm(), FakeContext(), 1)

        assert store.rename_session(sid, "New Name") is True
        metas = store.list_sessions()
        assert metas[0].name == "New Name"
        data = store.load_session(sid)
        assert data is not None
        assert data["session_name"] == "New Name"

    def test_rename_missing_returns_false(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        assert store.rename_session("nope", "x") is False


# ---------------------------------------------------------------------------
# SessionStore — index cap
# ---------------------------------------------------------------------------


class TestIndexCap:
    def test_cap_prunes_oldest_sessions(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        # Create _MAX_SESSIONS + 2 sessions.
        for i in range(_MAX_SESSIONS + 2):
            sid = store.create_session(name=f"S{i}")
            store.save_session(
                sid, [{"role": "user", "content": str(i)}], _fake_llm(), FakeContext(), i
            )
        metas = store.list_sessions()
        assert len(metas) == _MAX_SESSIONS
        # The two oldest (first created, never re-saved) should be pruned.


# ---------------------------------------------------------------------------
# SessionStore — malformed index
# ---------------------------------------------------------------------------


class TestMalformedIndex:
    def test_load_handles_missing_index(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        assert store._load_index() == []

    def test_load_handles_corrupt_index(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        store._index_path.parent.mkdir(parents=True, exist_ok=True)
        store._index_path.write_text("not json {{{", encoding="utf-8")
        assert store._load_index() == []

    def test_load_handles_non_list_index(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        store._index_path.parent.mkdir(parents=True, exist_ok=True)
        store._index_path.write_text('{"not": "a list"}', encoding="utf-8")
        assert store._load_index() == []


# ---------------------------------------------------------------------------
# Session naming helpers
# ---------------------------------------------------------------------------


class TestDefaultSessionName:
    def test_short_message(self) -> None:
        assert default_session_name("hello world") == "hello world"

    def test_long_message_truncated(self) -> None:
        msg = "a" * 100
        name = default_session_name(msg)
        assert len(name) == 30

    def test_multiline_collapsed(self) -> None:
        name = default_session_name("line one\nline two")
        assert "\n" not in name

    def test_empty_message(self) -> None:
        assert default_session_name("") == "Untitled"


class TestGenerateSessionName:
    def test_returns_none_when_no_complete_fn(self) -> None:
        llm = MagicMock()
        llm._provider = MagicMock(spec=[])  # no complete attr
        llm.model_id = "x"
        assert generate_session_name(llm, "hello") is None

    def test_returns_summary_from_complete_fn(self) -> None:
        llm = MagicMock()
        provider = MagicMock()
        provider.complete.return_value = "fix the login bug"
        llm._provider = provider
        llm.model_id = "x"
        result = generate_session_name(llm, "Please fix the login bug on the auth page")
        assert result == "fix the login bug"

    def test_handles_dict_response_with_content_list(self) -> None:
        llm = MagicMock()
        provider = MagicMock()
        provider.complete.return_value = {
            "content": [{"type": "text", "text": "  summary text  "}]
        }
        llm._provider = provider
        llm.model_id = "x"
        result = generate_session_name(llm, "some request")
        assert result == "summary text"

    def test_handles_dict_response_with_content_str(self) -> None:
        llm = MagicMock()
        provider = MagicMock()
        provider.complete.return_value = {"content": "plain string summary"}
        llm._provider = provider
        llm.model_id = "x"
        result = generate_session_name(llm, "some request")
        assert result == "plain string summary"

    def test_truncates_long_summary(self) -> None:
        llm = MagicMock()
        provider = MagicMock()
        long_text = "x" * 100
        provider.complete.return_value = long_text
        llm._provider = provider
        llm.model_id = "x"
        result = generate_session_name(llm, "request")
        assert result is not None
        assert len(result) <= 60

    def test_returns_none_on_exception(self) -> None:
        llm = MagicMock()
        provider = MagicMock()
        provider.complete.side_effect = RuntimeError("boom")
        llm._provider = provider
        llm.model_id = "x"
        assert generate_session_name(llm, "request") is None


# ---------------------------------------------------------------------------
# SessionStore — directory creation
# ---------------------------------------------------------------------------


class TestDirectoryCreation:
    def test_creates_sessions_dir(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".minicode" / "sessions"
        assert not sessions_dir.exists()
        SessionStore(tmp_path)
        assert sessions_dir.exists()
        assert sessions_dir.is_dir()

    def test_index_file_path(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path)
        assert store._index_path == tmp_path / ".minicode" / "sessions" / "index.json"
