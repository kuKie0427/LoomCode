from __future__ import annotations

import json
import stat
from pathlib import Path

from loop.init_cmd import init

EXPECTED_FILES = (
    "AGENTS.md",
    "feature_list.json",
    "feature_list.schema.json",
    "progress.md",
    "session-handoff.md",
    "init.sh",
)


class TestInitHappyPath:
    def test_creates_all_six_files(self, tmp_path: Path) -> None:
        results = init(tmp_path)
        written = {r.path.name for r in results if r.status == "written"}
        assert written == set(EXPECTED_FILES)

    def test_init_sh_is_executable(self, tmp_path: Path) -> None:
        init(tmp_path)
        mode = (tmp_path / "init.sh").stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    def test_agents_md_placeholders_replaced(self, tmp_path: Path) -> None:
        init(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text()
        assert "{{AGENT_FILE_NAME}}" not in content
        assert "{{PROJECT_PURPOSE}}" not in content
        assert "{{VERIFICATION_COMMANDS}}" not in content
        assert "{{PRIMARY_VERIFICATION_COMMAND}}" not in content
        assert "AGENTS.md" in content.splitlines()[0]

    def test_feature_list_is_valid_json(self, tmp_path: Path) -> None:
        init(tmp_path)
        data = json.loads((tmp_path / "feature_list.json").read_text())
        assert "features" in data
        assert len(data["features"]) == 5
        assert all("id" in f and "status" in f for f in data["features"])

    def test_feature_list_schema_is_valid_json(self, tmp_path: Path) -> None:
        init(tmp_path)
        data = json.loads((tmp_path / "feature_list.schema.json").read_text())
        assert "properties" in data
        assert "features" in data["properties"]

    def test_init_sh_runs_bash(self, tmp_path: Path) -> None:
        init(tmp_path)
        content = (tmp_path / "init.sh").read_text()
        assert content.startswith("#!/bin/bash")
        assert "set -e" in content
        assert "Harness Initialization" in content
        assert "Verification Complete" in content


class TestInitStackAware:
    def test_python_project_uses_pytest(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        init(tmp_path)
        content = (tmp_path / "init.sh").read_text()
        assert "python -m pytest" in content

    def test_node_project_uses_npm_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "x", "version": "0.0.1"}))
        init(tmp_path)
        content = (tmp_path / "init.sh").read_text()
        assert "npm install" in content

    def test_node_project_uses_pnpm(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "x", "version": "0.0.1"}))
        (tmp_path / "pnpm-lock.yaml").write_text("")
        init(tmp_path)
        content = (tmp_path / "init.sh").read_text()
        assert "pnpm install" in content
        assert "=== npm install ===" not in content
        assert "\nnpm install\n" not in content

    def test_generic_uses_placeholder(self, tmp_path: Path) -> None:
        init(tmp_path)
        content = (tmp_path / "init.sh").read_text()
        assert "No package manifest detected" in content


class TestInitOptions:
    def test_claude_md(self, tmp_path: Path) -> None:
        init(tmp_path, agent_file="CLAUDE.md")
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "CLAUDE.md" in content.splitlines()[0]

    def test_custom_commands_override(self, tmp_path: Path) -> None:
        init(tmp_path, custom_commands=["echo hello", "echo world"])
        content = (tmp_path / "init.sh").read_text()
        assert "echo hello" in content
        assert "echo world" in content
        assert "No package manifest detected" not in content

    def test_existing_files_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# EXISTING\n")
        results = init(tmp_path)
        skipped = {r.path.name: r for r in results if r.status == "skipped"}
        assert "AGENTS.md" in skipped
        assert (tmp_path / "AGENTS.md").read_text() == "# EXISTING\n"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# EXISTING\n")
        init(tmp_path, force=True)
        content = (tmp_path / "AGENTS.md").read_text()
        assert content != "# EXISTING\n"
        assert "Project harness" in content

    def test_init_creates_target_if_missing(self, tmp_path: Path) -> None:
        new = tmp_path / "subdir" / "project"
        init(new)
        assert new.is_dir()
        assert (new / "AGENTS.md").exists()
