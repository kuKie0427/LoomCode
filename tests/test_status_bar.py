"""Tests for the StatusBar (turn count, context capacity, progress bar).

User-facing requirements:
- ``turns`` reflects the number of user inputs (conversation rounds), not raw
  message count which would balloon with assistant + tool messages.
- ``ctx`` shows ``<used>/<window>`` plus a progress bar; updates after every
  agent turn and after compaction.
- App-level mouse-wheel scrolling routes to the ChatLog regardless of cursor
  position so the user can scroll history while the Composer holds focus.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.events import MouseScrollDown, MouseScrollUp

from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import ChatLog
from loom.tui.status_bar import ShuttleTickOverlay, StatusBar, _format_tokens, _render_engine_badge
from tests.conftest import wait_for_state


def test_format_tokens_compact():
    assert _format_tokens(0) == "0"
    assert _format_tokens(999) == "999"
    assert _format_tokens(1000) == "1.0k"
    assert _format_tokens(1234) == "1.2k"
    assert _format_tokens(1_500_000) == "1.5M"


def _seed_user_messages(app: AgentTUIApp, n: int) -> None:
    """Simulate ``n`` user submissions without invoking the LLM."""
    for i in range(n):
        app.history.append({"role": "user", "content": f"q{i}"})
        app.user_turn_count = app.user_turn_count + 1


def test_status_bar_turns_counts_user_messages_only():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            assert status_bar.turns == 0
            _seed_user_messages(app, 3)
            app.history.append({"role": "assistant", "content": "a"})
            app.history.append({"role": "user", "content": "tool_result"})
            await pilot.pause(0.05)
            assert status_bar.turns == 3, (
                "turns should count user submissions, not raw message count "
                f"(history={len(app.history)}, turns={status_bar.turns})"
            )
            text = status_bar.render()
            assert "3t" in text and "turns:" not in text

    asyncio.run(driver())


def test_status_bar_shows_context_capacity_with_rail():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            text = status_bar.render()
            assert "ctx:" in text, f"status bar missing ctx field: {text!r}"
            window = app.llm.get_context_window()
            assert str(window // 1000)[0] in text or "M" in text or "k" in text
            assert "─" in text, (
                f"ctx rail tick glyph missing: {text!r}"
            )

    asyncio.run(driver())


def test_status_bar_ctx_tokens_updates_when_app_state_changes():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            app.ctx_tokens = 12345
            await pilot.pause(0.05)
            assert status_bar.ctx_tokens == 12345
            text = status_bar.render()
            assert "12.3k" in text or "12345" in text

    asyncio.run(driver())


def test_clear_resets_turns_and_ctx():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            _seed_user_messages(app, 4)
            app.tool_call_count = 7
            app.ctx_tokens = 9999
            await pilot.pause(0.05)
            await app.run_slash_command("clear")
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            assert status_bar.turns == 0
            assert status_bar.tools == 0
            assert status_bar.ctx_tokens == 0

    asyncio.run(driver())


async def _seed_overflow(app: AgentTUIApp) -> ChatLog:
    chat_log = app.query_one(ChatLog)
    await chat_log.append_user_message("hi")
    for i in range(60):
        chat_log.append_streaming_text(f"line {i}\n\n")
    deadline = 5.0
    waited = 0.0
    step = 0.05
    while chat_log.max_scroll_y <= 0 and waited < deadline:
        await asyncio.sleep(step)
        waited += step
    return chat_log


def test_app_level_wheel_event_scrolls_chatlog():
    """Wheel events that bubble up to the App should still scroll the
    ChatLog. This protects against the real-terminal bug where wheel events
    arrive at the App without a Widget consuming them (e.g. cursor over the
    Composer or the gutter)."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.3)
            assert chat_log.max_scroll_y > 0
            baseline = chat_log.scroll_y
            ev = MouseScrollUp(app.screen, 0, 0, 0, -3, 0, False, False, False)
            app.post_message(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message="wheel up did not decrease chat_log.scroll_y",
            )

            up_y = chat_log.scroll_y
            ev2 = MouseScrollDown(app.screen, 0, 0, 0, 3, 0, False, False, False)
            app.post_message(ev2)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y > up_y, timeout=2.0,
                message="wheel down did not increase chat_log.scroll_y",
            )

    asyncio.run(driver())


def test_status_bar_updates_after_user_turn_count_changes():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            assert "0t" in status_bar.render() and "turns:" not in status_bar.render()
            app.user_turn_count = 5
            await pilot.pause(0.05)
            assert "5t" in status_bar.render() and "turns:" not in status_bar.render()

    asyncio.run(driver())


def test_wheel_event_posted_to_composer_scrolls_chatlog():
    """Real-world bug: cursor sits in the input box while user wants to
    scroll history. Composer (TextArea) inherits ScrollView and would
    normally consume wheel events. This test verifies our override
    forwards the event to ChatLog instead.
    """

    async def driver():
        from loom.tui.composer import Composer

        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = await _seed_overflow(app)
            await pilot.pause(0.5)
            assert chat_log.max_scroll_y > 0
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.1)
            composer = app.query_one(Composer)
            baseline = chat_log.scroll_y
            assert baseline > 0
            ev = MouseScrollUp(composer, 0, 0, 0, -3, 0, False, False, False)
            composer.post_message(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message=(
                    "Wheel event over Composer must scroll ChatLog, not Composer"
                ),
            )

            up_y = chat_log.scroll_y
            ev2 = MouseScrollDown(composer, 0, 0, 0, 3, 0, False, False, False)
            composer.post_message(ev2)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y > up_y, timeout=2.0,
                message="wheel down over Composer did not scroll ChatLog",
            )

    asyncio.run(driver())


def test_layout_has_unified_chrome_container():
    """Visual unification: status bar and composer must live inside one
    container so they read as a single bottom 'chrome' zone, like opencode.
    """

    async def driver():
        from loom.tui.composer import Composer

        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            chrome = app.query_one("#chrome")
            assert chrome is not None
            status_bar = app.query_one(StatusBar)
            composer = app.query_one(Composer)
            assert status_bar.parent is chrome, (
                f"StatusBar must be inside #chrome, got parent={status_bar.parent}"
            )
            assert composer.parent is chrome, (
                f"Composer must be inside #chrome, got parent={composer.parent}"
            )

    asyncio.run(driver())


def test_app_on_event_intercepts_wheel_before_screen_forward():
    """The real driver path goes: driver → App.on_event → screen._forward_event.
    We override App.on_event so that wheel events are captured at the App
    boundary and scroll the ChatLog regardless of which widget is at the
    cursor. This is the only place in Textual's event flow where the
    App-level handler can intercept mouse events for real driver input.
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await _seed_overflow(app)
            await pilot.pause(0.5)
            assert chat_log.max_scroll_y > 0
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.1)
            baseline = chat_log.scroll_y

            ev = MouseScrollUp(app.screen, 0, 0, 0, -3, 0, False, False, False)
            await app.on_event(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message="App.on_event did not intercept wheel",
            )

    asyncio.run(driver())


def test_wheel_posts_update_messages_to_screen():
    """The user's bug was that wheel scroll updated the logical scroll
    position but the visual did not refresh until focus changed. We post
    ``Update`` and ``UpdateScroll`` messages directly to the screen so the
    compositor knows to repaint on its next pump cycle.
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await _seed_overflow(app)
            await pilot.pause(0.5)
            assert chat_log.max_scroll_y > 0
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.1)

            posted: list[str] = []
            orig_post = app.screen.post_message

            def track(msg):
                posted.append(msg.__class__.__name__)
                return orig_post(msg)

            app.screen.post_message = track  # type: ignore[method-assign]
            try:
                ev = MouseScrollUp(app.screen, 0, 0, 0, -3, 0, False, False, False)
                await app.on_event(ev)
                await pilot.pause(0.1)
            finally:
                app.screen.post_message = orig_post  # type: ignore[method-assign]

            assert "Update" in posted, (
                f"Update message must be posted to screen so the compositor "
                f"repaints without waiting for a focus handoff; got: {posted}"
            )
            assert "UpdateScroll" in posted, (
                f"UpdateScroll message must be posted to screen so the "
                f"compositor re-runs scroll layout; got: {posted}"
            )

    asyncio.run(driver())


def test_wheel_event_with_cursor_over_composer_uses_app_on_event():
    """Real-world bug repro: cursor sits in the Composer (most common case
    while typing), user wheels. Without App.on_event interception, the event
    was dispatched to the Composer and never bubbled to the App's message
    pump, so the App-level handler was dead code.
    """

    async def driver():
        from loom.tui.composer import Composer

        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            composer = app.query_one(Composer)
            await _seed_overflow(app)
            await pilot.pause(0.5)
            assert chat_log.max_scroll_y > 0
            chat_log.scroll_y = chat_log.max_scroll_y
            await pilot.pause(0.1)
            baseline = chat_log.scroll_y

            cx = composer.region.x + composer.region.width // 2
            cy = composer.region.y + composer.region.height // 2
            ev = MouseScrollUp(
                app.screen, cx, cy, 0, -3, 0, False, False, False,
                screen_x=cx, screen_y=cy,
            )
            await app.on_event(ev)
            await wait_for_state(
                pilot, lambda: chat_log.scroll_y < baseline, timeout=2.0,
                message="App.on_event must intercept wheel regardless of cursor",
            )

    asyncio.run(driver())


def test_status_bar_default_engine_state_idle():
    assert StatusBar().engine_state == "idle"


def test_render_engine_badge_pure_helper_idle():
    assert _render_engine_badge("idle") == "[$text-muted]● idle[/]"


def test_render_engine_badge_pure_helper_error():
    assert _render_engine_badge("error") == "[$error]⊗ error[/]"


@pytest.mark.parametrize(
    "state",
    ["thinking", "streaming", "executing", "compacting"],
)
def test_render_engine_badge_pure_helper_active_states(state):
    assert _render_engine_badge(state) == "[$accent]▸ run[/]"


def test_status_bar_renders_engine_state_badge_for_three_representative_states():
    """P0a §4.2.1: StatusBar.render() must include the engine_state badge
    in the joined stats. Locks the 3 representative branches:
      - executing (active):   [$accent]▸ run[/]
      - idle:                 [$text-muted]● idle[/]
      - error:                [$error]⊗ error[/]
    P0b wires the App reactive transitions; P0a locks the render layer.
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)

            status_bar.engine_state = "executing"
            await pilot.pause(0.05)
            assert "[$accent]▸ run[/]" in status_bar.render(), (
                f"executing badge missing: {status_bar.render()!r}"
            )

            status_bar.engine_state = "idle"
            await pilot.pause(0.05)
            assert "[$text-muted]● idle[/]" in status_bar.render(), (
                f"idle badge missing: {status_bar.render()!r}"
            )

            status_bar.engine_state = "error"
            await pilot.pause(0.05)
            assert "[$error]⊗ error[/]" in status_bar.render(), (
                f"error badge missing: {status_bar.render()!r}"
            )

    asyncio.run(driver())


def test_status_bar_renders_rail_not_fill_bar():
    """P1b §2.2.4: ctx must use fixed rail (─) + shuttle (●), not fill bar (█/░)."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            status_bar.engine_state = "idle"
            await pilot.pause(0.05)
            text = status_bar.render()
            assert "█" not in text, f"fill bar forbidden in idle render: {text!r}"
            assert "░" not in text, f"empty bar forbidden in idle render: {text!r}"
            assert "─" in text, f"rail tick must appear in idle render: {text!r}"
            assert "●" in text, f"shuttle must appear in idle render: {text!r}"

    asyncio.run(driver())


def test_status_bar_renders_shuttle_tick_above():
    """§4.2.1: StatusBar no longer renders inline ^N. Tick is in ShuttleTickOverlay above."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)

            status_bar = app.query_one(StatusBar)
            tick_overlay = app.query_one(ShuttleTickOverlay)
            chrome = app.query_one("#chrome")

            # Verify DOM structure: TickOverlay is first child, StatusBar is inside #chrome
            children = list(chrome.children)
            assert tick_overlay in children, "TickOverlay must be inside #chrome"
            assert status_bar in children, "StatusBar must be inside #chrome"
            assert list(chrome.children).index(tick_overlay) < list(chrome.children).index(
                status_bar
            ), "TickOverlay must sit above StatusBar in #chrome"

            # Verify StatusBar.render() no longer contains inline ^0/^1
            status_bar.shuttle_phase = 0
            await pilot.pause(0.05)
            sb_text = status_bar.render()
            assert "^0" not in sb_text, (
                f"StatusBar should NOT contain inline ^0 after P3a: {sb_text!r}"
            )
            assert "^1" not in sb_text, (
                f"StatusBar should NOT contain inline ^1 after P3a: {sb_text!r}"
            )

            # Verify TickOverlay DOES render ^ (the tick)
            tick_text = tick_overlay.render()
            assert "^" in tick_text, (
                f"TickOverlay must render ^ glyph: {tick_text!r}"
            )

            # Verify the ^ position is right after 'ctx: ' (shuttle_x offset)
            ctx_marker = "ctx:"
            assert ctx_marker in tick_text, (
                f"TickOverlay must contain ctx: prefix: {tick_text!r}"
            )

    asyncio.run(driver())
