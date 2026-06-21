"""Tests for ShuttleTickOverlay — §4.2.1 tick above shuttle.

Covers: import, default reactives, idle/active position behavior, DOM
structure (#chrome ordering), prefix alignment with StatusBar, color
invariant ($text-muted), and App→TickOverlay reactive propagation.
"""

from __future__ import annotations

import asyncio
import re

from loom.tui.app import AgentTUIApp
from loom.tui.status_bar import ShuttleTickOverlay, StatusBar
from tests.conftest import wait_for_state


def test_shuttle_tick_overlay_importable():
    """Pure import test: ShuttleTickOverlay class is accessible."""
    assert ShuttleTickOverlay is not None


def test_shuttle_tick_overlay_default_reactives():
    """Default reactive values match idle/no-context state."""
    tick = ShuttleTickOverlay()
    assert tick.engine_state == "idle"
    assert tick.shuttle_phase == 0
    assert tick.ctx_tokens == 0
    assert tick.ctx_window == 0


def test_shuttle_tick_overlay_renders_caret_in_idle():
    """In idle, TickOverlay render contains ^ after ctx: prefix."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)
            text = tick.render()
            assert "^" in text, f"TickOverlay must render ^ in idle: {text!r}"
            assert "ctx:" in text, f"TickOverlay must include ctx: prefix: {text!r}"
            # Verify ^ comes after ctx: (not in the prefix itself)
            ctx_end = text.find("ctx:") + 4
            assert "^" in text[ctx_end:], (
                f"^ must appear after ctx: in render: {text!r}"
            )

    asyncio.run(driver())


def test_shuttle_tick_overlay_prefix_matches_status_bar():
    """§4.2.1 char-level alignment: TickOverlay prefix contains the same plain
    text as StatusBar prefix (loom · model · ⎇ branch · Nt·Mtl · ctx:) but
    rendered as a single $text-faint span instead of the full §9.3 hierarchy.

    The ^ caret must land at the same visual column as the ● shuttle.
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)
            status_bar = app.query_one(StatusBar)

            tick_text = tick.render()
            sb_text = status_bar.render()

            # Strip Rich markup tags from both renders to compare plain text
            def strip_tags(s: str) -> str:
                return re.sub(r"\[\$[^\]]+\]|\[/\]", "", s)

            tick_plain = strip_tags(tick_text)
            sb_plain = strip_tags(sb_text)

            # Find ctx: in both renders
            ctx_idx_tick = tick_plain.find("ctx:")
            ctx_idx_sb = sb_plain.find("ctx:")
            assert ctx_idx_tick >= 0, f"ctx: missing in TickOverlay: {tick_plain!r}"
            assert ctx_idx_sb >= 0, f"ctx: missing in StatusBar: {sb_plain!r}"

            # Plain text up to and including "ctx:" must be identical
            tick_prefix = tick_plain[: ctx_idx_tick + 4]
            sb_prefix = sb_plain[: ctx_idx_sb + 4]
            assert tick_prefix == sb_prefix, (
                f"Plain-text prefix mismatch up to ctx:\n"
                f"  TickOverlay: {tick_prefix!r}\n"
                f"  StatusBar:   {sb_prefix!r}"
            )

            assert "[$surface]loom" in tick_text, (
                f"TickOverlay prefix must use single $surface span (invisible against #chrome bg); got: {tick_text!r}"
            )
            assert "[$surface]loom · " not in sb_text, (
                f"StatusBar prefix must NOT collapse to single $surface span (would hide §9.3 hierarchy); got: {sb_text!r}"
            )

    asyncio.run(driver())


def test_shuttle_tick_overlay_idle_freezes_position():
    """Idle: shuttle_phase changes don't affect ^ position."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)

            tick.shuttle_phase = 0
            await pilot.pause(0.05)
            text_0 = tick.render()
            pos_0 = text_0.index("^")

            tick.shuttle_phase = 1
            await pilot.pause(0.05)
            text_1 = tick.render()
            pos_1 = text_1.index("^")

            assert pos_0 == pos_1, (
                f"Idle freeze violated: phase=0 at pos {pos_0}, phase=1 at pos {pos_1}"
            )

    asyncio.run(driver())


def test_shuttle_tick_overlay_active_bobs_phase():
    """Active (non-idle): shuttle_phase 0→1 shifts ^ right by 1."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)

            # Activate: set engine_state to non-idle
            tick.engine_state = "executing"

            tick.shuttle_phase = 0
            await pilot.pause(0.05)
            text_0 = tick.render()
            pos_0 = text_0.index("^")

            tick.shuttle_phase = 1
            await pilot.pause(0.05)
            text_1 = tick.render()
            pos_1 = text_1.index("^")

            assert pos_1 == pos_0 + 1, (
                f"Active bob failed: phase=0 at {pos_0}, phase=1 at {pos_1}, "
                f"expected phase=1 at {pos_0 + 1}"
            )

    asyncio.run(driver())


def test_shuttle_tick_overlay_sits_above_status_bar_in_chrome():
    """§4.2.1: TickOverlay is first child in #chrome, StatusBar is second."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            chrome = app.query_one("#chrome")
            tick = app.query_one(ShuttleTickOverlay)
            status_bar = app.query_one(StatusBar)

            children = list(chrome.children)
            assert tick in children, "TickOverlay must be inside #chrome"
            assert status_bar in children, "StatusBar must be inside #chrome"
            tick_idx = children.index(tick)
            sb_idx = children.index(status_bar)
            assert tick_idx < sb_idx, (
                f"TickOverlay (index {tick_idx}) must be before "
                f"StatusBar (index {sb_idx}) in #chrome"
            )

    asyncio.run(driver())


def test_shuttle_tick_overlay_uses_text_muted_color():
    """Tick uses [$text-muted], NOT success/warning/error."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)
            text = tick.render()
            assert "[$text-muted]" in text, (
                f"TickOverlay must use $text-muted color: {text!r}"
            )
            assert "[$success]" not in text, (
                f"TickOverlay must NOT use $success: {text!r}"
            )
            assert "[$warning]" not in text, (
                f"TickOverlay must NOT use $warning: {text!r}"
            )
            assert "[$error]" not in text, (
                f"TickOverlay must NOT use $error: {text!r}"
            )

    asyncio.run(driver())


def test_sync_shuttle_tick_overlay_propagates_engine_state():
    """App.engine_state → TickOverlay.engine_state within 1s."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)

            app.engine_state = "executing"
            await wait_for_state(
                pilot,
                lambda: tick.engine_state == "executing",
                timeout=1.0,
                message="TickOverlay.engine_state must propagate from App",
            )

    asyncio.run(driver())


def test_sync_shuttle_tick_overlay_propagates_ctx_tokens():
    """App.ctx_tokens → TickOverlay.ctx_tokens within 1s."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            tick = app.query_one(ShuttleTickOverlay)

            app.ctx_tokens = 5000
            await wait_for_state(
                pilot,
                lambda: tick.ctx_tokens == 5000,
                timeout=1.0,
                message="TickOverlay.ctx_tokens must propagate from App",
            )

    asyncio.run(driver())
