"""Tests for mouse wheel scrolling in the TUI app.

User requirement: scrolling must work via mouse wheel (no keyboard shortcuts).
Default Textual behavior: Widget._on_mouse_scroll_down/up fires on the
topmost widget under the cursor; if it can't scroll, the event bubbles up
to ancestors (bubble=True) until a scrollable widget handles it.
"""

from __future__ import annotations

import asyncio

from textual.events import MouseScrollDown, MouseScrollUp

from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import ChatLog, UserMessage
from tests.conftest import wait_for_state


async def _seed_overflow(app: AgentTUIApp) -> ChatLog:
    chat_log = app.query_one(ChatLog)
    await chat_log.append_user_message("hello")
    for i in range(60):
        chat_log.append_streaming_text(f"line {i}\n\n")
    deadline = 5.0
    waited = 0.0
    step = 0.05
    while chat_log.max_scroll_y <= 0 and waited < deadline:
        await asyncio.sleep(step)
        waited += step
    return chat_log


def test_mouse_wheel_on_chatlog_scrolls_up():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            assert chat_log.scroll_y > 0
            baseline = chat_log.scroll_y
            ev = MouseScrollUp(chat_log, 40, 10, 0, -3, 0, False, False, False)
            chat_log.post_message(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message="mouse wheel up on chatlog did not decrease scroll_y",
            )
    asyncio.run(driver())


def test_mouse_wheel_on_chatlog_scrolls_down():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = 0
            await pilot.pause(0.05)
            ev = MouseScrollDown(chat_log, 40, 10, 0, 3, 0, False, False, False)
            chat_log.post_message(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y > 0, timeout=2.0,
                message="mouse wheel down on chatlog did not increase scroll_y",
            )
    asyncio.run(driver())


def test_mouse_wheel_bubbles_from_child_markdown_to_chatlog():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            user_msg = chat_log.query_one(UserMessage)
            baseline = chat_log.scroll_y
            ev = MouseScrollUp(user_msg, 40, 10, 0, -3, 0, False, False, False)
            user_msg.post_message(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message="Wheel event on UserMessage did not bubble to ChatLog",
            )
    asyncio.run(driver())


def test_mouse_wheel_repeatedly_reaches_top():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            assert chat_log.max_scroll_y > 0
            for _ in range(300):
                ev = MouseScrollUp(chat_log, 40, 10, 0, -3, 0, False, False, False)
                chat_log.post_message(ev)
                await pilot.pause(0.01)
            assert chat_log.scroll_y == 0
    asyncio.run(driver())


def test_mouse_wheel_repeatedly_reaches_bottom():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = 0
            await pilot.pause(0.05)
            for _ in range(300):
                ev = MouseScrollDown(chat_log, 40, 10, 0, 3, 0, False, False, False)
                chat_log.post_message(ev)
                await pilot.pause(0.01)
            assert chat_log.scroll_y == chat_log.max_scroll_y
    asyncio.run(driver())


def test_scrollbar_size_is_visible():
    app = AgentTUIApp()
    css = app.CSS
    assert "scrollbar-size-vertical: 3" in css, (
        "Scrollbar should be 3 cells wide for easy mouse targeting"
    )


def test_status_bar_has_no_scroll_hint_when_overflowing():
    """StatusBar must NOT show a 'scroll with mouse wheel' hint even when the
    chat log overflows — the hint was dropped (f-tui-statusbar-drop-scroll-hint)
    to keep the bar usable on narrow terminals (≥ 93 cols instead of ≥ 119)."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            from loom.tui.status_bar import StatusBar
            status_bar = app.query_one(StatusBar)
            await _seed_overflow(app)
            await pilot.pause(0.3)
            text = status_bar.render()
            assert "mouse wheel" not in text, (
                f"Status bar must NOT show a mouse-wheel scroll hint; got: {text!r}"
            )
            assert "scroll" not in text, (
                f"Status bar must not mention scrolling at all; got: {text!r}"
            )
    asyncio.run(driver())


def test_keyboard_scroll_page_up_down():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.05)
            await pilot.press("shift+pageup")
            await pilot.pause(0.05)
            assert chat_log.scroll_y < chat_log.max_scroll_y, (
                "Shift+PageUp should scroll the chat log up"
            )
            await pilot.press("shift+pagedown")
            await pilot.pause(0.05)
            assert chat_log.scroll_y == chat_log.max_scroll_y, (
                "Shift+PageDown should scroll the chat log back to the bottom"
            )
    asyncio.run(driver())


def test_keyboard_scroll_home_end():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.05)
            await pilot.press("ctrl+home")
            await pilot.pause(0.05)
            assert chat_log.scroll_y == 0, "Ctrl+Home should jump to the top of the chat log"
            await pilot.press("ctrl+end")
            await pilot.pause(0.05)
            assert chat_log.scroll_y == chat_log.max_scroll_y, (
                "Ctrl+End should jump to the bottom of the chat log"
            )
    asyncio.run(driver())
