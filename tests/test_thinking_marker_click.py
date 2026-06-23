"""Tests for ThinkingMarker click/press fallback when no thinking content exists.

Regression: previously, click and press on the spinner were silent no-ops
when the model wasn't streaming thinking (non-thinking model, or extended
thinking disabled). The user had no feedback that their click was registered
and concluded the spinner was broken.

Fix: when ``_display is None``, fall through to ``self.notify(...)`` so the
user gets explicit feedback ("No thinking content for this response").
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from loom.tui.chat_log import ThinkingMarker


def test_click_toggles_when_display_exists() -> None:
    """Standard path: click should toggle the linked ThinkingDisplay."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    display = object()  # sentinel: any non-None
    marker._display = display
    marker.on_click(MagicMock())
    marker._on_toggle.assert_called_once_with(display)


def test_click_notifies_when_no_display() -> None:
    """No display yet → notify the user instead of silent no-op."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    assert marker._display is None
    with patch.object(marker, "notify") as mock_notify:
        marker.on_click(MagicMock())
    mock_notify.assert_called_once()
    assert "thinking" in mock_notify.call_args.args[0].lower()
    marker._on_toggle.assert_not_called()


def test_press_notifies_when_no_display() -> None:
    """Same fallback for keyboard press (Enter/Space)."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    assert marker._display is None
    with patch.object(marker, "notify") as mock_notify:
        marker.on_press()
    mock_notify.assert_called_once()
    assert "thinking" in mock_notify.call_args.args[0].lower()
    marker._on_toggle.assert_not_called()


def test_press_toggles_when_display_exists() -> None:
    """Standard path for keyboard press."""
    marker = ThinkingMarker(on_toggle=MagicMock())
    display = object()
    marker._display = display
    marker.on_press()
    marker._on_toggle.assert_called_once_with(display)
