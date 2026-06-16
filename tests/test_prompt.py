"""Unit tests for the SystemPrompt class and BOUNDARY constant."""

import subprocess

from prompt import BOUNDARY, SystemPrompt


class TestAddStatic:
    def test_add_static_appends_newline(self):
        """add_static() auto-appends a newline to each section."""
        sp = SystemPrompt()
        sp.add_static("section1")
        sp.add_static("section2")
        assert len(sp.static) == 2
        for section in sp.static:
            assert section.endswith("\n")


class TestAddDynamic:
    def test_add_dynamic_appends_newline(self):
        """add_dynamic() auto-appends a newline to each section."""
        sp = SystemPrompt()
        sp.add_dynamic("section1")
        assert len(sp.dynamic) == 1
        assert sp.dynamic[0].endswith("\n")


class TestBuild:
    def test_build_joins_with_boundary(self):
        """build() joins static + BOUNDARY + dynamic; static before dynamic."""
        sp = SystemPrompt()
        sp.add_static("static A")
        sp.add_dynamic("dynamic A")
        result = sp.build()
        assert "---" in result
        idx_static = result.index("static A")
        idx_boundary = result.index("---")
        idx_dynamic = result.index("dynamic A")
        assert idx_static < idx_boundary < idx_dynamic

    def test_build_empty_no_crash(self):
        """Empty SystemPrompt build() returns a string containing BOUNDARY."""
        sp = SystemPrompt()
        result = sp.build()
        assert isinstance(result, str)
        assert BOUNDARY in result


class TestGetGitContext:
    def test_get_git_context_in_git_repo(self, temp_workdir):
        """In a real git repo, returns branch name and recent commits."""
        subprocess.run(["git", "init"], cwd=temp_workdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=temp_workdir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=temp_workdir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "test"],
            cwd=temp_workdir,
            capture_output=True,
        )

        sp = SystemPrompt()
        result = sp.get_git_context(temp_workdir)
        assert "当前分支" in result
        assert "最近提交" in result

    def test_get_git_context_non_git_dir(self, temp_workdir):
        """In a non-git directory, returns fallback message."""
        sp = SystemPrompt()
        result = sp.get_git_context(temp_workdir)
        assert "not a git repository" in result
