from __future__ import annotations

import json
from pathlib import Path

import pytest

from loom.memory.context import (
    COMBINED_BUDGET,
    TIER1_TOKEN_BUDGET,
    TIER2_TOKEN_BUDGET,
    combined_tier1_tier2,
    load_tier1,
    load_tier2,
    load_tier3,
    truncate_to_tokens,
)
from loom.memory.paths import (
    find_project_root,
    is_own_project,
    memory_dir,
    memory_file,
)
from loom.memory.store import MemoryStore, token_count


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# AGENTS\nTest project.\n")
    feature = {
        "project": "test",
        "features": [
            {"id": "f1", "name": "Done", "description": "x", "status": "done",
             "verification": "x", "evidence": "x"},
            {"id": "f2", "name": "Active", "description": "y", "status": "in-progress",
             "verification": "y", "evidence": None},
            {"id": "f3", "name": "Blocked", "description": "z", "status": "blocked",
             "verification": "z", "blocker": "x"},
            {"id": "f4", "name": "Not started", "description": "w", "status": "not-started",
             "verification": "w", "evidence": None},
        ],
    }
    (tmp_path / "feature_list.json").write_text(json.dumps(feature))
    return tmp_path


class TestPaths:
    def test_memory_dir_under_minicode(self, tmp_path: Path) -> None:
        assert memory_dir(tmp_path) == tmp_path / ".minicode" / "memory"

    def test_memory_file_under_memory_dir(self, tmp_path: Path) -> None:
        assert memory_file(tmp_path) == tmp_path / ".minicode" / "memory" / "MEMORY.md"

    def test_find_project_root_returns_parent_of_minicode(self, tmp_path: Path) -> None:
        mem = memory_file(tmp_path)
        assert find_project_root(mem) == tmp_path

    def test_find_project_root_returns_none_for_orphan(self, tmp_path: Path) -> None:
        orphan = tmp_path / "no_minicode_ancestor" / "MEMORY.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("")
        assert find_project_root(orphan) is None

    def test_is_own_project_true(self, project: Path) -> None:
        assert is_own_project(memory_file(project), workdir=project) is True

    def test_is_own_project_false_for_sibling_project(self, project: Path, tmp_path: Path) -> None:
        foreign_root = tmp_path.parent / "sibling_project" / ".minicode" / "memory" / "MEMORY.md"
        foreign_root.parent.mkdir(parents=True)
        foreign_root.write_text("")
        assert is_own_project(foreign_root, workdir=project) is False

    def test_is_own_project_false_when_no_minicode(self, project: Path, tmp_path: Path) -> None:
        orphan = tmp_path / "no_minicode" / "MEMORY.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("")
        assert is_own_project(orphan, workdir=project) is False

    def test_is_own_project_false_for_parent_workdir(self, project: Path) -> None:
        nested = project / "subdir"
        nested.mkdir()
        assert is_own_project(nested, workdir=project) is False


class TestMemoryStore:
    def test_creates_dir_and_file_idempotently(self, project: Path) -> None:
        MemoryStore(project)
        assert memory_dir(project).is_dir()
        assert memory_file(project).exists()
        MemoryStore(project)
        assert memory_file(project).exists()

    def test_read_returns_header_initially(self, project: Path) -> None:
        store = MemoryStore(project)
        text = store.read()
        assert "Project Memory" in text

    def test_write_overwrites(self, project: Path) -> None:
        store = MemoryStore(project)
        store.write("# Custom\n")
        assert store.read() == "# Custom\n"

    def test_append_adds_dated_section(self, project: Path) -> None:
        store = MemoryStore(project)
        before = store.read()
        store.append("User prefers tabs over spaces.")
        after = store.read()
        assert "User prefers tabs" in after
        assert "## " in after
        assert len(after) > len(before)

    def test_append_respects_byte_cap(self, project: Path) -> None:
        store = MemoryStore(project)
        big = "x" * (26 * 1024)
        with pytest.raises(ValueError, match="bytes"):
            store.append(big)

    def test_append_respects_line_cap(self, project: Path) -> None:
        store = MemoryStore(project)
        chunk = "\n".join(f"line {i}" for i in range(250))
        with pytest.raises(ValueError, match="lines"):
            store.append(chunk)

    def test_search_case_insensitive(self, project: Path) -> None:
        store = MemoryStore(project)
        store.write("# Title\nFoo bar\nBAZ\n")
        matches = store.search("foo")
        assert any("Foo" in m for m in matches)

    def test_search_empty_query_returns_empty(self, project: Path) -> None:
        store = MemoryStore(project)
        assert store.search("") == []

    def test_session_log_creates_file(self, project: Path) -> None:
        store = MemoryStore(project)
        store.append_event("sess-1", {"kind": "user", "text": "hello"})
        store.append_event("sess-1", {"kind": "assistant", "text": "hi"})
        events = store.read_events("sess-1")
        assert events == [{"kind": "user", "text": "hello"}, {"kind": "assistant", "text": "hi"}]

    def test_session_log_missing_returns_empty(self, project: Path) -> None:
        store = MemoryStore(project)
        assert store.read_events("nope") == []


class TestTokenCount:
    def test_counts_words(self) -> None:
        assert token_count("one two three") == 3
        assert token_count("") == 0
        assert token_count("hello world foo") == 3
        assert token_count("a-b c") == 3


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert truncate_to_tokens("hello world", 100) == "hello world"

    def test_long_text_truncated_with_marker(self) -> None:
        text = "\n".join(f"line {i}" for i in range(200))
        out = truncate_to_tokens(text, 10)
        assert "truncated" in out
        assert len(out.splitlines()) < 200


class TestLoadTier1:
    def test_includes_feature_status(self, project: Path) -> None:
        text = load_tier1(project)
        assert "Feature Status" in text
        assert "1 done" in text
        assert "1 in-progress" in text
        assert "1 blocked" in text
        assert "1 not-started" in text

    def test_under_tier1_budget(self, project: Path) -> None:
        text = load_tier1(project)
        assert token_count(text) <= TIER1_TOKEN_BUDGET


class TestLoadTier2:
    def test_includes_agents_md(self, project: Path) -> None:
        text = load_tier2(project)
        assert "AGENTS.md" in text
        assert "Test project" in text

    def test_under_tier2_budget(self, project: Path) -> None:
        text = load_tier2(project)
        assert token_count(text) <= TIER2_TOKEN_BUDGET


class TestLoadTier3:
    def test_returns_empty_for_missing(self, tmp_path: Path) -> None:
        assert load_tier3(tmp_path / "nope.md") == ""

    def test_returns_content_for_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "x.md"
        p.write_text("# hello")
        assert load_tier3(p) == "# hello"


class TestCombinedT1T2:
    def test_under_combined_budget(self, project: Path) -> None:
        text = combined_tier1_tier2(project)
        assert token_count(text) <= COMBINED_BUDGET

    def test_works_with_no_memory(self, project: Path) -> None:
        text = combined_tier1_tier2(project)
        assert "Memory" in text or "(no" in text or "Tier 1" in text
