"""Tests for the unified inline notification system (P1-2).

Replaces Textual's notify() toasts with ShowNotification messages that the
AgentTUIApp renders as SystemNotes in the ChatLog.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from textual.app import App

from loom.tui.chat_log import ChatLog
from loom.tui.messages import ShowNotification


def test_show_notification_message_attributes():
    """ShowNotification carries text and severity."""
    msg = ShowNotification("hello", severity="warning")
    assert msg.text == "hello"
    assert msg.severity == "warning"


def test_app_handles_show_notification():
    """AgentTUIApp appends a SystemNote when ShowNotification bubbles up."""
    from loom.tui.app import AgentTUIApp

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 24)) as pilot:
            pilot.app.show_notification("Test notification", severity="info")
            await pilot.pause(0.05)
            notes = pilot.app.query("#chat-log SystemNote")
            assert len(notes) == 1
            assert "Test notification" in str(notes[0].render())

    asyncio.run(driver())


def test_chat_log_append_system_note_severity_colors():
    """append_system_note embeds severity as a color token."""

    async def driver():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            chat = ChatLog(id="chat-log")
            pilot.app.mount(chat)
            await pilot.pause(0.05)
            chat.append_system_note("error msg", severity="error")
            await pilot.pause(0.05)
            notes = pilot.app.query("SystemNote")
            assert len(notes) == 1
            rendered = notes[0].render()
            assert "error msg" in str(rendered)
            assert any("$error" in str(span.style) for span in rendered.spans)

    asyncio.run(driver())


def test_show_notification_helper_posts_message():
    """show_notification() posts a ShowNotification to the app."""
    app = App()
    with patch.object(app, "post_message") as mock_post:
        from loom.tui.app import AgentTUIApp

        # show_notification is defined on AgentTUIApp but works on any App.
        AgentTUIApp.show_notification(app, "hello", severity="success")

    mock_post.assert_called_once()
    msg = mock_post.call_args.args[0]
    assert isinstance(msg, ShowNotification)
    assert msg.text == "hello"
    assert msg.severity == "success"
