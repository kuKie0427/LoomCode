"""Tests for Triangle Protocol orchestrator prompt upgrade (TP-4).

Verifies:
- New static section contains the three triangle roles (Orchestrator/Generator/Reviewer)
- Delegation decision rules are present
- Feedback loop with feedback_directive.action is present
- PreCompact system-reminder recognition rule is present
- Retry safety bound (≥3 attempts) is present
- All 8 legacy rules are preserved (dual-mode)
- New section length is within plan budget (≤1500 chars)
"""

from __future__ import annotations

import re
from pathlib import Path

from loom.agent.system_prompt import build_fresh


def _prompt(tmp_path: Path) -> str:
    """Build the system prompt with empty working dir (avoids AGENTS.md injection)."""
    return build_fresh(tmp_path)


def test_orchestrator_prompt_mentions_triangle_roles(tmp_path: Path) -> None:
    """The three triangle roles (Orchestrator/Generator/Reviewer) are named in the prompt."""
    p = _prompt(tmp_path)
    assert "编排者" in p or "Orchestrator" in p
    assert "Generator" in p
    assert "Reviewer" in p


def test_orchestrator_prompt_mentions_delegation_decision(tmp_path: Path) -> None:
    """Delegation decision rule is present (when to delegate vs do it yourself)."""
    p = _prompt(tmp_path)
    # P0-1: delegation hard constraint section (replaces old "何时委派给 Generator")
    assert "委派硬约束" in p
    assert "何时自己做" in p
    assert "何时调 Reviewer" in p


def test_orchestrator_prompt_mentions_feedback_loop(tmp_path: Path) -> None:
    """Feedback loop table with feedback_directive.action is present."""
    p = _prompt(tmp_path)
    assert "feedback_directive.action" in p
    # All 6 action types are listed
    for action in ("none", "scope_trim", "fix_bug", "improve_quality", "clarify_with_user", "escalate"):
        assert action in p, f"missing action type: {action}"


def test_orchestrator_prompt_mentions_pre_compact_recognition(tmp_path: Path) -> None:
    """PreCompact system-reminder recognition rule is present."""
    p = _prompt(tmp_path)
    assert "[system-reminder] PreCompact review verdict" in p


def test_orchestrator_prompt_mentions_retry_safety_bound(tmp_path: Path) -> None:
    """I9 retry safety bound: ≥3 attempts → force escalate."""
    p = _prompt(tmp_path)
    assert "≥ 3" in p or "3 次" in p
    # Both the bound AND the escalation consequence should appear
    assert "escalate" in p


def test_orchestrator_prompt_legacy_rules_preserved(tmp_path: Path) -> None:
    """Dual-mode: all 8 original rules still present in prompt.

    Verifies that adding the new Triangle Protocol section did NOT remove
    or rewrite the existing legacy rules. Each rule is identified by a
    distinctive keyword phrase that the original prompt contained.
    """
    p = _prompt(tmp_path)
    expected_keywords = [
        "行为准则",  # behavior guidelines
        "完成标准",  # completion criteria
        "审查优先",  # review-first
        "阅读优先",  # read-first
        "不可逆操作",  # irreversible operations
        "URL",  # URL policy
        "语言风格",  # language style
        "Tool Failure Handling",  # tool failure handling
    ]
    for kw in expected_keywords:
        assert kw in p, f"legacy rule keyword missing: {kw!r}"


def test_triangle_section_within_length_budget(tmp_path: Path) -> None:
    """New section is within plan budget (≤1500 chars).

    Plan R4 revised the original 800-char cap to 1500 because the four
    sub-sections (identity + delegation + feedback + PreCompact) cannot
    be compressed further. If this test starts failing, the section has
    grown past the budget and needs trimming (not 'just one more example').
    """
    p = _prompt(tmp_path)
    # Extract the Triangle Protocol section. It starts with "你是 LoomCode 编排者"
    # (orchestrator identity line) and continues to the end of the static block.
    # The static block ends at the first blank line + "---" boundary marker
    # before the dynamic session section.
    start = p.find("你是 LoomCode 编排者")
    assert start != -1, "Triangle Protocol section not found in prompt"
    # Find the end of the static block — either end of string or first
    # boundary marker after the start
    end_search = p[start:]
    end = end_search.find("\n\n---\n\n")
    if end == -1:
        section = end_search
    else:
        section = end_search[:end]
    assert len(section) <= 1500, (
        f"Triangle section {len(section)} chars exceeds 1500 budget"
    )
