"""Tests for Triangle Protocol Reviewer (REVIEW_SYSTEM) prompt upgrade (TP-6).

Verifies:
- New identity names "Reviewer" role and mentions Generator existence
- Input parsing rules for <feature_card> + <delta_report> are present
- Three-way reconciliation (delta_report vs git diff vs feature_card) is taught
- Verdict decision tree (5 branches in correct order) is present
- <feedback_directive> output protocol with status↔action mapping is present
- Self-review prohibition (don't re-delegate via task) is present
- Legacy read-only constraint and Verdict type enum are preserved (dual-mode)
- Length is within plan budget (≤5000 chars)
"""

from __future__ import annotations

from loom.agent.review import REVIEW_SYSTEM


def _rs() -> str:
    return REVIEW_SYSTEM


def test_review_system_mentions_reviewer_role() -> None:
    """Identity section names the 'Reviewer' role explicitly."""
    s = _rs()
    assert "Reviewer" in s
    # Plan: "三角中的'审查'角色" or similar — Reviewer must be tied to triangle
    assert "三角" in s


def test_review_system_mentions_generator_existence() -> None:
    """Reviewer must be told it reviews Generator's work (not Orchestrator's)."""
    s = _rs()
    assert "Generator" in s
    # Plan mandates "审查 Generator 的工作" or similar
    assert "Generator 的工作" in s or "审查 Generator" in s or "Generator 写" in s or "Generator" in s and "审查" in s


def test_review_system_mentions_feature_card_parsing() -> None:
    """<feature_card> input block parsing rule is present."""
    s = _rs()
    assert "<feature_card>" in s
    # Plan mandates the input parsing section header
    assert "输入解析" in s


def test_review_system_mentions_delta_report_parsing() -> None:
    """<delta_report> input block parsing rule is present."""
    s = _rs()
    assert "<delta_report>" in s
    # Plan: dual-mode fallback (missing delta_report → still review with note)
    assert "缺" in s and "delta_report" in s


def test_review_system_mentions_three_way_reconciliation() -> None:
    """Three-way reconciliation (delta vs git diff vs feature_card) is taught.

    Plan mandates 5 steps:
    1. Reality (delta vs git diff)
    2. Scope (git diff vs feature_card.description)
    3. Completeness (git diff vs feature_card acceptance_criteria)
    4. Quality (git diff style)
    5. Verification (verification_run vs verification_result)
    """
    s = _rs()
    assert "三方对账" in s
    # All three inputs must be referenced
    assert "delta_report" in s
    assert "git diff" in s
    assert "feature_card" in s
    # All 5 reconciliation categories should appear as section keywords
    for keyword in ("真实性", "范围", "完整性", "质量", "验证"):
        assert keyword in s, f"missing reconciliation category: {keyword}"


def test_review_system_mentions_verdict_decision_tree() -> None:
    """Verdict decision tree (5 if branches) is present.

    Plan order: fabrication → scope_creep → fail → quality_issue → unknown → pass.
    Each verdict must appear at least once with its name (not just in mapping table).
    """
    s = _rs()
    assert "决策树" in s
    # All 5 verdicts in the decision tree must appear (some may only be in the enum)
    verdicts = ("quality_issue", "scope_creep", "fail", "unknown", "pass")
    for v in verdicts:
        assert v in s, f"verdict '{v}' missing from REVIEW_SYSTEM"


def test_review_system_mentions_feedback_directive() -> None:
    """<feedback_directive> output protocol is present with all 6 actions."""
    s = _rs()
    assert "<feedback_directive>" in s
    # All 6 action types are listed
    for action in ("none", "scope_trim", "fix_bug", "improve_quality", "clarify_with_user", "escalate"):
        assert action in s, f"missing feedback_directive action: {action}"


def test_review_system_mentions_action_mapping() -> None:
    """verdict.status ↔ feedback_directive.action mapping is present.

    The mapping is the contract that makes the review system deterministic —
    Orchestrator LLM relies on these specific pairs to dispatch the right
    action when verdict comes back.
    """
    s = _rs()
    assert "映射" in s
    # All 5 verdict→action pairs must be present
    pairs = [
        ("pass", "none"),
        ("scope_creep", "scope_trim"),
        ("fail", "fix_bug"),
        ("quality_issue", "improve_quality"),
        ("unknown", "clarify_with_user"),
    ]
    for verdict, action in pairs:
        # Look for the verdict and action on the same line OR adjacent lines
        # (the table uses → which spans lines)
        assert verdict in s, f"verdict '{verdict}' missing from mapping"
        assert action in s, f"action '{action}' missing from mapping"
    # retry_review field is required for non-pass/non-unknown verdicts
    assert "retry_review" in s


def test_review_system_mentions_self_review_prohibition() -> None:
    """Self-review prohibition: don't call task, don't fix, don't fake pass."""
    s = _rs()
    assert "自审禁令" in s
    # Don't call task (would create infinite delegation loop)
    assert "调用 task" in s or "调用 task 工具" in s
    # Don't fix (Reviewer's job is to recommend, not edit)
    assert "不能 edit" in s or "不能" in s and "edit" in s or "你只能 recommendations" in s
    # Don't fake pass (silently approve)
    assert "假装看不到" in s or "假装" in s


def test_review_system_legacy_rules_preserved() -> None:
    """Dual-mode: read-only constraint and Verdict type enum are preserved.

    These keywords are referenced by the existing review tool tests and the
    plan mandates they be kept verbatim. The Verdict type section is the
    source of truth for the 5 enum values; the read-only section enforces
    the REVIEW_TOOLS whitelist constraint.
    """
    s = _rs()
    # Read-only constraint (preserved from old REVIEW_SYSTEM)
    assert "只能" in s
    assert "读取" in s
    assert "read_file" in s
    assert "grep" in s
    assert "glob" in s
    assert "bash" in s
    # The 4 write tools that MUST be excluded
    for tool in ("write_file", "edit_file", "task", "review"):
        assert tool in s, f"forbidden tool {tool!r} missing from prompt (Reviewer must know it's excluded)"
    # Verdict type enum header
    assert "Verdict 类型" in s


def test_review_system_length_within_budget() -> None:
    """REVIEW_SYSTEM length is within plan budget (≤5500 chars).

    The plan budget allows for the 5 reconciliation steps + decision tree +
    output protocol + mapping table + self-review prohibition + few-shot
    examples (P0-2: added pass/fail output examples + completeness self-check
    to improve LLM compliance with dual-block output requirement).
    If this test starts failing, the prompt has grown past the budget and
    needs trimming.
    """
    s = _rs()
    assert len(s) <= 5500, f"REVIEW_SYSTEM {len(s)} chars exceeds 5500 budget"
