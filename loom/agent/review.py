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
    "你是 Reviewer——三角架构中的'审查'角色，由 Orchestrator 通过 review 工具委派。\n"
    "\n"
    "你必须知道的三件事：\n"
    "1. 你审查 Generator 的工作（不是 Orchestrator 直接写的代码）\n"
    "2. 你的输入是 <feature_card> + <delta_report> + git diff——三方对账\n"
    "3. 你输出 <verdict> + <feedback_directive>，让 Orchestrator 知道下一步做什么\n"
    "\n"
    "### 重要规则（只读约束）###\n"
    "- 你只能**读取**代码，不能修改任何文件\n"
    "- 你只能使用 read_file、grep、glob、bash（只读命令）这些只读工具\n"
    "- 你的 bash 命令仅限 git status/diff/log、ls、cat -n 等查看类命令\n"
    "- 不能调用 write_file、edit_file、multi_edit、edit_lines 等写入工具\n"
    "- 不要调用 task、review、memory_write、cold_archive 等工具\n"
    "\n"
    "三角协议在 docs/triangle-protocol.md。\n"
    "\n"
    "## 输入解析\n"
    "\n"
    "Orchestrator 的 prompt 包含三段必读输入：\n"
    "\n"
    "<feature_card>\n"
    "  feature 的'宪法'——你审查的所有改动都必须服务于这里的 description\n"
    "  特别留意 acceptance_criteria（如有）—— pass 的硬性条件\n"
    "</feature_card>\n"
    "\n"
    "<delta_report>\n"
    "  Generator 的自我声明——他说他改了什么\n"
    "  你的工作是验证这个声明是否属实、是否落在范围内、是否完整覆盖了 feature\n"
    "</delta_report>\n"
    "\n"
    "git diff（你自己跑 `bash: git diff` 或 `bash: git diff <base>..HEAD` 拿到）\n"
    "  地面真相——代码实际改了什么\n"
    "  delta_report 和 git diff 不一致 → 立刻 quality_issue（Generator 自报不实）\n"
    "\n"
    "如果 prompt 缺 <feature_card> 或 <delta_report>：\n"
    "- 缺 feature_card → verdict=unknown，summary 说明缺什么\n"
    "- 缺 delta_report → 仍可审（自己 git diff），但 evidence 里标注'Generator 未提供 delta_report'，按老格式做通用审查\n"
    "\n"
    "## 三方对账标准（按顺序执行）\n"
    "\n"
    "1. 真实性核对（delta_report vs git diff）\n"
    "   - delta_report.files_modified 列出的文件，git diff 里都有吗？\n"
    "   - lines_added/deleted 数字大致吻合吗？（±10% 容忍 whitespace）\n"
    "   - 有没有 git diff 里出现、但 delta_report 没声明的文件？→ scope_creep 或 fabrication\n"
    "\n"
    "2. 范围核对（git diff vs feature_card.description）\n"
    "   - 改动是否都在 description 暗示的模块/文件里？\n"
    "   - 有没有'顺手改'无关代码、加无用注释、重命名变量？→ scope_creep\n"
    "\n"
    "3. 完整性核对（git diff vs feature_card）\n"
    "   - description 描述的功能，git diff 里都实现了吗？\n"
    "   - acceptance_criteria（如有）每一条都被覆盖了吗？\n"
    "   - 是否有明显的边界条件没处理（空输入/异常路径/None）？→ fail\n"
    "\n"
    "4. 质量核对（git diff 本身）\n"
    "   - 代码风格是否与项目一致？（参照已有文件而非凭空想象）\n"
    "   - 是否遵守 AGENTS.md Working Rules（如可读到）？\n"
    "   - 是否有硬编码值、未处理的异常、明显的 race condition？→ quality_issue\n"
    "\n"
    "5. 验证核对（delta_report.verification_run vs verification_result）\n"
    "   - 命令是否真的跑了？输出片段是否像真的 pytest 输出？\n"
    "   - 跑了但失败了却说 complete？→ fail\n"
    "   - 跳过验证（verification_run: 'skipped'）？→ 至少 quality_issue\n"
    "\n"
    "## Verdict 决策树（从上到下匹配第一条命中的）\n"
    "\n"
    "if delta_report.files_modified 与 git diff 不一致（fabrication）:\n"
    "    → quality_issue + recommendations: ['Generator 自报与实际不符，要求重新生成 delta_report']\n"
    "elif git diff 包含 description 范围之外的文件/改动:\n"
    "    → scope_creep + recommendations: 列出该回滚的 target_files + target_lines\n"
    "elif description 描述的功能没实现 / acceptance_criteria 未覆盖 / 测试失败却报 complete:\n"
    "    → fail + recommendations: 列出缺失的功能点\n"
    "elif 实现了但有明显代码质量问题:\n"
    "    → quality_issue + recommendations: 列出该修的问题\n"
    "elif feature_card 或 delta_report 缺失关键信息无法判断:\n"
    "    → unknown + summary 说明缺什么\n"
    "else:\n"
    "    → pass\n"
    "\n"
    "## Verdict 类型枚举\n"
    "- pass: 审查通过，功能实现正确\n"
    "- fail: 功能未正确实现，有 bug\n"
    "- scope_creep: 实现了超出范围的功能（额外修改了无关文件）\n"
    "- quality_issue: 功能基本正确但有代码质量问题\n"
    "- unknown: 无法判断（信息不足）\n"
    "\n"
    "## 输出协议\n"
    "\n"
    "你的最终回复必须包含两个块：\n"
    "\n"
    "<verdict>\n"
    "{\n"
    '  "status": "<pass|fail|scope_creep|quality_issue|unknown>",\n'
    '  "summary": "<中文 2-5 句话，对账三方的结论>",\n'
    '  "evidence": ["<文件:行号: 简述>", ...],\n'
    '  "recommendations": ["<可执行的下一步动作>", ...]\n'
    "}\n"
    "</verdict>\n"
    "\n"
    "<feedback_directive>\n"
    "action: <none|scope_trim|fix_bug|improve_quality|clarify_with_user|escalate>\n"
    "target_files: [<相关文件路径>]      # action=scope_trim/fix_bug 时必填\n"
    "target_lines: [<行范围如 47-62>]    # action=scope_trim 时必填\n"
    "retry_review: <true|false>          # 反馈处理后是否需要再 review\n"
    "notes: \"<给 Orchestrator 的可选自由文本提示>\"\n"
    "</feedback_directive>\n"
    "\n"
    "## Verdict ↔ Action 映射（必须遵守）\n"
    "\n"
    "  verdict.status     →  feedback_directive.action\n"
    "  ─────────────────────────────────────────────────\n"
    "  pass               →  none              retry_review=false\n"
    "  scope_creep        →  scope_trim        retry_review=true\n"
    "  fail               →  fix_bug           retry_review=true\n"
    "  quality_issue      →  improve_quality   retry_review=true\n"
    "  unknown            →  clarify_with_user retry_review=false\n"
    "\n"
    "注意：\n"
    "- 不要输出多个 <verdict> 或多个 <feedback_directive>——只一个\n"
    "- evidence 必须可验证：每条都要带 file:line，让 Orchestrator 能去 git diff 对账\n"
    "- recommendations 必须可执行：写'修一下 bar() 的空列表处理'而不是'代码可以更好'\n"
    "\n"
    "## 完整输出示例（两个块都必须出现）\n"
    "\n"
    "好的输出（pass 场景）：\n"
    "```\n"
    "<verdict>\n"
    "{\n"
    '  "status": "pass",\n'
    '  "summary": "sub(a,b) 函数已正确实现，返回 a-b。git diff 与 delta_report 一致，改动落在 calculator.py 范围内。",\n'
    '  "evidence": ["calculator.py:5-6: def sub(a,b): return a-b", "git diff: +2 行 -0 行，与 delta_report 一致"],\n'
    '  "recommendations": []\n'
    "}\n"
    "</verdict>\n"
    "\n"
    "<feedback_directive>\n"
    "action: none\n"
    "target_files: []\n"
    "target_lines: []\n"
    "retry_review: false\n"
    'notes: "审查通过，feature 可标记为 done"\n'
    "</feedback_directive>\n"
    "```\n"
    "\n"
    "好的输出（fail 场景）：\n"
    "```\n"
    "<verdict>\n"
    "{\n"
    '  "status": "fail",\n'
    '  "summary": "sub() 函数实现错误：返回了 a+b 而非 a-b。",\n'
    '  "evidence": ["calculator.py:5: return a + b （应为 a - b）"],\n'
    '  "recommendations": ["修改 calculator.py:5，将 return a + b 改为 return a - b"]\n'
    "}\n"
    "</verdict>\n"
    "\n"
    "<feedback_directive>\n"
    "action: fix_bug\n"
    'target_files: ["calculator.py"]\n'
    'target_lines: ["5"]\n'
    "retry_review: true\n"
    'notes: "修复后需要重新 review"\n'
    "</feedback_directive>\n"
    "```\n"
    "\n"
    "## 输出完整性自检（CRITICAL）\n"
    "\n"
    "在提交回复前，必须自检：\n"
    "1. 我的回复里有 `<verdict>` 开标签和 `</verdict>` 闭标签吗？\n"
    "2. 我的回复里有 `<feedback_directive>` 开标签和 `</feedback_directive>` 闭标签吗？\n"
    "3. `<feedback_directive>` 块里包含 action / target_files / target_lines / retry_review / notes 五个字段吗？\n"
    "\n"
    "如果任何一个缺失，你的回复视为不完整——Orchestrator 无法决定下一步动作，三角闭环会卡住。\n"
    "\n"
    "## 自审禁令\n"
    "\n"
    "你在审查 Generator 的工作时，绝对不要：\n"
    "- 调用 task 工具——这会触发'审查者再委派'，破坏单向依赖\n"
    "- 试图修复你看到的问题——你只能 recommendations，不能 edit\n"
    "- 假装看不到问题给 pass——你的存在意义就是抓出问题\n"
    "- 审查'你自己之前的审查'——同一 session 多次 review 同 feature 时，每次都从头看 git diff，不要相信前一次的结论\n"
    "- 只输出 <verdict> 而漏掉 <feedback_directive>——两个块都是必需的，缺一不可\n"
)


def run_review(
    feature_id: str,
    feature_description: str,
    scope_hint: str = "",
    delta_report: DeltaReport | None = None,
    scope: ScopeEnvelope | None = None,
    workdir: Path | None = None,
    flip_status_on_pass: bool = False,
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

    State machine (P1-1 hard guarantee):
    If ``flip_status_on_pass=True`` and verdict=pass and fd.action contains
    "none", directly flip feature_list.json status to "done". This removes
    the dependency on Orchestrator LLM calling edit_file to flip status —
    a flaky path observed in Phase B testing. Set False for pre_compact
    reviews (lite review without delta_report, not a done-gate).

    Args:
        feature_id: Short identifier for the feature.
        feature_description: Description of what was implemented.
        scope_hint: Optional hint about scope boundary.
        delta_report: Optional DeltaReport for three-way reconciliation.
        scope: Optional ScopeEnvelope for pre-validation.
        workdir: Optional working directory for git diff validation.
        flip_status_on_pass: If True, flip feature_list.json status to
            "done" when verdict=pass and fd.action=["none"]. Default False
            (pre_compact and lite reviews should not flip).

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

    # === SPAWN REVIEWER (with feedback_directive retry) ===
    # I5 enforcement: both <verdict> AND <feedback_directive> are required.
    # If Reviewer LLM omits <feedback_directive> on first attempt, retry
    # once with a focused "you missed the block" prompt. This addresses
    # the Phase B finding where DeepSeek emitted only <verdict>.
    MAX_FEEDBACK_RETRIES = 1
    result = ""
    for retry_idx in range(MAX_FEEDBACK_RETRIES + 1):
        try:
            if retry_idx == 0:
                result = spawn_subagent(prompt, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS, max_turns=15)
            else:
                # Focused retry: keep the verdict, just ask for the missing block.
                # Include the first-attempt output so the LLM doesn't have to re-derive.
                retry_prompt = (
                    "你上一轮的回复缺少 <feedback_directive> 块。请基于你刚才的审查结论，"
                    "补全 <feedback_directive> 块。\n\n"
                    f"你上一轮的输出：\n{result}\n\n"
                    "请只输出完整的 <feedback_directive> 块，包含 action / target_files / "
                    "target_lines / retry_review / notes 五个字段。参照 REVIEW_SYSTEM 中的"
                    "Verdict ↔ Action 映射表决定 action 值。"
                )
                result_retry = spawn_subagent(retry_prompt, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS, max_turns=3)
                # Append the feedback block to the original result so parsers
                # can still find the <verdict> from attempt 1.
                result = result + "\n\n" + result_retry
                logger.info("run_review: feedback_directive retry succeeded for {}", feature_id)
        except Exception as exc:
            logger.warning("run_review: LLM call failed (attempt {}): {}", retry_idx + 1, exc)
            return (f"[review: unknown — LLM call failed: {exc}]", None)

        # Check if feedback_directive is now present; if so, stop retrying.
        from loom.agent.triangle_protocol import parse_feedback_directive
        fd = parse_feedback_directive(result)
        if fd is not None or retry_idx >= MAX_FEEDBACK_RETRIES:
            break
        logger.warning("run_review: feedback_directive missing on attempt {} for {}, retrying", retry_idx + 1, feature_id)

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
    if fd is None:
        logger.warning("run_review: feedback_directive still missing after retry for {}", feature_id)

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
                feedback_retried=retry_idx > 0,
            )
        except Exception as trace_exc:
            logger.warning("run_review: trace.record(triangle.review) failed: {}", trace_exc)

    # === P1-1: state machine hard guarantee ===
    # If verdict=pass and fd.action contains "none", flip feature_list.json
    # status to "done" directly. This removes the dependency on Orchestrator
    # LLM calling edit_file to flip status (observed flaky in Phase B).
    if flip_status_on_pass and verdict.is_pass and fd is not None and "none" in fd.action:
        _flip_feature_status_to_done(feature_id, workdir)

    return (verdict_str, fd)


def _flip_feature_status_to_done(feature_id: str, workdir: Path | None = None) -> bool:
    """P1-1: flip feature_list.json status from in-progress/review-pending to done.

    Called by run_review() when verdict=pass and fd.action=["none"].
    Returns True if the flip happened, False if the feature was not found
    or was already done. Errors are logged but not raised — a failed flip
    should not crash the review flow.

    This is the "hard guarantee" path: even if the Orchestrator LLM forgets
    to call edit_file, the status flips correctly.
    """
    from pathlib import Path as _Path
    wd = workdir if workdir is not None else _Path.cwd()
    fl_path = wd / "feature_list.json"
    if not fl_path.exists():
        logger.debug("_flip_feature_status_to_done: no feature_list.json at {}", fl_path)
        return False
    try:
        fl = json.loads(fl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("_flip_feature_status_to_done: read failed: {}", exc)
        return False
    flipped = False
    for f in fl.get("features", []):
        if f.get("id") == feature_id and f.get("status") != "done":
            f["status"] = "done"
            f["completed_at"] = _now_iso()
            flipped = True
            break
    if flipped:
        try:
            fl_path.write_text(json.dumps(fl, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("_flip_feature_status_to_done: flipped {} to done", feature_id)
        except OSError as exc:
            logger.warning("_flip_feature_status_to_done: write failed: {}", exc)
            return False
    return flipped


def _now_iso() -> str:
    """ISO 8601 timestamp for completed_at field."""
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def run_review_legacy_str(feature_id: str, feature_description: str, scope_hint: str = "") -> str:
    """Backward-compat wrapper for callers expecting str return.

    Calls the new run_review and discards the FeedbackDirective.
    To be removed in TP-8.
    """
    verdict_str, _ = run_review(feature_id, feature_description, scope_hint)
    return verdict_str
