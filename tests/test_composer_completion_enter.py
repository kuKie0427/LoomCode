"""Tests for Enter-key behavior when the / completion popup is visible.

Regression: typing `/` then Enter used to raise IndexError (empty command)
or submit the raw `/` text. Now, when the completion popup is visible and
a command is highlighted, Enter executes that command directly (one-step
run), matching the user's expectation from the popup.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_completion_enter_message_exists() -> None:
    """Composer exposes a CompletionEnter message class."""
    from loom.tui.composer import Composer

    assert hasattr(Composer, "CompletionEnter")
    assert hasattr(Composer.CompletionEnter, "__mro__")


def test_enter_with_slash_and_no_space_posts_completion_enter() -> None:
    """When text starts with `/` and has no space, Enter posts CompletionEnter
    instead of Submitted (so the app can execute the highlighted command).
    """

    async def driver() -> None:
        from loom.tui.app import AgentTUIApp
        from loom.tui.composer import Composer

        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            # Dismiss welcome modal if present.
            try:
                from loom.tui.welcome import WelcomeModal

                app.query_one(WelcomeModal).dismiss("")
                await pilot.pause(0.2)
            except Exception:
                pass

            composer = app.query_one(Composer)
            composer.text = "/qu"  # / prefix, no space, popup would be visible
            await pilot.pause(0.1)

            posted: list[object] = []
            orig = composer.post_message

            def spy(msg: object) -> object:
                posted.append(msg)
                return orig(msg)

            composer.post_message = spy  # type: ignore[method-assign]
            try:
                await composer._on_key(_make_key("enter"))
                await pilot.pause(0.1)
            finally:
                composer.post_message = orig

            # CompletionEnter should be posted, NOT Submitted.
            assert any(
                isinstance(m, Composer.CompletionEnter) for m in posted
            ), f"Expected CompletionEnter, got: {posted}"
            assert not any(
                isinstance(m, Composer.Submitted) for m in posted
            ), f"Should not post Submitted when popup is visible, got: {posted}"

    asyncio.run(driver())


def test_enter_without_slash_posts_submitted() -> None:
    """Normal text (no / prefix) still posts Submitted on Enter.

    Uses a bare Composer (not inside AgentTUIApp) to avoid triggering
    run_agent_turn, which would require a fully-mounted ChatLog.
    """

    async def driver() -> None:
        from textual.app import App, ComposeResult

        from loom.tui.composer import Composer

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield Composer(id="composer")

        app = TestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "hello world"  # no / prefix
            await pilot.pause(0.1)

            posted: list[object] = []
            orig = composer.post_message

            def spy(msg: object) -> object:
                posted.append(msg)
                return orig(msg)

            composer.post_message = spy  # type: ignore[method-assign]
            try:
                await composer._on_key(_make_key("enter"))
                await pilot.pause(0.1)
            finally:
                composer.post_message = orig

            assert any(
                isinstance(m, Composer.Submitted) and m.value == "hello world"
                for m in posted
            ), f"Expected Submitted('hello world'), got: {posted}"

    asyncio.run(driver())


def test_enter_with_slash_and_space_posts_submitted() -> None:
    """`/model claude` (slash + space) should submit normally, not trigger
    completion (the popup is only for the command-name portion).

    Uses a bare Composer to avoid triggering run_slash_command.
    """

    async def driver() -> None:
        from textual.app import App, ComposeResult

        from loom.tui.composer import Composer

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield Composer(id="composer")

        app = TestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "/model claude"  # / prefix WITH space
            await pilot.pause(0.1)

            posted: list[object] = []
            orig = composer.post_message

            def spy(msg: object) -> object:
                posted.append(msg)
                return orig(msg)

            composer.post_message = spy  # type: ignore[method-assign]
            try:
                await composer._on_key(_make_key("enter"))
                await pilot.pause(0.1)
            finally:
                composer.post_message = orig

            assert any(
                isinstance(m, Composer.Submitted) and m.value == "/model claude"
                for m in posted
            ), f"Expected Submitted('/model claude'), got: {posted}"

    asyncio.run(driver())


def test_on_composer_completion_enter_runs_command() -> None:
    """on_composer_completion_enter executes the highlighted command directly."""

    async def driver() -> None:
        from loom.tui.app import AgentTUIApp
        from loom.tui.composer import Composer

        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            try:
                from loom.tui.welcome import WelcomeModal

                app.query_one(WelcomeModal).dismiss("")
                await pilot.pause(0.2)
            except Exception:
                pass

            # Seed the completer with a known command highlighted.
            from loom.tui.completer import CommandCompleter
            completer = app.query_one(CommandCompleter)
            completer.show_for("/qu")  # matches "quit"
            await pilot.pause(0.1)
            assert completer.current() is not None
            assert completer.current().name == "quit"

            # Spy on run_slash_command to verify it's called with "quit".
            called_with: list[str] = []
            orig_run = app.run_slash_command

            async def spy_run(cmd_line: str) -> None:
                called_with.append(cmd_line)

            app.run_slash_command = spy_run  # type: ignore[method-assign]
            try:
                await app.on_composer_completion_enter(Composer.CompletionEnter())
                await pilot.pause(0.1)
            finally:
                app.run_slash_command = orig_run  # type: ignore[method-assign]

            assert called_with == ["quit"], (
                f"Expected run_slash_command('quit'), got: {called_with}"
            )
            # Composer text should be cleared.
            assert app.query_one(Composer).text == ""

    asyncio.run(driver())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key(key: str):
    """Build a minimal mock key event with a `.key` attribute."""
    mock = MagicMock()
    mock.key = key
    mock.prevent_default = MagicMock()
    mock.stop = MagicMock()
    return mock
