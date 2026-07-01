"""Tests for P0-F: unfinished intent detection.

Verifies that ``detect_unfinished_intent`` correctly identifies the
"announced action but no tool_use" pattern (e.g. "我来直接创建 CSS："
with stop_reason=end_turn), and that ``build_unfinished_intent_guidance``
produces an effective system-reminder.

Covers:
- Match: text ending with intent declaration + colon, no tool_use
- No match: text with tool_use block (LLM did execute)
- No match: text ending without intent declaration
- No match: empty content / no text blocks
- No match: intent phrase in mid-paragraph (not at end)
- Guidance: escalation tone on attempt 2
- Guidance: includes matched tail verbatim
"""

from __future__ import annotations

from loom.agent.tool_errors import (
    MAX_UNFINISHED_INTENT_REMINDERS,
    build_unfinished_intent_guidance,
    detect_unfinished_intent,
)


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use(name: str = "bash") -> dict:
    return {"type": "tool_use", "id": "t1", "name": name, "input": {}}


# ---------------------------------------------------------------------------
# detect_unfinished_intent — positive cases
# ---------------------------------------------------------------------------


def test_match_simple_intent_colon():
    """经典 case：'我来直接创建 CSS：' 末尾，无 tool_use → 命中。"""
    content = [_text("JS 已创建（21566 字节 ✅），CSS 还没生成。我来直接创建 CSS：")]
    matched = detect_unfinished_intent(content)
    assert matched is not None
    assert "我来直接创建 CSS" in matched
    assert matched.rstrip().endswith("：") or matched.rstrip().endswith(":")


def test_match_various_intent_phrases():
    """覆盖多种意图触发词。"""
    cases = [
        "接下来我来创建 CSS：",
        "我现在就写代码：",
        "我将开始实现：",
        "让我先读一下文件：",
        "我马上调用 bash：",
        "然后我运行测试：",
        "下一步我修改 loop.py:",
        "下面我来重构：",
        "我准备调 write_file：",
    ]
    for tail in cases:
        content = [_text(f"前文…\n{tail}")]
        matched = detect_unfinished_intent(content)
        assert matched is not None, f"应匹配: {tail!r}"
        assert tail.strip().rstrip(":：") in matched or tail in matched


def test_match_multiple_text_blocks_only_last_matters():
    """多个 text block 时，只看最后一个非空的末尾。"""
    content = [
        _text("先做了一些分析。"),
        _text("JS 已创建。我来直接创建 CSS："),
    ]
    matched = detect_unfinished_intent(content)
    assert matched is not None
    assert "我来直接创建 CSS" in matched


# ---------------------------------------------------------------------------
# detect_unfinished_intent — negative cases
# ---------------------------------------------------------------------------


def test_no_match_when_tool_use_present():
    """LLM 真的调用了工具 → 不算未完成意图。"""
    content = [
        _text("我来直接创建 CSS："),
        _tool_use("write_file"),
    ]
    assert detect_unfinished_intent(content) is None


def test_no_match_when_no_intent_phrase():
    """末尾是普通陈述，无意图触发词。"""
    content = [_text("JS 已创建，21566 字节。文件保存成功。")]
    assert detect_unfinished_intent(content) is None


def test_no_match_when_intent_in_mid_paragraph():
    """意图词在段落中间，末尾是其他内容 → 不算未完成意图。"""
    content = [_text("我来直接创建 CSS：这是我的计划。首先读取需求，然后…")]
    assert detect_unfinished_intent(content) is None


def test_no_match_when_terminated_by_full_stop():
    """末尾是句号（完整陈述），不是冒号 → 不算未完成意图。

    '我来创建 CSS。' 是已完成陈述（可能 LLM 已经做完或决定不做），
    不是 '我来创建 CSS：' 这种待执行模式。
    """
    content = [_text("JS 已创建。我来直接创建 CSS。")]
    assert detect_unfinished_intent(content) is None


def test_no_match_empty_content():
    assert detect_unfinished_intent([]) is None


def test_no_match_only_empty_text():
    content = [_text("")]
    assert detect_unfinished_intent(content) is None


def test_no_match_no_text_blocks():
    """只有 thinking block 之类的非 text/tool_use。"""
    content = [{"type": "thinking", "thinking": "我来直接创建 CSS："}]
    assert detect_unfinished_intent(content) is None


# ---------------------------------------------------------------------------
# build_unfinished_intent_guidance
# ---------------------------------------------------------------------------


def test_guidance_includes_matched_tail():
    guidance = build_unfinished_intent_guidance("我来直接创建 CSS：", attempt=1)
    assert "我来直接创建 CSS：" in guidance
    assert "<system-reminder>" in guidance
    assert "</system-reminder>" in guidance
    assert "[unfinished_intent]" in guidance


def test_guidance_escalates_on_attempt_2():
    """第 2 次提醒应包含 [final_reminder] 升级语气。"""
    g1 = build_unfinished_intent_guidance("我来：", attempt=1)
    g2 = build_unfinished_intent_guidance("我来：", attempt=2)
    assert "[final_reminder]" not in g1
    assert "[final_reminder]" in g2
    assert "第 2 次" in g2


def test_max_reminders_constant():
    """防死循环：上限为 2 次。"""
    assert MAX_UNFINISHED_INTENT_REMINDERS == 2


# ---------------------------------------------------------------------------
# Integration: dict + dataclass block support
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    """Mimic an in-memory dataclass text block."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name: str) -> None:
        self.type = "tool_use"
        self.id = "t1"
        self.name = name
        self.input = {}


def test_match_with_dataclass_blocks():
    """支持 in-memory dataclass block（不只是 dict）。"""
    content = [_FakeTextBlock("JS 已创建。我来直接创建 CSS：")]
    matched = detect_unfinished_intent(content)
    assert matched is not None
    assert "我来直接创建 CSS" in matched


def test_no_match_with_dataclass_tool_use():
    content = [
        _FakeTextBlock("我来直接创建 CSS："),
        _FakeToolUseBlock("write_file"),
    ]
    assert detect_unfinished_intent(content) is None
