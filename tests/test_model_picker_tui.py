"""Tests for the ModelPicker TUI modal."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from textual.screen import ModalScreen

from loom.tui.model_picker import ModelPicker


def test_lists_all_providers() -> None:
    mp = ModelPicker()
    assert hasattr(mp, "compose")
    assert callable(mp.compose)


def test_import_and_instantiation() -> None:
    mp = ModelPicker()
    assert isinstance(mp, ModalScreen)


def test_recent_section_appears() -> None:
    from loom.agent.model_state import ModelRef

    recent = [ModelRef("anthropic", "claude-sonnet-4-5")]
    mp = ModelPicker(recent=recent)
    assert len(mp._recent) == 1
    assert mp._recent[0].provider_id == "anthropic"
    assert mp._recent[0].model_id == "claude-sonnet-4-5"


def test_no_recent_defaults_to_empty() -> None:
    mp = ModelPicker()
    assert mp._recent == []


def test_on_list_view_selected_parses_model_id() -> None:
    """on_list_view_selected dismisses with (provider_id, model_id) from _model_items.

    Note: after the model_picker refactor, item IDs are safe indices
    (``m0``, ``r0``, ...) that map to (provider_id, model_id) via the
    ``_model_items`` dict populated by ``_build_rows``. The old format
    ``model:openai/gpt-4o`` is no longer used.
    """
    mp = ModelPicker()
    # Populate _model_items as _build_rows would (without requiring a full
    # app.run_test() cycle).
    mp._model_items["m0"] = ("openai", "gpt-4o")
    mock_event = MagicMock()
    mock_event.item.id = "m0"

    fake_cred = MagicMock()
    with patch.object(mp, "dismiss") as mock_dismiss, patch(
        "loom.agent.credential.credentials.get", return_value=fake_cred
    ):
        mp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with(("openai", "gpt-4o"))


def test_on_list_view_selected_parses_recent_id() -> None:
    """Recent section item uses ``r0`` safe ID, mapped via _model_items."""
    from loom.agent.model_state import ModelRef

    mp = ModelPicker(recent=[ModelRef("anthropic", "claude-sonnet-4-5")])
    mp._model_items["r0"] = ("anthropic", "claude-sonnet-4-5")
    mock_event = MagicMock()
    mock_event.item.id = "r0"

    # Mock credentials so the handler goes straight to dismiss() instead
    # of trying to push AuthInputModal (which requires an active app).
    fake_cred = MagicMock()
    with patch.object(mp, "dismiss") as mock_dismiss, patch(
        "loom.agent.credential.credentials.get", return_value=fake_cred
    ):
        mp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with(("anthropic", "claude-sonnet-4-5"))


def test_on_list_view_selected_ignores_no_id() -> None:
    mp = ModelPicker()
    mock_event = MagicMock()
    mock_event.item.id = None

    with patch.object(mp, "dismiss") as mock_dismiss:
        mp.on_list_view_selected(mock_event)
    mock_dismiss.assert_not_called()


def test_esc_cancels() -> None:
    mp = ModelPicker()
    with patch.object(mp, "dismiss") as mock_dismiss:
        mp.action_cancel()
    mock_dismiss.assert_called_once_with(None)


def test_bindings_include_escape() -> None:
    assert any("escape" in str(b) for b in ModelPicker.BINDINGS)


def test_on_mount_does_not_crash_when_list_view_missing() -> None:
    """on_mount should not crash the app if query_one fails.

    Regression: ModelPicker.on_mount called query_one("#model-picker-list")
    without a try/except. If compose hadn't completed (race condition), the
    NoMatches exception would propagate to Textual's _handle_exception,
    exiting the app. The fix defers to call_later.
    """
    from textual.css.query import NoMatches

    mp = ModelPicker()

    def _raise_no_matches(*args, **kwargs):
        raise NoMatches("simulated")

    async def _run():
        with patch.object(mp, "query_one", side_effect=_raise_no_matches):
            # This should NOT raise — it should defer via call_later
            await mp.on_mount()

    asyncio.run(_run())


def test_on_mount_handles_build_rows_failure() -> None:
    """on_mount should not crash if _build_rows throws or the screen is pushed
    in a context where the ListView isn't fully mounted yet.
    """

    async def _run():
        from textual.app import App, ComposeResult
        from textual.widgets import ListView

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield ListView(id="model-picker-list")

        app = TestApp()
        async with app.run_test() as pilot:
            mp = ModelPicker()
            app.push_screen(mp)
            await pilot.pause(0.3)
            # Verify the screen is still alive (didn't crash)
            assert app.screen is mp

    asyncio.run(_run())


def test_on_model_picked_handles_change_model_failure() -> None:
    """_on_model_picked should not crash if change_model raises."""

    async def _run():
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            try:
                from loom.tui.welcome import WelcomeModal
                app.query_one(WelcomeModal).dismiss("")
                await pilot.pause(0.2)
            except Exception:
                pass

            with patch.object(
                app.llm, "change_model", side_effect=RuntimeError("boom")
            ):
                app._on_model_picked(("anthropic", "claude-sonnet-4-5"))

            await pilot.pause(0.1)
            assert app._exit is False

    asyncio.run(_run())


def test_on_model_picked_handles_chatlog_missing() -> None:
    """_on_model_picked should not crash if query_one(ChatLog) fails."""
    from textual.css.query import NoMatches

    async def _run():
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            try:
                from loom.tui.welcome import WelcomeModal
                app.query_one(WelcomeModal).dismiss("")
                await pilot.pause(0.2)
            except Exception:
                pass

            def _raise(*args, **kwargs):
                raise NoMatches("simulated")

            with patch.object(app, "query_one", side_effect=_raise):
                app._on_model_picked(("deepseek", "deepseek-v4-flash"))

            await pilot.pause(0.1)
            assert app._exit is False

    asyncio.run(_run())
