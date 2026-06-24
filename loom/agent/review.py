"""Review tool — spawn a read-only subagent to inspect code and verify quality.

Provides REVIEW_SYSTEM (Chinese review agent prompt), ReviewVerdict
dataclass, _parse_verdict(), and run_review() for the ``review`` tool.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from loguru import logger

from loom.agent.triangle_protocol import (
    DeltaReport,
    FeedbackDirective,
    ScopeEnvelope,
)

VerdictStatus = Literal["pass", "fail", "scope_creep", "quality_issue", "unknown"]

# Per-feature review attempt counter for I9 invariant (≤3 attempts per feature).
# Reset at agent_loop entry — TP-3 only. TP-4 will persist to feature_list.json.
_REVIEW_ATTEMPT_COUNTER: dict[str, int] = {}


@dataclass
class ReviewVerdict:
    """Structured verdict from a code review.

    Attributes:
        status: One of pass/fail/scope_creep/quality_issue/unknown.
        summary: Human-readable summary in Chinese (2-5 sentences).
        evidence: List of evidence strings (file:line + brief description).
        recommendations: List of improvement suggestions.
        raw_response: The full raw text from the review subagent.
    """
    status: VerdictStatus
    summary: str = ""
    evidence: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    raw_response: str = ""

    @property
    def is_pass(self) -> bool:
        return self.status == "pass"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "ReviewVerdict":
        data = json.loads(s)
        return cls(**data)


_VERDICT_RE = re.compile(r"<verdict>(.*?)</verdict>", re.DOTALL)


def _parse_verdict(text: str) -> ReviewVerdict:
    """Extract ``<verdict>{json}</verdict>`` from *text* and parse as ReviewVerdict.

    On failure (missing tag, invalid JSON): log a warning and return
    ReviewVerdict(status="unknown", raw_response=text).
    """
    m = _VERDICT_RE.search(text)
    if not m:
        logger.warning("_parse_verdict: no <verdict> tag found in response")
        return ReviewVerdict(status="unknown", raw_response=text)
    try:
        data = json.loads(m.group(1))
        return ReviewVerdict(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("_parse_verdict: failed to parse verdict JSON: {}", exc)
        return ReviewVerdict(status="unknown", raw_response=text)


REVIEW_SYSTEM = (
    "你是一个代码审查智能体（review agent），由主 agent 委派进行只读审查。\n"
    "你的任务是对指定的功能（feature）进行质量审查，给出通过/不通过的 verdict。\n"
    "\n"
    "### 重要规则 ###\n"
    "- 你只能**读取**代码，不能修改任何文件\n"
    "- 你只能使用 read_file、grep、glob、bash（只读命令）这些只读工具\n"
    "- 你的 bash 命令仅限 git status/diff/log、ls、cat -n 等查看类命令\n"
    "- 不能调用 write_file、edit_file、multi_edit、edit_lines 等写入工具\n"
    "- 不要调用 task、review、memory_write、cold_archive 等工具\n"
    "\n"
    "### 审查标准 ###\n"
    "对给定的 feature，你需要检查：\n"
    "1. 代码是否存在（文件路径、函数、类是否正确实现）\n"
    "2. 代码风格是否与项目一致\n"
    "3. 是否遵守了项目的 harness 规则（AGENTS.md 中的规则）\n"
    "4. 是否有明显的 bug 或边缘情况未处理\n"
    "5. 是否超出了 feature 的 scope（做了额外的事情）\n"
    "\n"
    "### Verdict 类型 ###\n"
    "- pass: 审查通过，功能实现正确\n"
    "- fail: 功能未正确实现，有 bug\n"
    "- scope_creep: 实现了超出范围的功能（额外修改了无关文件）\n"
    "- quality_issue: 功能基本正确但有代码质量问题\n"
    "- unknown: 无法判断（信息不足）\n"
    "\n"
    "### 输出格式 ###\n"
    "在你的最终回复中，必须包含一个 <verdict> 标签，其中是一个 JSON 对象：\n"
    "<verdict>{json}</verdict>\n"
    "\n"
    "JSON 字段：\n"
    "- status: 上述 verdict 类型之一\n"
    "- summary: 审查摘要（中文，2-5 句话）\n"
    "- evidence: 字符串列表，每条是一条证据（文件路径+行号+简述）\n"
    "- recommendations: 字符串列表，每条是一条改进建议（如适用）\n"
    "\n"
    "示例：\n"
    '<verdict>{"status": "pass", "summary": "功能实现正确，代码风格一致。", '
    '"evidence": ["src/foo.py:42: 实现了 bar() 函数"], "recommendations": []}</verdict>\n'
    "\n"
    "注意：不要输出多余的 <verdict> 标签，只输出一个。"
)


def run_review(
    feature_id: str,
    feature_description: str,
    scope_hint: str = "",
    delta_report: DeltaReport | None = None,
    scope: ScopeEnvelope | None = None,
    workdir: Path | None = None,
) -> tuple[str, FeedbackDirective | None]:
    """Run a code review for the given feature.

    Spawns a read-only subagent with REVIEW_SYSTEM + REVIEW_TOOLS, parses
    the ``<verdict>`` from its output, and returns (verdict_str, feedback_directive).

    Pre-processing (I7/I8 enforcement, hard):
    If delta_report AND scope/workdir are provided, call
    validate_delta_against_scope() and validate_delta_against_git_diff()
    BEFORE spawning the Reviewer LLM. If violations are non-empty,
    return (verdict_str_with_unknown, None) immediately — bypasses the
    LLM round-trip.

    Post-processing (I5/I6 enforcement, hard):
    Parse <verdict> and <feedback_directive> from Reviewer output.
    parse_feedback_directive() validates action list combination rules (§7.3).
    If invalid → returns fd=None. If <verdict> missing → returns verdict=unknown.

    Args:
        feature_id: Short identifier for the feature.
        feature_description: Description of what was implemented.
        scope_hint: Optional hint about scope boundary.
        delta_report: Optional DeltaReport for three-way reconciliation.
        scope: Optional ScopeEnvelope for pre-validation.
        workdir: Optional working directory for git diff validation.

    Returns:
        Tuple of (formatted_review_result_string, FeedbackDirective_or_None).
    """
    # Lazy import to avoid circular dependency (tools.py imports from review.py)
    from loom.agent.tools import REVIEW_TOOLS, spawn_subagent

    # === PRE-PROCESSING: validate delta before spending LLM tokens (I7/I8) ===
    if delta_report is not None:
        violations: list[str] = []
        if scope is not None:
            from loom.agent.triangle_protocol import validate_delta_against_scope
            violations.extend(validate_delta_against_scope(delta_report, scope))
        if workdir is not None:
            from loom.agent.triangle_protocol import validate_delta_against_git_diff
            violations.extend(validate_delta_against_git_diff(delta_report, workdir))
        if violations:
            violation_str = "; ".join(violations)
            logger.warning("run_review: pre-validation failed for {}: {}", feature_id, violation_str)
            return (
                f"[review: unknown]\n预校验失败，未启动 Reviewer LLM：\n{violation_str}",
                None,
            )

    # Truncate feature_description to 2000 chars
    desc = feature_description
    if len(desc) > 2000:
        desc = desc[:2000] + "... (truncated)"

    # === BUILD PROMPT ===
    prompt = (
        f"请审查功能: {feature_id}\n\n"
        f"功能描述: {desc}\n"
    )
    if scope_hint:
        prompt += f"范围提示: {scope_hint}\n"
    if delta_report is not None:
        from loom.agent.triangle_protocol import serialize_delta_report
        prompt += "\n" + serialize_delta_report(delta_report) + "\n"
    prompt += (
        "\n请检查实现是否正确、完整，是否超出范围。\n"
        "使用只读工具检查代码。最终输出必须包含 <verdict>...</verdict> 标签。"
    )

    # === SPAWN REVIEWER ===
    try:
        result = spawn_subagent(prompt, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS, max_turns=15)
    except Exception as exc:
        logger.warning("run_review: LLM call failed: {}", exc)
        return (f"[review: unknown — LLM call failed: {exc}]", None)

    # === POST-PROCESSING: parse verdict + feedback_directive ===
    verdict = _parse_verdict(result)
    verdict_str = (
        f"[review: {verdict.status}]\n"
        f"{verdict.summary}\n"
        f"证据: {verdict.evidence}\n"
        f"建议: {verdict.recommendations}"
    )

    # parse_feedback_directive handles I6 internally:
    # - returns None if action list empty / invalid combination (§7.3 rules)
    # - returns None if block missing entirely
    from loom.agent.triangle_protocol import parse_feedback_directive
    fd = parse_feedback_directive(result)
    if fd is None:
        logger.warning("run_review: feedback_directive missing or invalid for {}", feature_id)

    # === Triangle Protocol: record review event (TP-3) ===
    attempt = _REVIEW_ATTEMPT_COUNTER.get(feature_id, 0) + 1
    _REVIEW_ATTEMPT_COUNTER[feature_id] = attempt
    import loom.agent.trace as trace_mod
    tr = trace_mod.current()
    if tr is not None:
        try:
            tr.record(
                trace_mod.TRIANGLE_REVIEW,
                feature_id=feature_id,
                role="reviewer",
                verdict_status=verdict.status,
                feedback_action=list(fd.action) if fd is not None else ["missing"],
                retry_review=fd.retry_review if fd is not None else False,
                attempt=attempt,
            )
        except Exception as trace_exc:
            logger.warning("run_review: trace.record(triangle.review) failed: {}", trace_exc)

    return (verdict_str, fd)


def run_review_legacy_str(feature_id: str, feature_description: str, scope_hint: str = "") -> str:
    """Backward-compat wrapper for callers expecting str return.

    Calls the new run_review and discards the FeedbackDirective.
    To be removed in TP-8.
    """
    verdict_str, _ = run_review(feature_id, feature_description, scope_hint)
    return verdict_str
