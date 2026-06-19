"""Unit tests for chat_log.py P0 helpers.

Covers:
- `_is_structured_line` — Markdown structure detection
- `_normalize_for_stream` — single-newline → space conversion with structure preservation
- `_has_markdown_syntax` — fast-path regex for plain-text skip
- `ChatLog.watch_scroll_y` — sticky scroll state machine
"""

from unittest.mock import PropertyMock, patch

import pytest

from loom.tui.chat_log import (
    ChatLog,
    _has_markdown_syntax,
    _is_structured_line,
    _normalize_for_stream,
)


class TestIsStructuredLine:
    def test_empty_line(self):
        assert _is_structured_line("") is False
        assert _is_structured_line("   ") is False

    def test_table_row(self):
        assert _is_structured_line("| Name | Age |") is True
        assert _is_structured_line("  | indented |") is True

    def test_header(self):
        assert _is_structured_line("# H1") is True
        assert _is_structured_line("### H3") is True

    def test_blockquote(self):
        assert _is_structured_line("> quoted") is True

    def test_fenced_code_block(self):
        assert _is_structured_line("```python") is True
        assert _is_structured_line("```") is True

    def test_unordered_list(self):
        assert _is_structured_line("- item") is True
        assert _is_structured_line("* item") is True
        assert _is_structured_line("+ item") is True

    def test_ordered_list(self):
        assert _is_structured_line("1. first") is True
        assert _is_structured_line("12. twelfth") is True

    def test_plain_text(self):
        assert _is_structured_line("regular text") is False
        assert _is_structured_line("我要读文档") is False

    def test_dash_without_space_not_a_list(self):
        assert _is_structured_line("-not a list") is False

    def test_digit_without_dot_not_a_list(self):
        assert _is_structured_line("1 not a list") is False
        assert _is_structured_line("123abc") is False


class TestNormalizeForStream:
    def test_no_newlines(self):
        assert _normalize_for_stream("hello world") == "hello world"

    def test_empty(self):
        assert _normalize_for_stream("") == ""

    def test_single_newlines_become_spaces(self):
        # Original bug: each word on its own line
        text = "我要\n读\n哪些\n文档？"
        assert _normalize_for_stream(text) == "我要 读 哪些 文档？"

    def test_double_newlines_preserved(self):
        text = "para1\n\npara2"
        assert _normalize_for_stream(text) == "para1\n\npara2"

    def test_mixed_paragraphs(self):
        text = "我要\n读\n文档？\n\n如果要全部读完。"
        expected = "我要 读 文档？\n\n如果要全部读完。"
        assert _normalize_for_stream(text) == expected

    def test_table_preserves_newlines(self):
        text = "| Name | Age |\n| Alice | 30 |\n| Bob | 25 |"
        assert _normalize_for_stream(text) == text

    def test_unordered_list_preserves_newlines(self):
        text = "- item1\n- item2\n- item3"
        assert _normalize_for_stream(text) == text

    def test_ordered_list_preserves_newlines(self):
        text = "1. first\n2. second\n3. third"
        assert _normalize_for_stream(text) == text

    def test_header_preserves_newlines(self):
        text = "# Title\nContent here"
        assert _normalize_for_stream(text) == text

    def test_code_block_preserves_newlines(self):
        text = "```python\nprint(1)\n```"
        assert _normalize_for_stream(text) == text

    def test_blockquote_preserves_newlines(self):
        text = "> quote\n> continued"
        assert _normalize_for_stream(text) == text

    def test_drops_empty_lines_in_plain_paragraph(self):
        text = "first\n\nsecond"
        assert _normalize_for_stream(text) == "first\n\nsecond"


class TestHasMarkdownSyntax:
    def test_plain_text(self):
        assert _has_markdown_syntax("hello world") is False
        assert _has_markdown_syntax("我要读文档") is False

    def test_paragraph_break(self):
        assert _has_markdown_syntax("para1\n\npara2") is True

    def test_header(self):
        assert _has_markdown_syntax("# Title") is True

    def test_bold_marker(self):
        assert _has_markdown_syntax("**bold**") is True
        assert _has_markdown_syntax("*italic*") is True

    def test_inline_code(self):
        assert _has_markdown_syntax("`code`") is True

    def test_table_pipe(self):
        assert _has_markdown_syntax("| a | b |") is True

    def test_link_bracket(self):
        assert _has_markdown_syntax("[link](url)") is True

    def test_blockquote(self):
        assert _has_markdown_syntax("> quote") is True

    def test_dash(self):
        assert _has_markdown_syntax("- item") is True

    def test_underscore(self):
        assert _has_markdown_syntax("_emphasis_") is True

    def test_tilde(self):
        assert _has_markdown_syntax("~strike~") is True

    def test_only_first_500_chars_checked(self):
        prefix = "x" * 500
        assert _has_markdown_syntax(prefix + "# header at offset 500") is False
        assert _has_markdown_syntax(prefix[:499] + "# header at 499") is True


class TestWatchScrollY:
    @pytest.fixture
    def log(self):
        return ChatLog()

    def test_scroll_up_disables_sticky(self, log):
        log._sticky = True
        with patch.object(ChatLog, "is_vertical_scroll_end", new_callable=PropertyMock) as m:
            m.return_value = False
            log.watch_scroll_y(100.0, 50.0)
        assert log._sticky is False

    def test_scroll_to_bottom_enables_sticky(self, log):
        log._sticky = False
        with patch.object(ChatLog, "is_vertical_scroll_end", new_callable=PropertyMock) as m:
            m.return_value = True
            log.watch_scroll_y(50.0, 100.0)
        assert log._sticky is True

    def test_scroll_down_not_to_bottom_keeps_sticky_false(self, log):
        log._sticky = False
        with patch.object(ChatLog, "is_vertical_scroll_end", new_callable=PropertyMock) as m:
            m.return_value = False
            log.watch_scroll_y(50.0, 70.0)
        assert log._sticky is False

    def test_no_scroll_change_preserves_sticky(self, log):
        log._sticky = True
        log.watch_scroll_y(100.0, 100.0)
        assert log._sticky is True

        log._sticky = False
        log.watch_scroll_y(100.0, 100.0)
        assert log._sticky is False

    def test_initial_state_is_sticky(self, log):
        assert log._sticky is True

    def test_scroll_up_then_back_to_bottom_re_enables(self, log):
        log._sticky = True
        with patch.object(ChatLog, "is_vertical_scroll_end", new_callable=PropertyMock) as m:
            m.return_value = False
            log.watch_scroll_y(100.0, 30.0)
        assert log._sticky is False
        with patch.object(ChatLog, "is_vertical_scroll_end", new_callable=PropertyMock) as m:
            m.return_value = True
            log.watch_scroll_y(30.0, 100.0)
        assert log._sticky is True
