"""Tests for the §2.2.1 EngineState type and §4.2.1 StatusBar engine badge.

Pure-helper tests for `_render_engine_badge` — no widget needed, no Textual
app needed. Covers all 6 engine states plus the default reactive value.
"""

from __future__ import annotations

from typing import get_args

import pytest

from loom.tui.status_bar import EngineState, StatusBar, _render_engine_badge


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
