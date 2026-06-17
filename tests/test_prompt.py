"""Unit tests for the SystemPrompt class and BOUNDARY constant."""

import subprocess

from loop.agent.prompt import BOUNDARY, SystemPrompt


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
        """add_dynamic() is a backward-compatible alias for add_session()."""
        sp = SystemPrompt()
        sp.add_dynamic("section1")
        assert len(sp.session) == 1
        assert sp.session[0].endswith("\n")


class TestAddSession:
    def test_add_session_appends_newline(self):
        sp = SystemPrompt()
        sp.add_session("s1")
        sp.add_session("s2")
        assert len(sp.session) == 2
        for s in sp.session:
            assert s.endswith("\n")


class TestAddMemory:
    def test_add_memory_appends_newline(self):
        sp = SystemPrompt()
        sp.add_memory("m1")
        assert len(sp.memory) == 1
        assert sp.memory[0].endswith("\n")


class TestBuild:
    def test_build_joins_with_boundary(self):
        """build() joins static + BOUNDARY + session; static before session."""
        sp = SystemPrompt()
        sp.add_static("static A")
        sp.add_session("session A")
        result = sp.build()
        assert "---" in result
        idx_static = result.index("static A")
        idx_boundary = result.index("---")
        idx_session = result.index("session A")
        assert idx_static < idx_boundary < idx_session

    def test_build_empty_returns_empty_string(self):
        sp = SystemPrompt()
        result = sp.build()
        assert isinstance(result, str)
        assert result == ""

    def test_build_static_only_no_boundary(self):
        sp = SystemPrompt()
        sp.add_static("only static")
        assert sp.build() == "only static\n"

    def test_build_static_session_memory_two_boundaries(self):
        sp = SystemPrompt()
        sp.add_static("s")
        sp.add_session("ss")
        sp.add_memory("m")
        result = sp.build()
        assert result.count(BOUNDARY) == 2
        assert result.index("s") < result.index(BOUNDARY)
        assert result.index("ss") > result.index(BOUNDARY)
        assert result.index("m") > result.rindex(BOUNDARY)


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
