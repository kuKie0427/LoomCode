"""Tests that verify streaming text causes the ChatLog to auto-scroll.

The chat log receives incremental text via ``append_streaming_text`` as the
LLM streams output. Each call schedules a 50ms flush timer that updates the
overlay widget and calls ``scroll_end()`` (sticky). With truly incremental
streaming, the chat log must:

1. Grow ``max_scroll_y`` as content arrives (overflow happens).
2. Stay at the bottom via sticky scroll (``is_vertical_scroll_end`` is True).

Regression target: ``stream_iter`` was BATCH mode (all events at once after
stream completion), so the chat log never received incremental updates. The
auto-scroll behavior was never exercised in practice.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from loop.tui.chat_log import ChatLog, StreamingOverlay


class _ChatLogApp(App):
    """Minimal Textual app hosting only a ChatLog for scroll testing."""

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


async def _drain(pilot, ticks: int = 15, interval: float = 0.05) -> None:
    """Pause long enough for both the overlay mount and the flush timer to fire."""
    for _ in range(ticks):
        await pilot.pause(interval)


def test_streaming_text_grows_max_scroll_y():
    """Each append_streaming_text call must cause max_scroll_y to grow."""

    _PADDING = "with multiple lines of text content " * 8

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            # First chunk: ~16 lines of content
            chat_log.append_streaming_text(_PADDING + "\n\n" + _PADDING + "\n")
            await _drain(pilot)
            max_after_first = chat_log.max_scroll_y

            # Second chunk: ~32 lines of content
            chat_log.append_streaming_text(
                _PADDING + "\n\n" + _PADDING + "\n\n" + _PADDING + "\n\n" + _PADDING + "\n"
            )
            await _drain(pilot)
            max_after_second = chat_log.max_scroll_y

            # Third chunk: ~48 lines of content
            chat_log.append_streaming_text(
                _PADDING + "\n\n" + _PADDING + "\n\n" + _PADDING + "\n\n"
                + _PADDING + "\n\n" + _PADDING + "\n\n" + _PADDING + "\n"
            )
            await _drain(pilot)
            max_after_third = chat_log.max_scroll_y

            assert max_after_second > max_after_first, (
                f"max_scroll_y did not grow: {max_after_first} -> {max_after_second}"
            )
            assert max_after_third > max_after_second, (
                f"max_scroll_y did not grow: {max_after_second} -> {max_after_third}"
            )

    asyncio.run(driver())


def test_streaming_text_auto_scrolls_to_bottom():
    """While content is being appended, is_vertical_scroll_end stays True (sticky)."""

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            chunks = [
                "first chunk of streaming text\n"
                "with multiple lines\n"
                "to force overflow\n"
                "so the sticky scroll has work to do\n",
                "second chunk with more content\n"
                "and even more lines added\n"
                "so the viewport overflows further\n"
                "and auto-scroll must keep up\n",
                "third chunk with even more content\n"
                "to push past the second overflow\n"
                "and verify sticky still works\n",
                "fourth chunk that should keep us scrolled to the bottom\n"
                "by appending yet more lines\n"
                "to verify the final scroll state\n",
            ]
            for chunk in chunks:
                chat_log.append_streaming_text(chunk)
                await _drain(pilot)
                assert chat_log.is_vertical_scroll_end, (
                    f"ChatLog did not auto-scroll after appending {chunk[:30]!r}: "
                    f"scroll_y={chat_log.scroll_y}, max={chat_log.max_scroll_y}"
                )

    asyncio.run(driver())


def test_streaming_text_overflow_triggers_scrollbar():
    """Verify that overflowing content makes max_scroll_y > 0 (scroll needed)."""

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            # Append enough content to overflow the 20-line viewport.
            # Use double-newlines so each chunk becomes its own paragraph
            # (single newlines collapse into one wrapped paragraph).
            for i in range(40):
                chat_log.append_streaming_text(f"line {i}\n\n")
            await _drain(pilot, ticks=30)

            assert chat_log.max_scroll_y > 0, (
                "ChatLog did not overflow after 40 streaming appends; scroll broken?"
            )
            assert chat_log.is_vertical_scroll_end, (
                f"ChatLog should be at bottom after streaming: "
                f"scroll_y={chat_log.scroll_y}, max={chat_log.max_scroll_y}"
            )
            assert isinstance(chat_log._current_overlay, StreamingOverlay), (
                "StreamingOverlay should be the live overlay during streaming"
            )

    asyncio.run(driver())


def test_streaming_chunks_incrementally_grow_overlay():
    """Each appended chunk should grow the overlay's reported virtual height."""

    async def driver():
        app = _ChatLogApp()
        async with app.run_test(size=(80, 30)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("hi")

            chat_log.append_streaming_text(
                "first chunk\nwith multiple lines\nto grow overlay\n"
            )
            await _drain(pilot)
            first_height = (
                chat_log._current_overlay.virtual_size.height
                if chat_log._current_overlay else 0
            )

            chat_log.append_streaming_text(
                "second chunk\nwith more lines\n"
                "to grow overlay\nmore\nlines\nof content\n"
            )
            await _drain(pilot)
            second_height = (
                chat_log._current_overlay.virtual_size.height
                if chat_log._current_overlay else 0
            )

            assert second_height > first_height, (
                f"Overlay virtual height did not grow: {first_height} -> {second_height}"
            )

    asyncio.run(driver())
