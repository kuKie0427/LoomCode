"""Review tool — spawn a read-only subagent to inspect code and verify quality.

Provides REVIEW_SYSTEM (Chinese review agent prompt), ReviewVerdict
dataclass, _parse_verdict(), and run_review() for the ``review`` tool.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from loguru import logger

VerdictStatus = Literal["pass", "fail", "scope_creep", "quality_issue", "unknown"]


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


def run_review(feature_id: str, feature_description: str, scope_hint: str = "") -> str:
    """Run a code review for the given feature.

    Spawns a read-only subagent with REVIEW_SYSTEM + REVIEW_TOOLS, parses
    the ``<verdict>`` from its output, and returns a structured result string.

    Args:
        feature_id: Short identifier for the feature (e.g. "f-review-tool").
        feature_description: Description of what was implemented.
        scope_hint: Optional hint about what scope to check against.

    Returns:
        Formatted review result string.
    """
    # Lazy import to avoid circular dependency (tools.py imports from review.py)
    from loom.agent.tools import REVIEW_TOOLS, spawn_subagent

    # Truncate feature_description to 2000 chars
    desc = feature_description
    if len(desc) > 2000:
        desc = desc[:2000] + "... (truncated)"

    prompt = (
        f"请审查功能: {feature_id}\n\n"
        f"功能描述: {desc}\n"
    )
    if scope_hint:
        prompt += f"范围提示: {scope_hint}\n"
    prompt += (
        "\n请检查实现是否正确、完整，是否超出范围。\n"
        "使用只读工具检查代码。最终输出必须包含 <verdict>...</verdict> 标签。"
    )

    try:
        result = spawn_subagent(prompt, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS, max_turns=15)
    except Exception as exc:
        logger.warning("run_review: LLM call failed: {}", exc)
        return f"[review: unknown — LLM call failed: {exc}]"

    verdict = _parse_verdict(result)
    return (
        f"[review: {verdict.status}]\n"
        f"{verdict.summary}\n"
        f"证据: {verdict.evidence}\n"
        f"建议: {verdict.recommendations}"
    )
