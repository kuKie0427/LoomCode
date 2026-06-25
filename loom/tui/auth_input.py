"""Auth input modal for the loom TUI.

Allows the user to enter an API key for a provider and saves the
credential via CredentialManager.  The provider's built-in base URL
is used automatically — no URL input needed.

Keyboard-driven: Enter to save, Esc to cancel.  No buttons.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class AuthInputModal(ModalScreen[str | None]):
    """Modal screen for entering an API key.

    ``Enter`` saves the credential and dismisses with the ``provider_id``.
    ``Esc`` dismisses with ``None``.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    AuthInputModal {
        align: center middle;
    }
    #auth-dialog {
        width: 70%;
        height: auto;
        border: solid $accent;
        padding: 1;
        background: $surface 90%;
    }
    #auth-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    #auth-key-input {
        margin: 0;
    }
    #auth-error {
        color: $error;
        text-style: bold;
        padding: 1 0 0 0;
        height: auto;
    }
    """

    def __init__(self, provider_id: str) -> None:
        super().__init__()
        self._provider_id = provider_id

    def _lookup_display_name(self) -> str:
        from loom.agent.providers import PROVIDERS

        try:
            inst = PROVIDERS[self._provider_id](api_key="", base_url=None)
            return inst.display_name or self._provider_id
        except Exception:
            return self._provider_id

    def compose(self) -> ComposeResult:
        display_name = self._lookup_display_name()
        with Container(id="auth-dialog"):
            yield Static(f"Login to {display_name}", id="auth-title")
            yield Input(
                placeholder="API key (sk-...)",
                password=True,
                id="auth-key-input",
            )
            yield Static("", id="auth-error")

    def on_mount(self) -> None:
        self.query_one("#auth-key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_save()

    def _do_save(self) -> None:
        from loom.agent.credential import CredentialInfo, credentials

        key_input = self.query_one("#auth-key-input", Input)
        api_key = key_input.value.strip()
        error_label = self.query_one("#auth-error", Static)

        if not api_key:
            error_label.update("API key is required")
            key_input.focus()
            return

        info = CredentialInfo(
            provider_id=self._provider_id,
            api_key=api_key,
            kind="api",
        )
        credentials.set(self._provider_id, info)

        display_name = self._lookup_display_name()
        error_label.update("")
        self.app.show_notification(f"Logged in to {display_name}", severity="success")
        self.dismiss(self._provider_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
