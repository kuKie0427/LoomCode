"""Tests for manual scroll behavior in the TUI app.

Regression: Composer (TextArea) is focused on mount and eats PageUp/PageDown
for cursor page movement, so standard scroll keys never reach the chat log.
The fix is global BINDINGS (Shift+PageUp/Down/Home/End) on AgentTUIApp that
always scroll the chat log regardless of focus.
"""

from __future__ import annotations

import asyncio

from loop.tui.app import AgentTUIApp
from loop.tui.chat_log import ChatLog
from loop.tui.composer import Composer
from loop.tui.status_bar import StatusBar


async def _seed_overflow(app: AgentTUIApp) -> ChatLog:
    chat_log = app.query_one(ChatLog)
    await chat_log.append_user_message("hello")
    for i in range(60):
        chat_log.append_streaming_text(f"line {i}\n\n")
    return chat_log


def test_shift_pageup_scrolls_chat_when_composer_focused():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            assert chat_log.scroll_y > 0
            baseline = chat_log.scroll_y
            assert isinstance(app.focused, Composer)
            await pilot.press("shift+pageup")
            await pilot.pause(0.1)
            assert chat_log.scroll_y < baseline
    asyncio.run(driver())


def test_shift_pagedown_scrolls_chat_when_composer_focused():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = 0
            await pilot.pause(0.05)
            assert isinstance(app.focused, Composer)
            await pilot.press("shift+pagedown")
            await pilot.pause(0.1)
            assert chat_log.scroll_y > 0
    asyncio.run(driver())


def test_ctrl_end_jumps_to_bottom_and_re_enables_sticky():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            chat_log.scroll_y = 0
            chat_log._sticky = False
            await pilot.pause(0.05)
            assert isinstance(app.focused, Composer)
            await pilot.press("ctrl+end")
            await pilot.pause(0.1)
            assert chat_log.scroll_y == chat_log.max_scroll_y
            assert chat_log._sticky is True
    asyncio.run(driver())


def test_ctrl_home_jumps_to_top():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            assert chat_log.scroll_y == chat_log.max_scroll_y
            assert isinstance(app.focused, Composer)
            await pilot.press("ctrl+home")
            await pilot.pause(0.1)
            assert chat_log.scroll_y == 0
    asyncio.run(driver())


def test_scroll_bindings_registered():
    binding_keys = set()
    for b in AgentTUIApp.BINDINGS:
        if isinstance(b, tuple):
            binding_keys.add(b[0])
        else:
            binding_keys.add(b.key)
    for key in ("shift+pageup", "shift+pagedown", "ctrl+home", "ctrl+end"):
        assert key in binding_keys, f"missing binding: {key}"


def test_chat_log_has_focus_indicator_css():
    app = AgentTUIApp()
    css = app.CSS
    assert "#chat-log:focus" in css or "#chat-log:focus-within" in css


def test_status_bar_shows_scroll_hint_when_overflow():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            status_bar = app.query_one(StatusBar)
            await pilot.pause(0.1)
            initial_text = status_bar.render()
            assert "Shift" not in initial_text
            await _seed_overflow(app)
            await pilot.pause(0.3)
            hint_text = status_bar.render()
            assert "Shift" in hint_text
    asyncio.run(driver())
