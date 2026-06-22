"""Harness eval cases for the f-prompt-rewrite-p0 prompt wording upgrade."""

from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult


def _system_prompt_source() -> str:
    from loom.agent import system_prompt as sp_mod
    return inspect.getsource(sp_mod.build_fresh)


def _tool_description(name: str) -> str:
    from loom.agent.tools import TOOL_REGISTRY
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return ""
    return tool.description or ""


class PromptRewriteIdentityIsLoomCode(EvalCase):
    name = "prompt-rewrite-identity-is-loomcode"
    description = "system prompt static tier names the agent 'LoomCode' (not MiniCode)"

    def run(self) -> EvalResult:
        src = _system_prompt_source()
        if "LoomCode" not in src:
            return EvalResult(name=self.name, passed=False, detail="'LoomCode' absent from build_fresh source")
        if "MiniCode" in src:
            return EvalResult(name=self.name, passed=False, detail="'MiniCode' still present — rename incomplete")
        return EvalResult(name=self.name, passed=True, detail="identity is 'LoomCode', 'MiniCode' removed")


class PromptRewriteOwaspSafetyBoundary(EvalCase):
    name = "prompt-rewrite-owasp-safety-boundary"
    description = "system prompt teaches OWASP lite (command injection / XSS / SQL injection)"

    def run(self) -> EvalResult:
        src = _system_prompt_source()
        missing = [s for s in ("命令注入", "XSS", "SQL 注入") if s not in src]
        if missing:
            return EvalResult(name=self.name, passed=False, detail=f"missing OWASP phrases: {missing}")
        return EvalResult(name=self.name, passed=True, detail="OWASP lite safety boundary present")


class PromptRewriteCompletionAnchor(EvalCase):
    name = "prompt-rewrite-completion-anchor"
    description = "system prompt has completion anchor (no gold-plate, no half-done, no false report)"

    def run(self) -> EvalResult:
        src = _system_prompt_source()
        if "不镀金" not in src or "半成品" not in src:
            return EvalResult(name=self.name, passed=False, detail="missing '不镀金/半成品' completion anchor")
        if "不谎报" not in src:
            return EvalResult(name=self.name, passed=False, detail="missing '不谎报结果' honesty anchor")
        return EvalResult(name=self.name, passed=True, detail="completion + honesty anchors present")


class PromptRewriteBashDenyListTeaching(EvalCase):
    name = "prompt-rewrite-bash-deny-list-teaching"
    description = "bash tool description teaches deny-list categories + dedicated-tool precedence + git safety"

    def run(self) -> EvalResult:
        desc = _tool_description("bash")
        if not desc:
            return EvalResult(name=self.name, passed=False, detail="bash not in TOOL_REGISTRY")
        checks = {
            "deny list mention": "deny list" in desc,
            "read_file precedence over cat": "read_file" in desc and "cat" in desc,
            "glob precedence over find": "glob" in desc and "find" in desc,
            "grep precedence over rg": "grep" in desc and "rg" in desc,
            "edit_file precedence over sed": "edit_file" in desc and "sed" in desc,
            "NEVER force-push": "NEVER force-push" in desc,
            "NEVER commit without explicit ask": "NEVER commit" in desc,
            "no --no-verify bypass": "--no-verify" in desc,
            "120s timeout": "120s" in desc,
        }
        failed = [k for k, v in checks.items() if not v]
        if failed:
            return EvalResult(name=self.name, passed=False, detail=f"missing: {failed}")
        return EvalResult(name=self.name, passed=True, detail="bash description teaches deny list + precedence + git safety")


class PromptRewriteTaskNeverDelegateUnderstanding(EvalCase):
    name = "prompt-rewrite-task-never-delegate-understanding"
    description = "task tool description teaches 'Never delegate understanding' + 'brief like colleague' + context boundary"

    def run(self) -> EvalResult:
        desc = _tool_description("task")
        if not desc:
            return EvalResult(name=self.name, passed=False, detail="task not in TOOL_REGISTRY")
        checks = {
            "Never delegate understanding": "Never delegate understanding" in desc,
            "brief like colleague": "colleague" in desc,
            "30 turn cap": "30 turns" in desc,
            "no re-delegation": "cannot re-delegate" in desc or "no `task` inside `task`" in desc,
            "anti-patterns section": "Anti-patterns" in desc,
            "do NOT fabricate": "fabricate" in desc,
            "CANNOT_FIX respect": "CANNOT_FIX" in desc,
        }
        failed = [k for k, v in checks.items() if not v]
        if failed:
            return EvalResult(name=self.name, passed=False, detail=f"missing: {failed}")
        return EvalResult(name=self.name, passed=True, detail="task description teaches delegation craft")


class PromptRewriteTaskWhenNotToUse(EvalCase):
    name = "prompt-rewrite-task-when-not-to-use"
    description = "task tool description has 'When NOT to use' with 5 dedicated-tool alternatives"

    def run(self) -> EvalResult:
        desc = _tool_description("task")
        if not desc:
            return EvalResult(name=self.name, passed=False, detail="task not in TOOL_REGISTRY")
        if "When NOT to use" not in desc:
            return EvalResult(name=self.name, passed=False, detail="missing 'When NOT to use' section")
        alternatives = ["read_file", "glob", "grep", "edit_file", "multi_edit"]
        missing = [a for a in alternatives if a not in desc]
        if missing:
            return EvalResult(name=self.name, passed=False, detail=f"missing dedicated-tool alternatives: {missing}")
        templates = ["task_investigate_code", "task_refactor_across_files", "task_fix_failing_test"]
        missing_t = [t for t in templates if t not in desc]
        if missing_t:
            return EvalResult(name=self.name, passed=False, detail=f"missing specialized template pointers: {missing_t}")
        return EvalResult(name=self.name, passed=True, detail="task description has 5 alternatives + 3 template pointers")


def _sub_system() -> str:
    from loom.agent.tools import SUB_SYSTEM
    return SUB_SYSTEM


class PromptRewriteSubSystemDelegationCraft(EvalCase):
    name = "prompt-rewrite-subsystem-delegation-craft"
    description = "SUB_SYSTEM teaches context assumption (no history) + grep priority + read-before-edit"

    def run(self) -> EvalResult:
        s = _sub_system()
        if not s:
            return EvalResult(name=self.name, passed=False, detail="SUB_SYSTEM empty / not importable")
        checks = {
            "context independence": "上下文独立" in s and "不继承" in s or "看不到" in s,
            "grep priority over read_file": "grep" in s and "read_file" in s and "便宜" in s,
            "read before edit": "改文件前先" in s and "read_file" in s,
            "structured report format": "文件路径" in s and "行号" in s,
            "final-only output": "只看到你的最终输出" in s or "最终输出" in s,
        }
        failed = [k for k, v in checks.items() if not v]
        if failed:
            return EvalResult(name=self.name, passed=False, detail=f"missing: {failed}")
        return EvalResult(name=self.name, passed=True, detail="SUB_SYSTEM teaches delegation craft")


class PromptRewriteSubSystemEscalationMarkers(EvalCase):
    name = "prompt-rewrite-subsystem-escalation-markers"
    description = "SUB_SYSTEM teaches 4 escalation markers (UNCLEAR / BLOCKED / CANNOT_FIX / OUT_OF_SCOPE)"

    def run(self) -> EvalResult:
        s = _sub_system()
        markers = ["[UNCLEAR", "[BLOCKED", "[CANNOT_FIX", "[OUT_OF_SCOPE"]
        missing = [m for m in markers if m not in s]
        if missing:
            return EvalResult(name=self.name, passed=False, detail=f"missing escalation markers: {missing}")
        if "Escalate" not in s and "escalate" not in s.lower():
            return EvalResult(name=self.name, passed=False, detail="missing 'Escalate' section header")
        return EvalResult(name=self.name, passed=True, detail="all 4 escalation markers + Escalate section present")


class PromptRewriteSubSystemAntiPatterns(EvalCase):
    name = "prompt-rewrite-subsystem-anti-patterns"
    description = "SUB_SYSTEM teaches anti-patterns: no fabricate / no silent skip / no test-edit / no destructive"

    def run(self) -> EvalResult:
        s = _sub_system()
        if "反模式" not in s:
            return EvalResult(name=self.name, passed=False, detail="missing '反模式' section header")
        checks = {
            "no fabricate": "fabricate" in s or "编造" in s,
            "no silent skip": "silently skip" in s or "跳过的步骤" in s,
            "no test edit": "skip/xfail" in s and "test" in s,
            "no destructive": "destructive" in s or "rm -rf" in s,
        }
        failed = [k for k, v in checks.items() if not v]
        if failed:
            return EvalResult(name=self.name, passed=False, detail=f"missing anti-pattern checks: {failed}")
        return EvalResult(name=self.name, passed=True, detail="SUB_SYSTEM teaches 4 anti-patterns")


class PromptRewriteSubSystemHonestyAnchor(EvalCase):
    name = "prompt-rewrite-subsystem-honesty-anchor"
    description = "SUB_SYSTEM has honesty anchor (no false report) — matches main agent completion anchor"

    def run(self) -> EvalResult:
        s = _sub_system()
        if "不谎报" not in s:
            return EvalResult(name=self.name, passed=False, detail="missing '不谎报结果' honesty anchor")
        if "没跑验证" not in s:
            return EvalResult(name=self.name, passed=False, detail="missing '没跑验证就说没跑' explicit honesty")
        return EvalResult(name=self.name, passed=True, detail="SUB_SYSTEM honesty anchor present (matches main agent)")
