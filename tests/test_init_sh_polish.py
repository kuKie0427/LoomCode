"""Tests for init.sh polish: ruff/mypy detection, generic skeleton, marker injection, docs."""

from __future__ import annotations

from pathlib import Path

from loom.detect import (
    ProjectInfo,
    verification_plan,
)
from loom.init_cmd import (
    _maybe_inject_pytest_markers,
)


class TestPythonRuffMypyDetection:
    """verification_plan() detects ruff/mypy in pyproject.toml content."""

    def test_detects_ruff(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
        proj = ProjectInfo(root=tmp_path, stack="python")
        plan = verification_plan(proj)
        assert "ruff check ." in plan.quick
        assert "ruff check ." in plan.full

    def test_detects_mypy(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
        proj = ProjectInfo(root=tmp_path, stack="python")
        plan = verification_plan(proj)
        assert "mypy ." in plan.quick
        assert "mypy ." in plan.full

    def test_no_tool_config(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        proj = ProjectInfo(root=tmp_path, stack="python")
        plan = verification_plan(proj)
        assert "ruff check ." not in plan.quick
        assert "mypy ." not in plan.quick
        assert len(plan.quick) == 1
        assert len(plan.full) == 2

    def test_pyproject_read_failure(self, tmp_path: Path) -> None:
        """No pyproject.toml → OSError fallback → only pytest + compileall."""
        proj = ProjectInfo(root=tmp_path, stack="python")
        plan = verification_plan(proj)
        assert "ruff check ." not in plan.quick
        assert "mypy ." not in plan.quick
        assert len(plan.quick) == 1
        assert len(plan.full) == 2

    def test_detects_ruff_subtable(self, tmp_path: Path) -> None:
        """[tool.ruff.lint] subtable triggers detection via '[tool.ruff.' check."""
        (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\n")
        proj = ProjectInfo(root=tmp_path, stack="python")
        plan = verification_plan(proj)
        assert "ruff check ." in plan.quick


class TestGenericSkeleton:
    """Generic stack returns skeleton TODO placeholders."""

    def test_generic_returns_three_steps(self, tmp_path: Path) -> None:
        proj = ProjectInfo(root=tmp_path, stack="generic")
        plan = verification_plan(proj)
        assert len(plan.full) == 3
        assert len(plan.quick) == 3

    def test_generic_has_todo_comments(self, tmp_path: Path) -> None:
        proj = ProjectInfo(root=tmp_path, stack="generic")
        plan = verification_plan(proj)
        for step in plan.full:
            assert "TODO" in step, f"Missing 'TODO' in step: {step!r}"

    def test_generic_has_step_titles(self, tmp_path: Path) -> None:
        proj = ProjectInfo(root=tmp_path, stack="generic")
        plan = verification_plan(proj)
        assert "STEP 1: tests" in plan.full[0], plan.full[0]
        assert "STEP 2: lint" in plan.full[1], plan.full[1]
        assert "STEP 3: build" in plan.full[2], plan.full[2]


class TestPytestMarkerInjection:
    """_maybe_inject_pytest_markers() behavior."""

    def test_no_pyproject_skips(self, tmp_path: Path) -> None:
        result = _maybe_inject_pytest_markers(tmp_path, False)
        assert result is None

    def test_injects_markers_when_missing(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        result = _maybe_inject_pytest_markers(tmp_path, False)
        assert result is not None
        assert result.status == "written"
        content = (tmp_path / "pyproject.toml").read_text()
        assert "[tool.pytest.ini_options]" in content
        assert "markers" in content

    def test_skips_when_section_exists(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        result = _maybe_inject_pytest_markers(tmp_path, False)
        assert result is None

    def test_skips_when_markers_exist_R5(self, tmp_path: Path) -> None:
        """R5: Already configured with slow + snapshot markers → conservative skip."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\nmarkers = [\n"
            '    "slow: marks tests as slow",\n'
            '    "snapshot: visual snapshot tests",\n'
            "]\n"
        )
        result = _maybe_inject_pytest_markers(tmp_path, False)
        assert result is None


class TestDocsInitSh:
    """docs/init-sh.md exists with required 7 sections."""

    def test_docs_exist(self) -> None:
        doc = Path(__file__).resolve().parent.parent / "docs" / "init-sh.md"
        assert doc.exists(), f"docs/init-sh.md not found at {doc}"
        content = doc.read_text()
        sections = [
            "1. Two-Tier Mode",
            "2. What Quick Mode Does",
            "3. Custom Commands",
            "4. scripts/verify-quick.sh",
            "5. Pytest",
            "6. Troubleshooting",
            "7. Unix",
        ]
        for section in sections:
            assert section in content, f"Missing section heading: {section}"
