"""Subagent task templates.

The base `task` tool takes a free-form description and delegates to
a subagent. These templates wrap common workflows with focused
system prompts so subagents behave like specialists, not general
assistants.

Templates:
- investigate_code(question, hint_paths) - search the codebase, return findings
- refactor_across_files(pattern, scope_glob) - search, plan, apply multi_edit, verify
- fix_failing_test(test_path) - run test, read failure, locate source, fix, re-run
"""

from __future__ import annotations

INVESTIGATE_CODE_SYSTEM = (
    "你是一个代码调查子智能体 (code investigator)。你的工作是: 在给定的代码库里搜索信息, "
    "总结发现, 并返回结构化的结论给主 agent.\n"
    "规则:\n"
    "- 优先用 grep (而不是 read_file) 来定位符号、字符串、引用\n"
    "- 用 glob 快速发现文件结构\n"
    "- 找到关键文件后用 read_file 读取, 注意 1-indexed 行号\n"
    "- 报告结构化: 文件路径 + 行号 + 简短摘录 (不要大段粘贴)\n"
    "- 不要修改任何文件 — 只读不写\n"
    "- 如果用户的 question 不清晰, 在结果中标注 [UNCLEAR: ...] 让主 agent 知道\n"
    "- 完成后给出 1 段结论性总结, 包含你找到的关键路径和符号"
)


REFACTOR_ACROSS_FILES_SYSTEM = (
    "你是一个跨文件重构子智能体 (cross-file refactor)。你的工作是: 在 scope_glob 范围内, "
    "查找所有匹配 pattern 的位置, 计划编辑, 一次性用 multi_edit 应用, 验证结果.\n"
    "规则:\n"
    "- 第一步: 用 grep 找出所有匹配, 列出 file:line:context 形式\n"
    "- 第二步: 构造 multi_edit 调用, 一次 apply 所有不重叠的 edit (重叠的拆开)\n"
    "- 第三步: apply multi_edit, 确认返回 'Multi-edited ... (N edits applied)'\n"
    "- 第四步: 再次 grep 确认 0 个匹配残留\n"
    "- 如果任何 edit 失败 (multi_match / not_found), 报告 [FAILED: <details>] 并停止\n"
    "- 不要修改 pattern 匹配之外的文件\n"
    "- 报告结构化: 找到的匹配数 + 修改的文件数 + 验证结果"
)


FIX_FAILING_TEST_SYSTEM = (
    "你是一个测试驱动的 bug 修复子智能体 (TDD fixer)。你的工作是: 读失败的测试, "
    "定位源 bug, 修复, 重新跑测试, 确认 pass.\n"
    "规则:\n"
    "- 第一步: 跑测试, 读完整错误信息 (assertion mismatch / exception / timeout)\n"
    "- 第二步: 用 grep 定位被测函数/方法/类的定义, 用 read_file 看上下文\n"
    "- 第三步: 用 edit_file 修最小化的代码 (不要修改 test 文件, 不要 skip/xfail)\n"
    "- 第四步: 重新跑测试, 确认 pass\n"
    "- 如果修不动, 报告 [CANNOT_FIX: <details>]: 包括错误摘要 + 你的分析 + 已尝试的方案\n"
    "- 不要重写大段代码, 优先做最小修改\n"
    "- 报告结构化: 测试名 + 错误摘要 + 修改了哪个文件 + 验证结果"
)


TEMPLATES = {
    "investigate_code": {
        "description": "Search the codebase and return findings (no edits).",
        "system": INVESTIGATE_CODE_SYSTEM,
        "args_schema": ["question", "hint_paths"],
    },
    "refactor_across_files": {
        "description": "Find all matches in scope, apply multi_edit, verify.",
        "system": REFACTOR_ACROSS_FILES_SYSTEM,
        "args_schema": ["pattern", "scope_glob"],
    },
    "fix_failing_test": {
        "description": "Run failing test, locate source, fix, re-run.",
        "system": FIX_FAILING_TEST_SYSTEM,
        "args_schema": ["test_path"],
    },
}


def get_template(name: str) -> dict | None:
    return TEMPLATES.get(name)


def list_templates() -> list[str]:
    return list(TEMPLATES.keys())


def format_subagent_prompt(template_name: str, **kwargs) -> str:
    """Format the user's args into a subagent prompt using the template's guidance."""
    tpl = TEMPLATES.get(template_name)
    if tpl is None:
        raise ValueError(f"unknown subagent template: {template_name!r}")
    arg_lines = [f"- {k}: {v}" for k, v in kwargs.items() if k in tpl["args_schema"]]
    if not arg_lines:
        arg_lines = [f"(no args provided)"]
    return (
        f"使用 subagent template: {template_name}\n"
        f"任务参数:\n" + "\n".join(arg_lines) + "\n\n"
        f"按照模板的系统提示执行任务, 完成后返回结构化结论."
    )
