"""Harness eval cases for f-subagent-grep-patterns-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class SubagentTemplatesDefined(EvalCase):
    name = "subagent-templates-defined"
    description = "3 subagent task templates registered: investigate_code, refactor_across_files, fix_failing_test"

    def run(self) -> EvalResult:
        from loom.agent.subagent_templates import list_templates, get_template
        names = list_templates()
        expected = {"investigate_code", "refactor_across_files", "fix_failing_test"}
        if set(names) != expected:
            return EvalResult(name=self.name, passed=False, detail=f"got {names}, expected {expected}")
        for n in expected:
            tpl = get_template(n)
            if not tpl.get("system"):
                return EvalResult(name=self.name, passed=False, detail=f"{n} has empty system prompt")
        return EvalResult(name=self.name, passed=True, detail="all 3 templates present with non-empty system prompts")


class SubagentTemplateToolsRegistered(EvalCase):
    name = "subagent-template-tools-registered"
    description = "task_investigate_code, task_refactor_across_files, task_fix_failing_test tools exist"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY
        for name in ("task_investigate_code", "task_refactor_across_files", "task_fix_failing_test"):
            if TOOL_REGISTRY.get(name) is None:
                return EvalResult(name=self.name, passed=False, detail=f"{name} not registered")
        return EvalResult(name=self.name, passed=True, detail="3 template tools registered")


class SubagentTemplateFormatIncludesArgs(EvalCase):
    name = "subagent-template-format-includes-args"
    description = "format_subagent_prompt embeds the user's args into a usable prompt"

    def run(self) -> EvalResult:
        from loom.agent.subagent_templates import format_subagent_prompt
        out = format_subagent_prompt("investigate_code", question="where is auth?")
        if "where is auth?" not in out:
            return EvalResult(name=self.name, passed=False, detail="user arg not in prompt")
        if "investigate_code" not in out:
            return EvalResult(name=self.name, passed=False, detail="template name not in prompt")
        return EvalResult(name=self.name, passed=True, detail="format includes user args + template name")
