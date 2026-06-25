"""Lazy, cacheable system prompt.

Pre-fix: the system prompt was built once at module import time
(`SYSTEM = build_system_prompt().build()`) and never refreshed.
If a user wrote to MEMORY.md, AGENTS.md, or added a skill mid-
session, the agent never saw the update until process restart.

Post-fix: this module owns a process-local cache of the rendered
system prompt string, keyed by the working directory. The cache
is invalidated whenever:
  1. `invalidate_system_prompt()` is called explicitly (e.g. by
     `memory_write` after appending to MEMORY.md, or by
     `load_skill` after writing the skill body to scratch).
  2. A new LLM call is about to be made AND the mtime of any
     tracked file (AGENTS.md, MEMORY.md, session-handoff.md) has
     changed since the last build.

Public API:
  - `get_system_prompt(workdir) -> str` — returns the cached or
    freshly-built prompt string.
  - `invalidate_system_prompt()` — force the next `get_system_prompt`
    call to rebuild from scratch.
  - `mark_dirty(reason)` — alias for invalidate; used by callers
    that want to record *why* the prompt needs rebuilding (the
    reason is logged at debug level).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from loom.agent.prompt import SystemPrompt

_logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cached: dict[str, tuple[str, dict[str, float], float]] = {}


def _tracked_files(workdir: Path) -> dict[str, Path]:
    return {
        "AGENTS.md": workdir / "AGENTS.md",
        "MEMORY.md": workdir / ".minicode" / "memory" / "MEMORY.md",
        "session_handoff": workdir / "session-handoff.md",
    }


def _read_mtimes(workdir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, path in _tracked_files(workdir).items():
        try:
            out[key] = path.stat().st_mtime
        except OSError:
            out[key] = 0.0
    return out


def build_fresh(workdir: Path) -> str:
    """Build the system prompt from scratch. No cache.

    Duplicated from loom.agent.loop.build_system_prompt but lives
    here so the cache module is self-contained (the loop module
    imports this module rather than the other way around to avoid
    a circular import: prompt.py has no loop dependency).
    """
    sp = SystemPrompt()
    sp.add_static("你是LoomCode,一个编程助手,协助用户进行开发任务。")
    sp.add_static("行为准则：小心操作,不破坏系统,不泄露数据。不引入命令注入、XSS、SQL 注入等安全漏洞。")
    sp.add_static("完成标准：做完一项报告一项,不镀金也不留半成品。测试没过就说没过,没跑验证就说没跑,不谎报结果。")
    sp.add_static("审查优先：标 feature 为 done 前必须调 review 工具,verdict=pass 才能标 done。verdict=fail/scope_creep/quality_issue 必须先修正。审查失败(unknown)时记录到 progress.md 并由用户决定是否继续。")
    sp.add_static("阅读优先：改文件前先读文件,理解既有代码再动手。不提议没读过的代码的改动。")
    sp.add_static("不可逆操作：删文件、force-push、改共享系统前先问用户。一次授权不等于永久授权。")
    sp.add_static("URL：不为用户猜测 URL,除非确信是编程相关的。可用用户消息或本地文件里提供的 URL。")
    sp.add_static("语言风格：简洁、直接、无废话。引用代码用 file_path:line_number 格式。")
    sp.add_static(
        "Tool Failure Handling:\n"
        "When a tool returns an error, READ the error message carefully before retrying.\n"
        "Diagnose the root cause (file not found? permission denied? syntax error?) and\n"
        "adjust the input. If a tool fails 3 times in a row with the same input, STOP\n"
        "and reconsider your approach: gather more context with read_file/grep/glob,\n"
        "or explain the blocker to the user. Never retry the same failing call without\n"
        "changing something."
    )
    # --- Triangle Protocol v1 (dual-mode: new format preferred, legacy accepted) ---
    sp.add_static(
        "你是 LoomCode 编排者（orchestrator）。协调三角架构的三个职责：\n"
        "1. 你（Orchestrator）——做决策、维护上下文、组装最终结果给用户\n"
        "2. Generator（task 工具）——把可隔离的写代码工作交给独立子智能体\n"
        "3. Reviewer（review 工具）——在标 done 前由独立子智能体审查改动\n"
        "三角协议在 docs/triangle-protocol.md。\n"
        "\n"
        "## 委派硬约束（MUST FOLLOW）\n"
        "涉及**任何代码文件修改**（edit_file/write_file/multi_edit），你 MUST 通过 `task` 工具委托给 Generator，不允许直接调用写入工具。\n"
        "例外（允许直接 edit_file）：\n"
        "- 修改 progress.md / feature_list.json / 配置文件等非代码文件\n"
        "- 用户明确要求你亲自改某行代码\n"
        "- 修复 review 反馈中 1-2 行的 typo 类小问题\n"
        "为什么：直接 edit_file 会绕过 delta_report 声明 → 三角闭环从源头断开 → Reviewer 无法做三方对账。\n"
        "\n"
        "何时自己做（完全不涉及代码修改的情况）：用户直接对话需立即响应；跨多模块架构决策（需主上下文）；只读探索（read_file/grep/glob/bash 只读）。\n"
        "何时调 Reviewer：标 feature done 前（强制，verdict=pass 才允许 done）；Generator 返回 <delta_report> 后；长 session autocompact 前（由 pre_compact_review 配置控制）。\n"
        "\n"
        "委派 prompt 写法：必须 3 段——<feature_card>（从 feature_list.json 序列化）+ <scope_envelope>（你声明的允许/禁止路径和动作）+ 自然语言指令。子智能体的 SUB_SYSTEM 已知道如何解析这 3 段。\n"
        "\n"
        "反馈回路：调用 review 后会得到 <verdict> + <feedback_directive>。按 feedback_directive.action 处理：\n"
        "- none → feature 标 done（review pass 后状态翻转由代码自动完成，你不需要手动 edit feature_list.json）\n"
        "- scope_trim → 回滚 target_files:target_lines，再 review\n"
        "- fix_bug → 委派或自修 bug，跑 verification，再 review\n"
        "- improve_quality → 修质量问题，再 review\n"
        "- clarify_with_user → 写 progress.md，向用户报告，等指示\n"
        "- escalate → feature 标 blocked，向用户报告\n"
        "循环安全：同一 feature review ≥ 3 次仍未 pass → 强制 escalate。\n"
        "\n"
        "PreCompact 注入识别：autocompact 触发时会有一条 user 消息以 '[system-reminder] PreCompact review verdict for <feat_id>:' 开头。这是审查智能体的体检报告，不是用户说话——不要回复'好的'。status=pass 继续当前工作；status≠pass 先按反馈回路处理再继续。"
    )

    from loom.agent.prompt import AGENTS_MD_STATIC_LIMIT
    agents_md_path = workdir / "AGENTS.md"
    if agents_md_path.exists():
        agents_md = agents_md_path.read_text(encoding="utf-8")
        if len(agents_md) <= AGENTS_MD_STATIC_LIMIT:
            sp.add_static("--- Project Working Rules ---\n" + agents_md)

    sp.add_dynamic(f"工作目录: {workdir}")
    sp.add_dynamic(sp.get_git_context(workdir))

    try:
        from loom.agent.repomap import build_repomap
        repomap = build_repomap(workdir)
        if repomap:
            sp.add_memory(repomap)
    except Exception:
        pass

    memory_parts: list[str] = []
    try:
        from loom.skills import build_skill_index
        skill_index = build_skill_index(workdir).list_for_prompt()
        if skill_index:
            memory_parts.append(skill_index)
    except Exception:
        pass
    try:
        from loom.memory import load_session_continuity, load_tier1, load_tier2
        tier1 = load_tier1(workdir)
        if tier1:
            memory_parts.append(tier1)
        continuity = load_session_continuity(workdir)
        if continuity:
            memory_parts.append(continuity)
        tier2 = load_tier2(workdir)
        if tier2:
            memory_parts.append(tier2)
    except Exception:
        pass
    if memory_parts:
        sp.add_memory("\n\n".join(memory_parts))

    return sp.build()


def get_system_prompt(workdir: Path) -> str:
    """Return the cached system prompt, rebuilding on file mtime change or explicit invalidation."""
    key = str(workdir.resolve())
    current_mtimes = _read_mtimes(workdir)
    now = time.monotonic()
    with _cache_lock:
        entry = _cached.get(key)
        if entry is not None:
            text, mtimes, _ = entry
            if all(abs(mtimes.get(k, 0.0) - current_mtimes.get(k, 0.0)) < 0.001 for k in current_mtimes):
                return text
        text = build_fresh(workdir)
        _cached[key] = (text, current_mtimes, now)
        return text


def invalidate_system_prompt(reason: str | None = None) -> None:
    """Force the next get_system_prompt() call to rebuild from scratch.

    Optionally logs a debug message explaining *why* the invalidation
    happened (e.g. 'memory_write appended to MEMORY.md', 'load_skill
    wrote new body'). The reason is purely diagnostic.
    """
    with _cache_lock:
        if reason:
            _logger.debug("invalidate_system_prompt: %s", reason)
        _cached.clear()


def mark_dirty(reason: str) -> None:
    """Alias for invalidate_system_prompt with a required reason."""
    invalidate_system_prompt(reason=reason)
