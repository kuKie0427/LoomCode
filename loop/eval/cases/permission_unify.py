from __future__ import annotations

from types import SimpleNamespace

from loop.eval.runner import EvalCase, EvalResult


class PermissionSingleSourceOfTruth(EvalCase):
    name = "permission-single-source-of-truth"
    description = "PermissionPolicy is the only definition of deny_patterns in loop/; uses AST to find list/tuple literals"

    def run(self) -> EvalResult:
        import ast
        from pathlib import Path

        from loop.agent.permissions import DEFAULT_POLICY

        repo = Path(__file__).resolve().parents[3]
        loop_pkg = repo / "loop"
        target = "rm -rf /"
        leaks: list[str] = []
        for py in loop_pkg.rglob("*.py"):
            if py.name == "permissions.py":
                continue
            if "/__pycache__/" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.List, ast.Tuple)):
                    for elt in node.elts:
                        if isinstance(elt, ast.Constant) and elt.value == target:
                            leaks.append(str(py.relative_to(repo)))
                            break
        if leaks:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"list/tuple literal containing {target!r} outside permissions.py: {leaks}",
            )
        if len(DEFAULT_POLICY.deny_patterns) < 5:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"DEFAULT_POLICY.deny_patterns has only {len(DEFAULT_POLICY.deny_patterns)} entries",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"only permissions.py defines the list ({len(DEFAULT_POLICY.deny_patterns)} patterns)",
        )


class PermissionBashAndHookAgreeOnDd(EvalCase):
    name = "permission-bash-and-hook-agree-on-dd"
    description = "run_bash and Hooks.check_permission_hook BOTH block 'dd if=...'"

    def run(self) -> EvalResult:
        import loop.agent.tools as tools_mod
        from loop.agent.hooks import Hooks

        block = SimpleNamespace(name="bash", input={"command": "dd if=/dev/zero of=/tmp/x bs=1M"})
        hook_result = Hooks().check_permission_hook("PreToolUse", block)
        bash_result = tools_mod.run_bash("dd if=/dev/zero of=/tmp/x bs=1M")
        hook_blocked = hook_result is not None
        bash_blocked = "Dangerous command blocked" in bash_result
        if not (hook_blocked and bash_blocked):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"hook={hook_result!r} bash={bash_result[:80]!r}",
            )
        return EvalResult(name=self.name, passed=True, detail="both blocked dd")


class PermissionBashAndHookAgreeOnSudo(EvalCase):
    name = "permission-bash-and-hook-agree-on-sudo"
    description = "run_bash and Hooks.check_permission_hook BOTH block 'sudo'"

    def run(self) -> EvalResult:
        import loop.agent.tools as tools_mod
        from loop.agent.hooks import Hooks

        block = SimpleNamespace(name="bash", input={"command": "sudo apt install foo"})
        hook_result = Hooks().check_permission_hook("PreToolUse", block)
        bash_result = tools_mod.run_bash("sudo apt install foo")
        hook_blocked = hook_result is not None
        bash_blocked = "Dangerous command blocked" in bash_result
        if not (hook_blocked and bash_blocked):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"hook={hook_result!r} bash={bash_result[:80]!r}",
            )
        return EvalResult(name=self.name, passed=True, detail="both blocked sudo")


class PermissionPolicyIsDataDriven(EvalCase):
    name = "permission-policy-is-data-driven"
    description = "Hooks accepts a custom PermissionPolicy via constructor; rule changes flow through"

    def run(self) -> EvalResult:
        from loop.agent.hooks import Hooks
        from loop.agent.permissions import DEFAULT_POLICY, PermissionPolicy

        custom_pattern = f"unique-test-pattern-{__import__('uuid').uuid4().hex[:8]}"
        custom_policy = PermissionPolicy(
            deny_patterns=(custom_pattern,),
            rules=DEFAULT_POLICY.rules,
        )
        hooks = Hooks(policy=custom_policy)

        block_matched = SimpleNamespace(
            name="bash", input={"command": f"echo {custom_pattern}"},
        )
        block_unmatched = SimpleNamespace(
            name="bash", input={"command": "echo harmless"},
        )
        matched = hooks.check_permission_hook("PreToolUse", block_matched)
        unmatched = hooks.check_permission_hook("PreToolUse", block_unmatched)

        custom_works = matched is not None
        default_still_blocks = Hooks().check_permission_hook(
            "PreToolUse",
            SimpleNamespace(name="bash", input={"command": "sudo x"}),
        ) is not None
        if not (custom_works and unmatched is None and default_still_blocks):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"matched={matched!r} unmatched={unmatched!r} default_still={default_still_blocks}",
            )
        return EvalResult(name=self.name, passed=True, detail="custom policy isolated, default unchanged")