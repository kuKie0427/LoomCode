"""Diagnostic test: verify StreamingOverlay width during/after streaming.

Reproduces the "every character on its own line" bug by mounting a
StreamingOverlay with long Chinese text and checking the rendered
width / line count.

The bug: StreamingOverlay inherits Static, whose DEFAULT_CSS has only
``height: auto`` and no width — Static defaults to ``width: auto``
(shrink-to-content). Even with ``width: 1fr`` in StreamingOverlay's
DEFAULT_CSS, there may be a timing window where the overlay is
updated before mount/layout completes, causing width collapse.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from loom.tui.chat_log import ChatLog


class _ChatLogApp(App):
    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


async def _drain(pilot, ticks: int = 10, interval: float = 0.05) -> None:
    for _ in range(ticks):
        await pilot.pause(interval)


def test_streaming_overlay_width_matches_terminal():
    """StreamingOverlay width should be terminal width (not content width).

    With width: 1fr, the overlay should fill the ChatLog content area
    (80 cols - 4 padding = 76). If width collapses to content width,
    word-wrap will break aggressively.
    """

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            # Append a long Chinese string (no newlines — should be one line)
            long_text = "JS已创建（21566字节✅），CSS还没生成。我来直接创建CSS：" * 3
            chat_log.append_streaming_text(long_text)
            await _drain(pilot)

            overlay = chat_log._current_overlay
            assert overlay is not None, "StreamingOverlay not mounted"

            # Check virtual_size — width should be ~76 (80 - 4 padding)
            # NOT the content width (which would be ~120 for the long text)
            overlay_width = overlay.virtual_size.width
            chatlog_width = chat_log.content_size.width

            print(f"\n[diag] terminal=80 chatlog_content={chatlog_width} "
                  f"overlay_virtual={overlay_width}")

            # The overlay should be at least as wide as the chatlog content area
            # (it has width: 1fr). If it's much narrower, width collapsed.
            assert overlay_width >= chatlog_width - 4, (
                f"StreamingOverlay width collapsed: overlay={overlay_width}, "
                f"chatlog_content={chatlog_width}. Expected overlay to fill "
                f"parent (width: 1fr). This causes aggressive word-wrap "
                f"(every-few-chars line break)."
            )

    asyncio.run(driver())


def test_streaming_overlay_chinese_no_per_char_wrap():
    """Long Chinese text should NOT wrap per-character.

    If the overlay width is correct (1fr), a 60-char Chinese string
    should render in 1-2 lines (depending on terminal width). If width
    collapsed, it renders in 60+ lines (one char per line).
    """

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            # 60 chars of Chinese text, no spaces, no newlines
            text = "已创建字节还没生成我来直接创建文件内容代码测试" * 2
            chat_log.append_streaming_text(text)
            await _drain(pilot)

            overlay = chat_log._current_overlay
            assert overlay is not None

            overlay_height = overlay.virtual_size.height
            overlay_width = overlay.virtual_size.width

            print(f"\n[diag] text_len={len(text)} overlay={overlay_width}x{overlay_height}")

            # With width=76 (80-4), 60 Chinese chars ≈ 60 cols → 1 line
            # With width=76, 120 Chinese chars ≈ 120 cols → 2 lines
            # If height > 10, width definitely collapsed (per-char wrap)
            assert overlay_height <= 5, (
                f"Overlay height={overlay_height} for {len(text)} chars — "
                f"suggests per-character wrapping (width collapsed to "
                f"~1-2 cols). Expected 1-2 lines with width: 1fr."
            )

    asyncio.run(driver())


def test_streaming_overlay_preserves_newlines_in_structured_content():
    """Structured content (lists, etc.) should preserve newlines.

    _normalize_for_stream keeps \n if any line is structured (list, header,
    table, etc.). This is correct behavior — but verify it doesn't
    accidentally trigger for plain Chinese paragraphs.
    """

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            # Plain Chinese text with single \n — should be joined with spaces
            text = "JS已创建\nCSS还没生成\n我来直接创建"
            chat_log.append_streaming_text(text)
            await _drain(pilot)

            overlay = chat_log._current_overlay
            assert overlay is not None

            # After _normalize_for_stream, \n → space, so no newlines in content
            # The overlay should show 1 line (text is ~25 chars, fits in 76 cols)
            overlay_height = overlay.virtual_size.height
            print(f"\n[diag] plain text height={overlay_height}")
            assert overlay_height <= 2, (
                f"Plain Chinese text with \\n should be joined to 1 line, "
                f"got height={overlay_height}. _normalize_for_stream may "
                f"have failed to merge newlines."
            )

    asyncio.run(driver())


if __name__ == "__main__":
    test_streaming_overlay_width_matches_terminal()
    print("✓ width matches terminal")
    test_streaming_overlay_chinese_no_per_char_wrap()
    print("✓ no per-char wrap")
    test_streaming_overlay_preserves_newlines_in_structured_content()
    print("✓ structured content preserved")
