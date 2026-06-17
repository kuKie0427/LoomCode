from __future__ import annotations

from pathlib import Path

import pytest

from loop.skills import build_skill_index
from loop.skills.discovery import list_skill_dirs
from loop.skills.registry import parse_skill_md

SAMPLE_SKILL = """# run-pytest

Run pytest with concise output and report failures clearly.

## Triggers

- run pytest
- run tests
- test

## Steps

1. Find the test command from init.sh
2. Run it
3. Parse the output

## Notes

- Use --tb=short for concise output
"""


SAMPLE_MINIMAL = """# hello

Say hello to the world.
"""


@pytest.fixture
def with_user_skill(tmp_path: Path, monkeypatch) -> Path:
    """Create ~/.minicode/skills/run-pytest/SKILL.md and return workdir."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    user_skills = home / ".minicode" / "skills" / "run-pytest"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text(SAMPLE_SKILL)
    workdir = tmp_path / "project"
    workdir.mkdir()
    return workdir


class TestListSkillDirs:
    def test_returns_user_global_first_then_project_local(self, tmp_path: Path) -> None:
        workdir = tmp_path / "p"
        dirs = list_skill_dirs(workdir)
        assert dirs[0] == Path.home() / ".minicode" / "skills"
        assert dirs[1] == workdir / ".minicode" / "skills"


class TestParseSkillMd:
    def test_parses_full_skill(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(SAMPLE_SKILL)
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.name == "run-pytest"
        assert "concise output" in skill.description
        assert skill.triggers == ["run pytest", "run tests", "test"]
        assert "init.sh" in skill.body
        assert "--tb=short" in skill.body

    def test_parses_minimal_skill(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(SAMPLE_MINIMAL)
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.name == "hello"
        assert skill.description == "Say hello to the world."
        assert skill.triggers == []
        assert skill.body == ""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert parse_skill_md(tmp_path / "nope.md") is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "SKILL.md"
        path.write_text("")
        assert parse_skill_md(path) is None


class TestBuildSkillIndex:
    def test_empty_when_no_skill_dirs(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        idx = build_skill_index(tmp_path / "project")
        assert len(idx) == 0
        assert idx.names() == []

    def test_discover_user_global_skill(self, with_user_skill: Path) -> None:
        idx = build_skill_index(with_user_skill)
        assert "run-pytest" in idx.names()
        skill = idx.get("run-pytest")
        assert skill is not None
        assert "concise output" in skill.description

    def test_project_local_overrides_user_global(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        (home / ".minicode" / "skills" / "shared").mkdir(parents=True)
        (home / ".minicode" / "skills" / "shared" / "SKILL.md").write_text(
            "# shared\n\nFrom user-global.\n"
        )
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / ".minicode" / "skills" / "shared").mkdir(parents=True)
        (workdir / ".minicode" / "skills" / "shared" / "SKILL.md").write_text(
            "# shared\n\nFrom project-local (overrides).\n"
        )
        idx = build_skill_index(workdir)
        skill = idx.get("shared")
        assert skill is not None
        assert "project-local" in skill.description

    def test_project_local_skill_added(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        workdir = tmp_path / "project"
        workdir.mkdir()
        (workdir / ".minicode" / "skills" / "local-only").mkdir(parents=True)
        (workdir / ".minicode" / "skills" / "local-only" / "SKILL.md").write_text(
            "# local-only\n\nOnly in project.\n"
        )
        idx = build_skill_index(workdir)
        assert "local-only" in idx.names()
        assert "user-global-only" not in idx.names()

    def test_skill_directory_without_skill_md_is_ignored(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        (home / ".minicode" / "skills" / "no-skill-md").mkdir(parents=True)
        (home / ".minicode" / "skills" / "no-skill-md" / "README.md").write_text("not a SKILL.md")
        idx = build_skill_index(tmp_path / "project")
        assert "no-skill-md" not in idx.names()


class TestSkillIndexForPrompt:
    def test_lists_skills_with_triggers(self, with_user_skill: Path) -> None:
        idx = build_skill_index(with_user_skill)
        prompt = idx.list_for_prompt()
        assert "Available Skills" in prompt
        assert "run-pytest" in prompt
        assert "concise output" in prompt
        assert "triggers" in prompt

    def test_empty_index_returns_empty_string(self) -> None:
        idx = build_skill_index(Path("/nonexistent"))
        assert idx.list_for_prompt() == ""

    def test_body_lookup(self, with_user_skill: Path) -> None:
        idx = build_skill_index(with_user_skill)
        assert idx.body("run-pytest") is not None
        assert "init.sh" in idx.body("run-pytest")
        assert idx.body("nonexistent") is None
