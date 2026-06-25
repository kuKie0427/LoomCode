"""Tests for ThinkingMarker click/key fallback when no thinking content exists.

Regression: previously, click and Enter/Space on the spinner were silent no-ops
when the model wasn't streaming thinking (non-thinking model, or extended
thinking disabled). The user had no feedback that their interaction was
registered and concluded the spinner was broken.

Fix: when ``_display is None``, post a ShowNotification so the user gets
explicit feedback inline in the ChatLog (replacing Textual's toast notify).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from textual.events import Key

from loom.tui.chat_log import ThinkingMarker
from loom.tui.messages import ShowNotification


def test_click_toggles_when_display_exists() -> None:
    """Standard path: click should toggle the linked ThinkingDisplay."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    display = object()  # sentinel: any non-None
    marker._display = display
    marker.on_click(MagicMock())
    marker._on_toggle.assert_called_once_with(display)


def test_click_notifies_when_no_display() -> None:
    """No display yet → post notification instead of silent no-op."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    assert marker._display is None
    with patch.object(marker, "post_message") as mock_post:
        marker.on_click(MagicMock())
    mock_post.assert_called_once()
    message = mock_post.call_args.args[0]
    assert isinstance(message, ShowNotification)
    assert "thinking" in message.text.lower()
    marker._on_toggle.assert_not_called()


def test_enter_notifies_when_no_display() -> None:
    """Same fallback for keyboard Enter."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    assert marker._display is None
    with patch.object(marker, "post_message") as mock_post:
        marker.on_key(Key("enter", None))
    mock_post.assert_called_once()
    message = mock_post.call_args.args[0]
    assert isinstance(message, ShowNotification)
    assert "thinking" in message.text.lower()
    marker._on_toggle.assert_not_called()


def test_space_notifies_when_no_display() -> None:
    """Same fallback for keyboard Space."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    assert marker._display is None
    with patch.object(marker, "post_message") as mock_post:
        marker.on_key(Key("space", None))
    mock_post.assert_called_once()
    message = mock_post.call_args.args[0]
    assert isinstance(message, ShowNotification)
    assert "thinking" in message.text.lower()
    marker._on_toggle.assert_not_called()


def test_enter_toggles_when_display_exists() -> None:
    """Standard path for keyboard Enter."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    display = object()
    marker._display = display
    marker.on_key(Key("enter", None))
    marker._on_toggle.assert_called_once_with(display)
