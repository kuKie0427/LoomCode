"""Tests for AgentTUIApp gear tick driver — §2.2.3 primitive 1.

Verifies the 1Hz interval that cycles StatusBar.shuttle_phase 0/1/2 when
the engine is active (3-frame gear animation), and the idle freeze / reset
semantics.
"""
from __future__ import annotations

import asyncio

from loom.tui.app import AgentTUIApp
from loom.tui.status_bar import StatusBar
from tests.conftest import wait_for_state


def test_app_shuttle_tick_idle_noop() -> None:
    """engine_state=idle: _tick_shuttle early-returns, shuttle_phase stays 0."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            app.engine_state = "idle"
            status_bar.shuttle_phase = 0
            await pilot.pause(0.05)
            app._tick_shuttle()
            assert status_bar.shuttle_phase == 0, (
                f"shuttle must stay 0 in idle, got {status_bar.shuttle_phase}"
            )

    asyncio.run(driver())


def test_app_shuttle_tick_active_cycles_3_frames() -> None:
    """engine_state=executing: _tick_shuttle cycles phase 0→1→2→0 (3-frame gear)."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            app.engine_state = "executing"
            status_bar.shuttle_phase = 0
            await pilot.pause(0.05)
            app._tick_shuttle()
            assert status_bar.shuttle_phase == 1, (
                f"first tick should advance to 1, got {status_bar.shuttle_phase}"
            )
            app._tick_shuttle()
            assert status_bar.shuttle_phase == 2, (
                f"second tick should advance to 2, got {status_bar.shuttle_phase}"
            )
            app._tick_shuttle()
            assert status_bar.shuttle_phase == 0, (
                f"third tick should wrap back to 0, got {status_bar.shuttle_phase}"
            )

    asyncio.run(driver())


def test_app_watch_engine_state_resets_shuttle_on_idle() -> None:
    """Transition executing → idle resets shuttle_phase to 0 via watch."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            app.engine_state = "executing"
            status_bar.shuttle_phase = 5
            await pilot.pause(0.05)
            # Now transition to idle
            app._set_engine_state("idle")
            await wait_for_state(
                pilot,
                lambda: status_bar.shuttle_phase == 0,
                timeout=1.0,
                message=f"shuttle_phase must reset to 0 on idle, got {status_bar.shuttle_phase}",
            )

    asyncio.run(driver())


def test_app_shuttle_interval_registered() -> None:
    """After on_mount, the 1Hz shuttle-tick interval is registered."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            # Textual stores timers in app._timers (a set of Timer objects).
            # Each Timer has a .name attribute.
            found = any(
                getattr(t, "name", None) == "shuttle-tick"
                for t in app._timers
            )
            if not found:
                # Fallback: search by callback reference
                found = any(
                    getattr(t, "_callback", None) is app._tick_shuttle
                    for t in app._timers
                )
            assert found, (
                "shuttle-tick interval must be registered in app._timers"
            )

    asyncio.run(driver())
