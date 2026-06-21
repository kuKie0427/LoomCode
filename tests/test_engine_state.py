"""Tests for the §2.2.1 EngineState type and §4.2.1 StatusBar engine badge.

Pure-helper tests for `_render_engine_badge` — no widget needed, no Textual
app needed. Covers all 6 engine states plus the default reactive value.
"""

from __future__ import annotations

import asyncio
from typing import get_args

import pytest

from loom.tui.app import AgentTUIApp
from loom.tui.status_bar import EngineState, StatusBar, _render_engine_badge
from tests.conftest import wait_for_state


def test_engine_state_type_has_six_values():
    """§2.2.1: EngineState is a Literal of exactly 6 states."""
    values = get_args(EngineState)
    assert values == (
        "idle",
        "thinking",
        "streaming",
        "executing",
        "compacting",
        "error",
    ), f"EngineState must be exactly 6 values, got {values!r}"


def test_render_engine_badge_idle():
    assert _render_engine_badge("idle") == "[$text-muted]● idle[/]"


def test_render_engine_badge_error():
    assert _render_engine_badge("error") == "[$error]⊗ error[/]"


@pytest.mark.parametrize(
    "state",
    ["thinking", "streaming", "executing", "compacting"],
)
def test_render_engine_badge_active_states(state):
    """The 4 active states all show [$accent]▸ run[/] per spec §4.2.1 — the
    active state is conveyed by other UI elements (spinner/animation), not by
    badge text. This is by design; do NOT differentiate per state in the badge.
    """
    assert _render_engine_badge(state) == "[$accent]▸ run[/]"


def test_render_engine_badge_no_literal_colors():
    """§2.3 one-theme rule: badge must use token spans ($text-muted/$error/$accent),
    never literal color names like [green]/[red]/[yellow]/[bold blue] etc.
    """
    import re

    literal_color_re = re.compile(
        r"\[(green|yellow|cyan|red|blue|purple|magenta|orange|black|white|grey|gray|bright_)"
        r"( on [a-z_]+)?\]"
    )
    for state in (
        "idle",
        "thinking",
        "streaming",
        "executing",
        "compacting",
        "error",
    ):
        out = _render_engine_badge(state)
        assert not literal_color_re.search(out), (
            f"badge for {state!r} uses literal color: {out!r}"
        )


def test_status_bar_engine_state_defaults_to_idle():
    """A freshly constructed StatusBar must default to the 'idle' state.
    This guards against accidental init order bugs and locks §2.2.1 default.
    """
    assert StatusBar().engine_state == "idle"


def test_engine_state_idle_default():
    """Pilot-launched App's engine_state starts as 'idle' (§2.2.1 default)."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            assert app.engine_state == "idle"

    asyncio.run(driver())


def test_engine_state_thinking_on_assistant_turn_start():
    """_set_engine_state('thinking') → App and StatusBar reflect 'thinking'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("thinking")
            await wait_for_state(
                pilot, lambda: app.engine_state == "thinking", timeout=2.0,
                message="engine_state should be 'thinking'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "thinking"

    asyncio.run(driver())


def test_engine_state_streaming_on_text_delta():
    """_set_engine_state('streaming') → App and StatusBar reflect 'streaming'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("streaming")
            await wait_for_state(
                pilot, lambda: app.engine_state == "streaming", timeout=2.0,
                message="engine_state should be 'streaming'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "streaming"

    asyncio.run(driver())


def test_engine_state_executing_on_tool_use():
    """_set_engine_state('executing') → App and StatusBar reflect 'executing'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("executing")
            await wait_for_state(
                pilot, lambda: app.engine_state == "executing", timeout=2.0,
                message="engine_state should be 'executing'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "executing"

    asyncio.run(driver())


def test_engine_state_compacting_on_compact():
    """_set_engine_state('compacting') → App and StatusBar reflect 'compacting'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("compacting")
            await wait_for_state(
                pilot, lambda: app.engine_state == "compacting", timeout=2.0,
                message="engine_state should be 'compacting'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "compacting"

    asyncio.run(driver())


def test_engine_state_error_on_tool_error():
    """_set_engine_state('error') → App and StatusBar reflect 'error'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("error")
            await wait_for_state(
                pilot, lambda: app.engine_state == "error", timeout=2.0,
                message="engine_state should be 'error'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "error"

    asyncio.run(driver())


def test_engine_state_idle_on_turn_end():
    """_set_engine_state('idle') from a non-idle state → engine_state == 'idle'."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("executing")
            await pilot.pause(0.05)
            assert app.engine_state == "executing"
            app._set_engine_state("idle")
            await wait_for_state(
                pilot, lambda: app.engine_state == "idle", timeout=2.0,
                message="engine_state should revert to 'idle'",
            )
            status_bar = app.query_one(StatusBar)
            assert status_bar.engine_state == "idle"

    asyncio.run(driver())


def test_engine_state_transition_priority():
    """§2.2.1: sequence of states — latest wins (idle at end)."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            app._set_engine_state("thinking")
            app._set_engine_state("streaming")
            app._set_engine_state("executing")
            app._set_engine_state("idle")
            await wait_for_state(
                pilot, lambda: app.engine_state == "idle", timeout=2.0,
                message="engine_state should be 'idle' (last in sequence)",
            )

    asyncio.run(driver())
