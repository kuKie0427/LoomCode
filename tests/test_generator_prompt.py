"""Tests for Triangle Protocol Generator (SUB_SYSTEM) prompt upgrade (TP-5).

Verifies:
- Identity section names "Generator" role and mentions Reviewer will review
- Input parsing rules for <feature_card> and <scope_envelope> are present
- Output protocol mandates <delta_report> at the end
- "allow_paths is allowed-not-mandatory" example (R5) is present
- Legacy delegation craft, escalation markers, anti-patterns, honesty
  anchors are preserved (dual-mode — prompt-rewrite-subsystem-* eval cases
  assert these keywords)
- Length is within plan budget (≤5000 chars)
"""

from __future__ import annotations

from loom.agent.tools import SUB_SYSTEM


def _sub() -> str:
    return SUB_SYSTEM


def test_sub_system_mentions_generator_role() -> None:
    """Identity section names the 'Generator' role explicitly."""
    s = _sub()
    assert "Generator" in s
    # Plan: "三角中的'生成'角色" or similar — Generator must be tied to triangle
    assert "三角" in s or "Orchestrator" in s


def test_sub_system_mentions_reviewer_will_review() -> None:
    """Generator must be told its output will be reviewed by an independent Reviewer."""
    s = _sub()
    # Look for review-related language — "Reviewer", "审查", or "review"
    assert "Reviewer" in s or "审查" in s or "review" in s.lower()


def test_sub_system_mentions_feature_card_parsing() -> None:
    """<feature_card> input block parsing rule is present."""
    s = _sub()
    assert "<feature_card>" in s
    # Plan mandates the input parsing section header
    assert "输入解析" in s


def test_sub_system_mentions_scope_envelope_parsing() -> None:
    """<scope_envelope> input block parsing rule is present."""
    s = _sub()
    assert "<scope_envelope>" in s
    # Plan mandates mention of "deny_paths" and "allow_paths"
    assert "deny_paths" in s
    assert "allow_paths" in s


def test_sub_system_mentions_delta_report_output() -> None:
    """Output protocol mandates <delta_report> at the end of every reply."""
    s = _sub()
    assert "<delta_report>" in s
    # The "must end with this block" enforcement is the key contract
    assert "必须以 <delta_report>" in s or "必须以" in s and "<delta_report>" in s
    # Plan specifies the block should contain status + files_modified + verification
    assert "status:" in s
    assert "files_modified" in s
    assert "verification_run" in s
    assert "verification_result" in s


def test_sub_system_mentions_allow_paths_not_mandatory() -> None:
    """R5: explicit example that allow_paths is 'you MAY touch' not 'you MUST touch'.

    Guard against scope_creep: a Generator that reads 'allow_paths = [A, B]'
    and modifies both files would be wrong even though both are allowed.
    """
    s = _sub()
    # The example section must be present with a clear "wrong" example
    assert "例子" in s
    # The plan's specific wording: "allow_paths 是'你最多能碰这些'，不是'你必须碰这些'"
    assert "allow_paths" in s
    # The "❌ 错误" / "scope_creep" coupling must appear
    assert "scope_creep" in s or "错误" in s


def test_sub_system_legacy_rules_preserved() -> None:
    """Dual-mode: 4 anti-pattern categories + 4 escalation markers + honesty anchor preserved.

    These keywords are asserted by loom/eval/cases/prompt_rewrite_p0.py::
    PromptRewriteSubSystemDelegationCraft, PromptRewriteSubSystemEscalationMarkers,
    PromptRewriteSubSystemAntiPatterns, PromptRewriteSubSystemHonestyAnchor.
    """
    s = _sub()
    # Anti-patterns (from PromptRewriteSubSystemAntiPatterns)
    assert "反模式" in s
    assert "fabricate" in s or "编造" in s
    assert "silently skip" in s or "跳过的步骤" in s
    assert "skip/xfail" in s
    assert "destructive" in s or "rm -rf" in s
    # Escalation markers (from PromptRewriteSubSystemEscalationMarkers)
    for marker in ("[UNCLEAR", "[BLOCKED", "[CANNOT_FIX", "[OUT_OF_SCOPE"):
        assert marker in s, f"missing escalation marker: {marker}"
    # Honesty anchor (from PromptRewriteSubSystemHonestyAnchor)
    assert "不谎报" in s
    assert "没跑验证" in s
    # Delegation craft (from PromptRewriteSubSystemDelegationCraft)
    # Match the eval case's logic: "上下文独立" AND "不继承", OR "看不到" (any one suffices)
    context_indep = ("上下文独立" in s and "不继承" in s) or "看不到" in s
    assert context_indep, "context independence indicator missing"
    assert "grep" in s
    assert "read_file" in s
    assert "便宜" in s
    assert "改文件前先" in s
    assert "文件路径" in s
    assert "行号" in s


def test_sub_system_no_fabricate_delta_report_anti_pattern() -> None:
    """R1: anti-pattern '不要伪造 delta_report' is present in the 反模式 section.

    This guards against the specific failure mode where a Generator would
    report lines_added=5 when it actually changed 50 lines — Reviewer
    cross-checks via git diff, but the prompt must forbid it up front.
    """
    s = _sub()
    # Both keywords must co-occur
    assert "伪造" in s and "delta_report" in s
    # The enforcement mechanism (git diff) must be mentioned
    assert "git diff" in s


def test_sub_system_length_within_budget() -> None:
    """SUB_SYSTEM length is within plan budget (≤5000 chars).

    Plan C1 revised the original 4000-char cap to 5000 because the new
    input parsing + output protocol sections are necessary for Triangle
    Protocol compliance. If this test starts failing, the prompt has
    grown past the budget and needs trimming.
    """
    s = _sub()
    assert len(s) <= 5000, f"SUB_SYSTEM {len(s)} chars exceeds 5000 budget"
