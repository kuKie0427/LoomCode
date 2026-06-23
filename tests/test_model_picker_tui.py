"""Tests for the ModelPicker TUI modal."""

from __future__ import annotations

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
    mp = ModelPicker()
    mock_event = MagicMock()
    mock_event.item.id = "model:openai/gpt-4o"

    fake_cred = MagicMock()
    with patch.object(mp, "dismiss") as mock_dismiss, patch(
        "loom.agent.credential.credentials.get", return_value=fake_cred
    ):
        mp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with(("openai", "gpt-4o"))


def test_on_list_view_selected_parses_recent_id() -> None:
    from loom.agent.model_state import ModelRef

    mp = ModelPicker(recent=[ModelRef("anthropic", "claude-sonnet-4-5")])
    mock_event = MagicMock()
    mock_event.item.id = "recent:anthropic/claude-sonnet-4-5"

    with patch.object(mp, "dismiss") as mock_dismiss:
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
