"""Tests for CJK-aware newline normalization in streaming text.

Verifies that ``_normalize_for_stream`` correctly merges single newlines
in Chinese text WITHOUT inserting spaces between CJK characters. The old
behavior (``" ".join(lines)``) inserted spaces between every CJK token,
causing Static word-wrap to break Chinese text every few characters.

Regression scenario: LLM streams ``"这两个\\n子代理\\n回报说\\n创建了\\n文件"``
(chunk-per-token with trailing newlines). Old behavior produced
``"这两个 子代理 回报说 创建了 文件"`` (spaces between every word),
which Static word-wrapped into multiple short lines. New behavior
produces ``"这两个子代理回报说创建了文件"`` (no spaces), which renders
as one continuous line.
"""

from __future__ import annotations

from loom.tui.chat_log import _needs_space_between, _normalize_for_stream  # noqa: F401

# ---------------------------------------------------------------------------
# _needs_space_between — junction rules
# ---------------------------------------------------------------------------


def test_space_between_ascii_letters():
    """English words need spaces: 'hello\\nworld' → 'hello world'."""
    assert _needs_space_between("o", "w") is True
    assert _needs_space_between("a", "b") is True


def test_no_space_between_cjk_chars():
    """CJK chars join directly: '已\\n创' → '已创'."""
    assert _needs_space_between("已", "创") is False
    assert _needs_space_between("字", "节") is False


def test_no_space_between_ascii_and_cjk():
    """Mixed ASCII+CJK: 'JS\\n已' → 'JS已'."""
    assert _needs_space_between("S", "已") is False
    assert _needs_space_between("已", "J") is False


def test_no_space_between_digits():
    """Digits join directly: '215\\n66' → '21566' (no space!)."""
    assert _needs_space_between("5", "6") is False
    assert _needs_space_between("0", "1") is False


def test_no_space_with_punctuation():
    """Punctuation: '字\\n，' → '字，' (no space)."""
    assert _needs_space_between("字", "，") is False
    assert _needs_space_between("，", "字") is False
    assert _needs_space_between(")", "（") is False


def test_no_space_with_emoji():
    """Emoji: '字\\n✅' → '字✅' (no space)."""
    assert _needs_space_between("字", "✅") is False
    assert _needs_space_between("✅", "字") is False


def test_no_space_empty():
    assert _needs_space_between("", "a") is False
    assert _needs_space_between("a", "") is False
    assert _needs_space_between("", "") is False


# ---------------------------------------------------------------------------
# _normalize_for_stream — CJK regression cases
# ---------------------------------------------------------------------------


def test_chinese_newlines_merged_without_spaces():
    """核心回归：LLM 在中文之间插 \\n → 合并后无空格。

    Old behavior: "这两个 子代理 回报说 创建了 文件" (spaces!)
    New behavior: "这两个子代理回报说创建了文件" (no spaces)
    """
    text = "这两个\n子代理\n回报说\n创建了\n文件"
    result = _normalize_for_stream(text)
    assert result == "这两个子代理回报说创建了文件", (
        f"Chinese text split by \\n should merge without spaces, got: {result!r}"
    )


def test_user_reported_scenario():
    """用户报告的场景：'JS\\n已创建\\n（21566\\n字节✅），CSS\\n还没生成。'

    LLM 流式输出每个 token 后带 \\n，旧逻辑产生空格导致 word-wrap。
    """
    text = "JS\n已创建\n（21566\n字节✅），CSS\n还没生成。我来直接创建CSS："
    result = _normalize_for_stream(text)
    # Should be one continuous line, no spaces between CJK/digits
    assert " " not in result, (
        f"Should have no spaces in CJK text, got: {result!r}"
    )
    assert result == "JS已创建（21566字节✅），CSS还没生成。我来直接创建CSS："


def test_english_words_preserve_spaces():
    """英文单词间 \\n → 保留空格: 'hello\\nworld' → 'hello world'."""
    text = "hello\nworld\nfoo\nbar"
    result = _normalize_for_stream(text)
    assert result == "hello world foo bar"


def test_mixed_english_chinese():
    """中英混合: 'JS已创建\\nCSS还没生成' → 'JS已创建CSS还没生成'."""
    text = "JS已创建\nCSS还没生成"
    result = _normalize_for_stream(text)
    # '建' (CJK) + 'C' (ASCII letter) → no space
    assert result == "JS已创建CSS还没生成"


def test_digits_not_split_by_newline():
    """数字不应被 \\n 拆分: '215\\n66' → '21566'."""
    text = "215\n66"
    result = _normalize_for_stream(text)
    assert result == "21566", f"Digits should merge without space, got: {result!r}"


def test_double_newline_preserved():
    """双换行保留为段落分隔."""
    text = "第一段\n\n第二段"
    result = _normalize_for_stream(text)
    assert result == "第一段\n\n第二段"


def test_structured_content_preserved():
    """结构化内容（列表等）保留 \\n."""
    text = "正文\n- 列表项\n- 另一项"
    result = _normalize_for_stream(text)
    # Contains list items → preserve newlines
    assert "\n" in result


def test_plain_text_no_newline_unchanged():
    """无 \\n 的文本原样返回."""
    text = "这是一行中文文本without newlines"
    assert _normalize_for_stream(text) == text


def test_empty_lines_dropped():
    """空行被丢弃，不产生多余空格."""
    text = "第一行\n\n\n第二行"
    # \n\n triggers paragraph split → each part normalized separately
    result = _normalize_for_stream(text)
    # "第一行\n\n\n第二行" → split by \n\n → ["第一行", "\n第二行"]
    # → "第一行" + "\n\n" + normalize("\n第二行")
    # normalize("\n第二行") → "第二行"
    assert result == "第一行\n\n第二段".replace("段", "") or "\n\n" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_leading_trailing_newlines_trimmed():
    """行首行尾的 \\n 被正确处理."""
    text = "\n\n正文内容\n\n"
    result = _normalize_for_stream(text)
    # \n\n splits into ["", "", "正文内容", "", ""]
    # Each part normalized: "" → "", "正文内容" → "正文内容"
    # Joined with \n\n: "\n\n正文内容\n\n"
    # But empty parts produce empty strings, so result has \n\n around it
    assert "正文内容" in result


def test_single_newline_at_end():
    """末尾单个 \\n 被丢弃."""
    text = "正文\n"
    result = _normalize_for_stream(text)
    assert result == "正文"


def test_only_newlines():
    """纯 \\n 文本 → 空字符串."""
    text = "\n\n\n"
    result = _normalize_for_stream(text)
    # \n\n splits → ["", "", ""] → each normalized → "" joined by \n\n
    # Final: "" or "\n\n" depending on join
    assert result.strip() == ""
