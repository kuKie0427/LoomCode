"""Eval cases for init.sh two-tier verification (quick vs full)."""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class InitShTwoTierVerificationPlanReturnsQuickAndFull(EvalCase):
    name = "init-sh-two-tier-verification-plan-returns-quick-and-full"
    description = "verification_plan() returns a VerificationPlan with distinct quick and full commands"

    def run(self) -> EvalResult:
        from loom.detect import ProjectInfo, verification_plan

        project = ProjectInfo(root=Path("/tmp"), stack="python")
        plan = verification_plan(project)

        if plan.quick == plan.full:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"quick and full are identical: quick={plan.quick!r}, full={plan.full!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"quick={plan.quick!r}, full={plan.full!r}",
        )


class InitShTwoTierInitScriptHasModeFlag(EvalCase):
    name = "init-sh-two-tier-init-script-has-mode-flag"
    description = "init_script_content() emits MODE= flag and case/quick/full branches"

    def run(self) -> EvalResult:
        from loom.detect import ProjectInfo, init_script_content, verification_plan

        project = ProjectInfo(root=Path("/tmp"), stack="python")
        plan = verification_plan(project)
        content = init_script_content(plan)

        checks = {
            "MODE=": 'MODE=' in content,
            'case "$MODE"': 'case "$MODE"' in content,
            "quick)": "quick)" in content,
            "full)": "full)" in content,
        }
        missing = [name for name, ok in checks.items() if not ok]
        if missing:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"missing patterns in output: {missing}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"all 4 mode patterns present in init.sh content ({len(content)} chars)",
        )


class InitShTwoTierInitCmdWritesVerifyQuickSh(EvalCase):
    name = "init-sh-two-tier-init-cmd-writes-verify-quick-sh"
    description = "loom init() writes scripts/verify-quick.sh and marks it executable"

    def run(self) -> EvalResult:
        from loom.init_cmd import init

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            init(tmp_path)

            quick_script = tmp_path / "scripts" / "verify-quick.sh"
            if not quick_script.exists():
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="scripts/verify-quick.sh was not created by init()",
                )

            st = quick_script.stat()
            is_exec = bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            if not is_exec:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="scripts/verify-quick.sh exists but is not executable",
                )

            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"scripts/verify-quick.sh exists, executable (mode={oct(st.st_mode)})",
            )


class InitShTwoTierVerificationCommandsBackwardCompat(EvalCase):
    name = "init-sh-two-tier-verification-commands-backward-compat"
    description = "verification_commands() still returns a list (backward compatibility)"

    def run(self) -> EvalResult:
        from loom.detect import ProjectInfo, verification_commands

        project = ProjectInfo(root=Path("/tmp"), stack="python")
        cmds = verification_commands(project)

        if not isinstance(cmds, list):
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"expected list, got {type(cmds).__name__}: {cmds!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"verification_commands() returned list with {len(cmds)} items: {cmds!r}",
        )
