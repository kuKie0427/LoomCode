"""Tests for the ConnectProvider and AuthInput TUI modals."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from textual.screen import ModalScreen

from loom.tui.auth_input import AuthInputModal
from loom.tui.connect_provider import ConnectProviderModal


def test_connect_modal_lists_all_providers() -> None:
    """Verify the modal has compose() and is a ModalScreen."""
    cp = ConnectProviderModal()
    assert hasattr(cp, "compose")
    assert callable(cp.compose)
    assert isinstance(cp, ModalScreen)


def test_connect_modal_import_and_instantiation() -> None:
    """Verify BINDINGS include escape."""
    assert any("escape" in str(b) for b in ConnectProviderModal.BINDINGS)


def test_connect_modal_shows_connected_providers() -> None:
    """When credentials.get('anthropic') returns a key, dismiss with ('anthropic', '').

    Connected → signal to show model picker.
    """
    cp = ConnectProviderModal()
    mock_event = MagicMock()
    mock_event.item.id = "connect:anthropic"

    # Both modals do `from loom.agent.credential import credentials` inside
    # method bodies, so we patch the canonical module attribute.
    with patch("loom.agent.credential.credentials") as mock_creds:
        mock_creds.get.return_value = MagicMock()  # non-None = connected
        with patch.object(cp, "dismiss") as mock_dismiss:
            cp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with(("anthropic", ""))


def test_connect_modal_unconnected_shows_no_key() -> None:
    """When credentials.get('anthropic') returns None, dismiss with ('anthropic', None).

    Unconnected → signal to show auth input modal.
    """
    cp = ConnectProviderModal()
    mock_event = MagicMock()
    mock_event.item.id = "connect:anthropic"

    with patch("loom.agent.credential.credentials") as mock_creds:
        mock_creds.get.return_value = None  # None = not connected
        with patch.object(cp, "dismiss") as mock_dismiss:
            cp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with(("anthropic", None))


def test_connect_modal_ignores_no_id() -> None:
    """If event.item.id is None, do nothing."""
    cp = ConnectProviderModal()
    mock_event = MagicMock()
    mock_event.item.id = None

    with patch.object(cp, "dismiss") as mock_dismiss:
        cp.on_list_view_selected(mock_event)
    mock_dismiss.assert_not_called()


def test_connect_modal_esc_cancels() -> None:
    """ESC dismisses with None."""
    cp = ConnectProviderModal()
    with patch.object(cp, "dismiss") as mock_dismiss:
        cp.action_cancel()
    mock_dismiss.assert_called_once_with(None)


def test_connect_modal_ignores_non_connect_id() -> None:
    """If event.item.id does not start with 'connect:', do nothing."""
    cp = ConnectProviderModal()
    mock_event = MagicMock()
    mock_event.item.id = "model:anthropic/claude-sonnet-4"

    with patch.object(cp, "dismiss") as mock_dismiss:
        cp.on_list_view_selected(mock_event)
    mock_dismiss.assert_not_called()


# ── AuthInputModal tests ────────────────────────────────────────────────────


def test_auth_input_modal_instantiation() -> None:
    """Verify AuthInputModal is a ModalScreen and has compose."""
    aim = AuthInputModal("anthropic")
    assert hasattr(aim, "compose")
    assert callable(aim.compose)
    assert isinstance(aim, ModalScreen)
    assert aim._provider_id == "anthropic"


def test_auth_input_modal_saves_credential() -> None:
    """When Save is pressed with a valid API key, credentials.set() is called
    and dismiss() with provider_id."""
    aim = AuthInputModal("anthropic")

    # app is a Textual property backed by active_app ContextVar;
    # patch the ContextVar so self.app resolves without error.
    mock_app = MagicMock()
    with patch("textual.message_pump.active_app") as mock_ctx:
        mock_ctx.get.return_value = mock_app

        with patch("loom.agent.credential.credentials") as mock_creds:
            with patch.object(aim, "query_one") as mock_query:
                key_input = MagicMock()
                key_input.value = "sk-valid-key"
                url_input = MagicMock()
                url_input.value = ""

                def query_side_effect(id_str: str, widget_type: type | None = None) -> MagicMock:
                    if id_str == "#auth-key-input":
                        return key_input
                    elif id_str == "#auth-url-input":
                        return url_input
                    return MagicMock()

                mock_query.side_effect = query_side_effect

                with patch.object(aim, "dismiss") as mock_dismiss:
                    aim._do_save()

    mock_creds.set.assert_called_once()
    args, kwargs = mock_creds.set.call_args
    assert args[0] == "anthropic"  # provider_id
    assert args[1].api_key == "sk-valid-key"  # CredentialInfo.api_key
    assert args[1].provider_id == "anthropic"
    mock_dismiss.assert_called_once_with("anthropic")


def test_auth_input_modal_requires_api_key() -> None:
    """Empty API key shows error and does not dismiss."""
    aim = AuthInputModal("anthropic")

    mock_app = MagicMock()
    with patch("textual.message_pump.active_app") as mock_ctx:
        mock_ctx.get.return_value = mock_app

        with patch.object(aim, "query_one") as mock_query:
            key_input = MagicMock()
            key_input.value = ""
            url_input = MagicMock()
            url_input.value = ""

            def query_side_effect(id_str: str, widget_type: type | None = None) -> MagicMock:
                if id_str == "#auth-key-input":
                    return key_input
                elif id_str == "#auth-url-input":
                    return url_input
                return MagicMock()

            mock_query.side_effect = query_side_effect

            with patch.object(aim, "dismiss") as mock_dismiss:
                aim._do_save()

    mock_app.notify.assert_called_once()
    mock_dismiss.assert_not_called()


def test_auth_input_modal_esc_cancels() -> None:
    """ESC dismisses with None."""
    aim = AuthInputModal("anthropic")
    with patch.object(aim, "dismiss") as mock_dismiss:
        aim.action_cancel()
    mock_dismiss.assert_called_once_with(None)


def test_auth_input_modal_bindings() -> None:
    """Verify BINDINGS include escape."""
    assert any("escape" in str(b) for b in AuthInputModal.BINDINGS)


def test_auth_input_modal_looks_up_display_name() -> None:
    """Verify _lookup_display_name returns a non-empty string.

    This exercises the lazy import of PROVIDERS — even if the provider
    instantiation fails, the fallback is the provider_id itself.
    """
    aim = AuthInputModal("anthropic")
    name = aim._lookup_display_name()
    assert isinstance(name, str)
    assert len(name) > 0


# ── App slash command tests ────────────────────────────────────────────────


def test_app_has_connect_handler() -> None:
    """Verify _on_connect_done and _on_connect_auth_done exist on app."""
    from loom.tui import app as app_module

    source = inspect.getsource(app_module.AgentTUIApp.run_slash_command)
    assert "connect" in source

    # Check that handler methods exist
    assert hasattr(app_module.AgentTUIApp, "_on_connect_done")
    assert hasattr(app_module.AgentTUIApp, "_on_connect_auth_done")


# ── Chat log hint test ─────────────────────────────────────────────────────


def test_chat_log_includes_connect() -> None:
    """The WelcomeBanner command hint includes /connect."""
    from loom.tui.chat_log import WelcomeBanner

    banner = WelcomeBanner()
    body = banner.render()
    assert "/connect" in str(body)
