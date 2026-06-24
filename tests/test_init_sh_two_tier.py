"""Tests for the two-tier verification system (VerificationPlan, init_script_content, verify-quick.sh)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from loom.detect import (
    ProjectInfo,
    VerificationPlan,
    init_script_content,
    verification_commands,
    verification_plan,
)
from loom.init_cmd import init


class TestVerificationPlan:
    """Unit tests for the VerificationPlan frozen dataclass."""

    def test_constructs_from_tuples(self) -> None:
        plan = VerificationPlan(quick=("a",), full=("b", "c"))
        assert plan.quick == ("a",)
        assert plan.full == ("b", "c")

    def test_all_commands_returns_full_as_list(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f1", "f2"))
        assert plan.all_commands == ["f1", "f2"]

    def test_is_frozen(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        with pytest.raises(AttributeError):
            plan.quick = ("new",)  # type: ignore[misc]

    def test_tuples_not_lists(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        assert isinstance(plan.quick, tuple)
        assert isinstance(plan.full, tuple)


class TestVerificationPlanPerStack:
    """Per-stack tests for verification_plan()."""

    def test_python_quick_differs_from_full(self) -> None:
        plan = verification_plan(ProjectInfo(stack="python", root=Path("/")))
        assert plan.quick != plan.full

    def test_go_quick_differs_from_full(self) -> None:
        plan = verification_plan(ProjectInfo(stack="go", root=Path("/")))
        assert plan.quick != plan.full

    def test_rust_quick_differs_from_full(self) -> None:
        plan = verification_plan(ProjectInfo(stack="rust", root=Path("/")))
        assert plan.quick != plan.full

    def test_java_maven_quick_equals_full(self) -> None:
        plan = verification_plan(ProjectInfo(stack="java-maven", root=Path("/")))
        assert plan.quick == plan.full

    def test_generic_quick_equals_full(self) -> None:
        plan = verification_plan(ProjectInfo(stack="generic", root=Path("/")))
        assert plan.quick == plan.full

    def test_node_with_lint_quick_does_not_contain_install(self) -> None:
        project = ProjectInfo(
            root=Path("/"),
            stack="node",
            package_json={"scripts": {"lint": "eslint"}},
            package_manager="npm",
        )
        plan = verification_plan(project)
        assert "install" not in " ".join(plan.quick)
        assert "install" in " ".join(plan.full)


class TestInitScriptContent:
    """Tests for init_script_content() output strings."""

    def test_contains_mode_string(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        out = init_script_content(plan)
        assert "MODE" in out

    def test_contains_case_mode(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        out = init_script_content(plan)
        assert 'case "$MODE"' in out

    def test_contains_quick_block(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        out = init_script_content(plan)
        assert "quick)" in out

    def test_contains_full_block(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        out = init_script_content(plan)
        assert "full)" in out

    def test_unknown_mode_exits(self) -> None:
        plan = VerificationPlan(quick=("q",), full=("f",))
        out = init_script_content(plan)
        assert "exit 1" in out
        assert "Usage:" in out


class TestInitCmdGeneratesVerifyQuick:
    """Tests that init() creates the scripts/verify-quick.sh file correctly."""

    def test_init_writes_verify_quick_sh(self, tmp_path: Path) -> None:
        results = init(tmp_path)
        quick_results = [r for r in results if r.path.name == "verify-quick.sh"]
        assert len(quick_results) == 1
        assert quick_results[0].status == "written"

    def test_verify_quick_sh_is_executable(self, tmp_path: Path) -> None:
        init(tmp_path)
        quick_path = tmp_path / "scripts" / "verify-quick.sh"
        assert quick_path.exists()
        mode = quick_path.stat().st_mode
        assert mode & stat.S_IXUSR

    def test_verify_quick_sh_skipped_when_exists(self, tmp_path: Path) -> None:
        script_dir = tmp_path / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "verify-quick.sh").write_text("#!/bin/bash\necho existing\n")
        results = init(tmp_path, force=False)
        quick_results = [r for r in results if r.path.name == "verify-quick.sh"]
        assert len(quick_results) == 1
        assert quick_results[0].status == "skipped"


class TestBackCompat:
    """Backward-compatibility tests for the old API surface."""

    def test_verification_commands_still_returns_list(self) -> None:
        cmds = verification_commands(ProjectInfo(stack="python", root=Path("/")))
        assert isinstance(cmds, list)

    def test_init_script_content_accepts_verification_plan(self) -> None:
        plan = verification_plan(ProjectInfo(stack="python", root=Path("/")))
        out = init_script_content(plan)
        assert isinstance(out, str)
        assert len(out) > 0
