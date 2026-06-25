"""Tests for provider status indicators in the TUI.

Covers the three places where provider credential status is surfaced:
  1. ModelPicker list items (✓ when credentials are configured)
  2. /status slash command output (provider-by-provider breakdown)
  3. StatusBar render (model line with ✓ / ✗ suffix)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from textual.widgets import ListItem, ListView

from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import ChatLog
from loom.tui.model_picker import ModelPicker
from loom.tui.status_bar import StatusBar


def test_model_picker_shows_connected_check() -> None:
    """ModelPicker compose: connected provider labels contain ●.

    Note: the indicator was changed from ✓ to ● (model_picker.py:173)
    to match the design spec's section-header style. The StatusBar still
    uses ✓ for its own compact layout; ModelPicker uses ● for the
    full-width section header.
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            fake_cred = MagicMock()
            with patch("loom.agent.credential.credentials.get", return_value=fake_cred):
                mp = ModelPicker()
                app.push_screen(mp)
                await pilot.pause(0.4)
                list_view = mp.query_one(ListView)
                found = False
                for item in list_view.children:
                    if isinstance(item, ListItem) and item.children:
                        label = item.children[0]
                        rendered = label.render()
                        rendered_str = str(rendered)
                        if "●" in rendered_str:
                            found = True
                            break
                assert found, "ModelPicker should show ● for connected providers"

    asyncio.run(driver())


def test_model_picker_shows_disconnected_state() -> None:
    """ModelPicker compose: disconnected provider labels have no ✓."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            with patch("loom.agent.credential.credentials.get", return_value=None):
                mp = ModelPicker()
                app.push_screen(mp)
                await pilot.pause(0.2)
                list_view = mp.query_one(ListView)
                for item in list_view.children:
                    if isinstance(item, ListItem) and item.children:
                        label = item.children[0]
                        rendered = label.render()
                        rendered_str = str(rendered)
                        assert "✓" not in rendered_str, (
                            f"Disconnected provider should NOT show ✓, got: {rendered_str!r}"
                        )

    asyncio.run(driver())


def test_status_command_lists_providers() -> None:
    """/status command shows provider names and ✓ credential indicators."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            chat_log = app.query_one(ChatLog)
            fake_cred = MagicMock()
            with (
                patch(
                    "loom.agent.credential.credentials.all",
                    return_value={"anthropic": fake_cred},
                ),
                patch.object(chat_log, "append_system_note") as mock_append,
            ):
                await app.run_slash_command("status")
                mock_append.assert_called_once()
                text = mock_append.call_args[0][0]
                assert "anthropic" in text, (
                    f"Status text should mention anthropic provider, got: {text!r}"
                )
                assert "✓" in text, (
                    f"Connected provider should show ✓ in status, got: {text!r}"
                )

    asyncio.run(driver())


def test_status_bar_shows_model_with_provider() -> None:
    """StatusBar.render() includes model name and ✓/✗ credential indicator."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            status_bar = app.query_one(StatusBar)
            model = app.llm.model
            assert model is not None and isinstance(model, str)

            # Connected: status line includes ✓
            with patch("loom.agent.credential.credentials.get", return_value=MagicMock()):
                text = status_bar.render()
                assert model in text, (
                    f"StatusBar should show model {model!r}, got: {text!r}"
                )
                assert "[$accent]✓[/]" in text, (
                    f"Connected provider should show ✓, got: {text!r}"
                )

            # Disconnected: status line includes ✗
            with patch("loom.agent.credential.credentials.get", return_value=None):
                text = status_bar.render()
                assert "[$text-muted]✗[/]" in text or "✗" in text, (
                    f"Disconnected provider should show ✗, got: {text!r}"
                )

    asyncio.run(driver())
