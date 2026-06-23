"""Tests for the Ctrl+P command palette — CommandPaletteModal widget + app wiring.

Locks the contract from ``loom/tui/command_palette.py``:

  * ``CommandPaletteModal`` is a ``ModalScreen[SlashCommand | None]``
    with an ``Input`` filter and ``ListView`` of matching commands.
  * ``action_show_command_palette`` pushes it onto the screen stack.
  * ``_on_palette_selected`` sets the Composer text and posts
    ``Composer.Submitted``.

Test layers:
  1. Widget tests — 4 cases (minimal ``_PaletteTestApp`` wrapper).
  2. App-level wiring tests — 2 cases (``AgentTUIApp`` integration).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from textual.app import App
from textual.widgets import Input, ListView, Static

from loom.tui.app import AgentTUIApp
from loom.tui.command_palette import CommandPaletteModal
from loom.tui.composer import Composer
from loom.tui.slash_commands import SlashCommand


class _PaletteTestApp(App):
    """Minimal app that pushes CommandPaletteModal on mount."""

    def on_mount(self) -> None:
        self.push_screen(CommandPaletteModal())


# ── Widget tests (1-4) ─────────────────────────────────────────────────────


def test_palette_initial_population() -> None:
    """Mount → ListView has 7 items (all commands from all_commands())."""

    async def driver() -> None:
        async with _PaletteTestApp().run_test() as pilot:
            await pilot.pause()
            list_view = pilot.app.screen.query_one(
                "#command-palette-list", ListView
            )
            assert len(list_view.children) == 7

    asyncio.run(driver())


def test_palette_filter_narrows() -> None:
    """Typing "qu" in the filter Input narrows to 1 item (quit via prefix)."""

    async def driver() -> None:
        async with _PaletteTestApp().run_test() as pilot:
            await pilot.pause()
            input_widget = pilot.app.screen.query_one(
                "#command-palette-input", Input
            )
            input_widget.value = "qu"
            await pilot.pause()
            list_view = pilot.app.screen.query_one(
                "#command-palette-list", ListView
            )
            assert len(list_view.children) == 1

    asyncio.run(driver())


def test_palette_filter_alias() -> None:
    """Typing "ex" (exit alias for quit) includes quit in results."""

    async def driver() -> None:
        async with _PaletteTestApp().run_test() as pilot:
            await pilot.pause()
            input_widget = pilot.app.screen.query_one(
                "#command-palette-input", Input
            )
            input_widget.value = "ex"
            await pilot.pause()
            list_view = pilot.app.screen.query_one(
                "#command-palette-list", ListView
            )
            assert len(list_view.children) > 0
            # At least one result should be "quit" (matched via alias "exit")
            assert any(
                "quit" in item.query_one(Static).render().plain.lower()
                for item in list_view.children
            )

    asyncio.run(driver())


def test_palette_esc_dismisses_none() -> None:
    """Pressing escape dismisses the palette (pop from screen stack)."""

    async def driver() -> None:
        async with _PaletteTestApp().run_test() as pilot:
            await pilot.pause()
            assert isinstance(pilot.app.screen, CommandPaletteModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, CommandPaletteModal)

    asyncio.run(driver())


# ── App-level wiring tests (5-6) ───────────────────────────────────────────


def test_action_show_command_palette_pushes_screen() -> None:
    """action_show_command_palette pushes CommandPaletteModal onto the screen stack."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            pilot.app.action_show_command_palette()
            await pilot.pause()
            assert isinstance(pilot.app.screen, CommandPaletteModal)

    asyncio.run(driver())


def test_on_palette_selected_dispatches() -> None:
    """_on_palette_selected sets composer text and posts Composer.Submitted."""

    async def driver() -> None:
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            cmd = SlashCommand(
                name="quit",
                description="Quit the application",
                handler=MagicMock(),
                aliases=("q", "exit"),
            )
            # Spy on app.post_message to capture the Submitted message
            posted: list[object] = []
            orig = pilot.app.post_message

            def spy(msg: object) -> object:
                posted.append(msg)
                return orig(msg)

            pilot.app.post_message = spy  # type: ignore[method-assign]
            try:
                pilot.app._on_palette_selected(cmd)
                await pilot.pause()
                assert any(
                    isinstance(m, Composer.Submitted) and m.value == "/quit "
                    for m in posted
                ), f"Expected Composer.Submitted('/quit '), got: {posted}"
            finally:
                pilot.app.post_message = orig

    asyncio.run(driver())
