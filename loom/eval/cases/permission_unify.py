from __future__ import annotations

from types import SimpleNamespace

from loom.eval.runner import EvalCase, EvalResult


class PermissionSingleSourceOfTruth(EvalCase):
    name = "permission-single-source-of-truth"
    description = "PermissionPolicy is the only definition of deny_patterns in loom/; uses AST to find list/tuple literals"

    def run(self) -> EvalResult:
        import ast
        from pathlib import Path

        from loom.agent.permissions import DEFAULT_POLICY

        repo = Path(__file__).resolve().parents[3]
        loop_pkg = repo / "loom"
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
        import loom.agent.tools as tools_mod
        from loom.agent.hooks import Hooks

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
        import loom.agent.tools as tools_mod
        from loom.agent.hooks import Hooks

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
        from loom.agent.hooks import Hooks
        from loom.agent.permissions import DEFAULT_POLICY, PermissionPolicy

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


class PermissionRuleRejectsSubclassesTraversal(EvalCase):
    name = "permission-rule-rejects-subclasses-traversal"
    description = "_compile_check rejects AST-based sandbox escape via __subclasses__"

    def run(self) -> EvalResult:
        from loom.agent.config import _compile_check

        expr = '().__class__.__bases__[0].__subclasses__()'
        result = _compile_check(expr, "test.field")
        if result is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected None, got callable {getattr(result, '__name__', result)!r}",
            )
        return EvalResult(name=self.name, passed=True, detail="rejected __subclasses__ traversal")


class PermissionRuleRejectsImport(EvalCase):
    name = "permission-rule-rejects-import"
    description = "_compile_check rejects __import__() in rule expressions"

    def run(self) -> EvalResult:
        from loom.agent.config import _compile_check

        expr = '__import__("os").system("id")'
        result = _compile_check(expr, "test.field")
        if result is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected None, got callable {getattr(result, '__name__', result)!r}",
            )
        return EvalResult(name=self.name, passed=True, detail="rejected __import__ call")


class PermissionRuleRejectsLambda(EvalCase):
    name = "permission-rule-rejects-lambda"
    description = "_compile_check rejects lambda expressions in rule expressions"

    def run(self) -> EvalResult:
        from loom.agent.config import _compile_check

        expr = '(lambda: 1)()'
        result = _compile_check(expr, "test.field")
        if result is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected None, got callable {getattr(result, '__name__', result)!r}",
            )
        return EvalResult(name=self.name, passed=True, detail="rejected lambda")


class PermissionRuleAcceptsArgsComparison(EvalCase):
    name = "permission-rule-accepts-args-comparison"
    description = "_compile_check accepts safe comparison expressions and returns a working callable"

    def run(self) -> EvalResult:
        from loom.agent.config import _compile_check

        check = _compile_check('"git" in args["command"]', "test.field")
        if check is None:
            return EvalResult(name=self.name, passed=False, detail="expected callable, got None")
        try:
            matched = check({"command": "git status"})
            unmatched = check({"command": "rm -rf /"})
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"callable raised {type(exc).__name__}: {exc}"[:120],
            )
        if matched is not True or unmatched is not False:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"matched={matched!r} unmatched={unmatched!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="callable works for matching and non-matching args",
        )