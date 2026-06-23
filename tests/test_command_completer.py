"""Tests for the /-command completer — filter_commands pure function + CommandCompleter widget.

Locking the contract from ``loom/tui/completer.py``:

  * ``filter_commands`` is a pure function (no Textual app needed for tests 1-5).
  * ``CommandCompleter`` is a ``Static`` subclass with reactive ``selected``/``matches``
    and watchers that call ``_render_rows()``.
  * CSS uses ``display: none`` by default; the ``.visible`` class toggles ``display: block``.

Test layers:
  1. Pure function tests — 5 cases (no Textual app required).
  2. Widget behavior tests — 3 cases (minimal Textual ``App`` wrapper).
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from loom.tui.completer import CommandCompleter, filter_commands
from loom.tui.slash_commands import SlashCommand

# ── Pure function tests (no Textual app needed) ────────────────────────────


def test_filter_commands_empty_query():
    """Empty query returns first N (limit) commands."""
    result = filter_commands("", limit=3)
    assert len(result) == 3


def test_filter_commands_prefix_priority():
    """Query "mo" → model ranks first (prefix match on name)."""
    result = filter_commands("mo")
    assert result[0].name == "model"


def test_filter_commands_alias_match():
    """Query "q" → quit (via alias "q") ranks first."""
    result = filter_commands("q")
    assert result[0].name == "quit"


def test_filter_commands_fuzzy_fallback():
    """Query "hlp" → help matches via fuzzy (difflib cutoff=0.3)."""
    result = filter_commands("hlp")
    assert result[0].name == "help"


def test_filter_commands_limit():
    """Limit=3 returns ≤3 items for empty query."""
    result = filter_commands("", limit=3)
    assert len(result) <= 3


# ── CommandCompleter widget tests ──────────────────────────────────────────


class _CompleterTestApp(App):
    """Minimal app wrapping a CommandCompleter for widget-level tests."""

    def compose(self) -> ComposeResult:
        yield CommandCompleter()


async def _noop_handler(app, args: str) -> None:
    """Minimal async handler for stub SlashCommand objects."""
    pass


def test_completer_show_hide():
    """show_for adds "visible" class, hide() removes it."""

    async def driver():
        async with _CompleterTestApp().run_test() as pilot:
            await pilot.pause()
            completer = pilot.app.query_one(CommandCompleter)
            assert not completer.has_class("visible")
            completer.show_for("/mo")
            assert completer.has_class("visible")
            completer.hide()
            assert not completer.has_class("visible")

    asyncio.run(driver())


def test_completer_move_wrap():
    """move(1) wraps around from last item back to first."""

    async def driver():
        async with _CompleterTestApp().run_test() as pilot:
            await pilot.pause()
            completer = pilot.app.query_one(CommandCompleter)
            items = [
                SlashCommand("a", "desc a", _noop_handler),
                SlashCommand("b", "desc b", _noop_handler),
                SlashCommand("c", "desc c", _noop_handler),
            ]
            completer.matches = items
            completer.selected = 0
            assert completer.selected == 0
            completer.move(1)
            assert completer.selected == 1
            completer.move(1)
            assert completer.selected == 2
            completer.move(1)
            assert completer.selected == 0  # wrap-around

    asyncio.run(driver())


def test_completer_current_returns_selected():
    """show_for("/help") → current().name == "help"."""

    async def driver():
        async with _CompleterTestApp().run_test() as pilot:
            await pilot.pause()
            completer = pilot.app.query_one(CommandCompleter)
            completer.show_for("/help")
            cmd = completer.current()
            assert cmd is not None
            assert cmd.name == "help"

    asyncio.run(driver())
