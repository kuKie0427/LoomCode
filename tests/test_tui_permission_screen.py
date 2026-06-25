"""Tests for the TUI PermissionScreen (P1-1 scope clarification)."""

from __future__ import annotations

import asyncio

from loom.agent.permission_store import WORKSPACE_WRITE_TOOLS
from loom.tui.screens import PermissionScreen


def test_permission_screen_shows_scope_for_non_write_tools():
    """Allow-always scope is shown for tools that can be persisted."""
    screen = PermissionScreen("bash", {"command": "ls"}, "Run shell command?")
    text = screen._scope_text()
    assert ".minicode/permissions.json" in text
    assert "30 days" in text


def test_permission_screen_shows_write_tool_scope():
    """Write/edit tools explain that Allow always is unavailable."""
    for tool in WORKSPACE_WRITE_TOOLS:
        screen = PermissionScreen(tool, {"path": "x"}, "Write file?")
        text = screen._scope_text()
        assert "not available" in text or "re-prompted" in text


def test_permission_screen_compose_includes_scope_widget():
    """The composed dialog includes the scope explanation Static."""
    from textual.app import App

    class PermApp(App):
        pass

    async def driver():
        app = PermApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = PermissionScreen("bash", {"command": "ls"}, "Run shell command?")
            pilot.app.push_screen(screen)
            await pilot.pause(0.05)
            scope = screen.query_one("#perm-scope")
            assert scope is not None

    asyncio.run(driver())


def test_permission_screen_buttons_dismiss_correctly():
    """Each button dismisses with the expected result string."""
    results: list[str] = []

    async def driver():
        screen = PermissionScreen("bash", {"command": "ls"}, "Run?")
        screen.dismiss = results.append
        # Simulate button presses via the handler.
        from textual.widgets import Button

        for button_id, expected in (
            ("btn-allow", "allow"),
            ("btn-allow-always", "allow_always"),
            ("btn-deny", "deny"),
        ):
            results.clear()
            btn = Button("x", id=button_id)
            screen.on_button_pressed(Button.Pressed(btn))
            assert results == [expected], f"{button_id} should dismiss {expected}, got {results}"

    asyncio.run(driver())


def test_permission_screen_keyboard_shortcuts_dismiss_correctly():
    """Action methods dismiss with the expected result string."""
    results: list[str] = []
    screen = PermissionScreen("bash", {"command": "ls"}, "Run?")
    screen.dismiss = results.append

    screen.action_allow_once()
    assert results == ["allow"]

    results.clear()
    screen.action_allow_always()
    assert results == ["allow_always"]

    results.clear()
    screen.action_deny()
    assert results == ["deny"]
