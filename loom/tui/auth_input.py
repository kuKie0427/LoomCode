"""Auth input modal for the loom TUI.

Allows the user to enter an API key and optional base URL for a provider,
and saves the credential via CredentialManager.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class AuthInputModal(ModalScreen[str | None]):
    """Modal screen for entering an API key and optional base URL.

    Shows a dialog with a masked API key input, a collapsible base URL
    section, and Save/Cancel buttons. On successful save the credential
    is persisted via ``credentials.set()`` and the modal dismisses with
    the ``provider_id`` string. On cancel it dismisses with ``None``.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    AuthInputModal {
        align: center middle;
    }
    #auth-dialog {
        width: 50%;
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
        margin: 0 0 1 0;
    }
    #auth-url-section {
        margin: 0 0 1 0;
    }
    #auth-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }
    Button {
        margin: 0 1;
    }
    .url-hidden {
        display: none;
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
            with Container(id="auth-url-section"):
                yield Button(
                    "Advanced: Base URL (optional)",
                    id="auth-toggle-url",
                    variant="default",
                )
                yield Input(
                    placeholder="https://api.example.com/v1",
                    id="auth-url-input",
                    classes="url-hidden",
                )
            with Horizontal(id="auth-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#auth-key-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-cancel":
            self.action_cancel()
        elif bid == "btn-save":
            self._do_save()
        elif bid == "auth-toggle-url":
            self._toggle_url_section()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_save()

    def _toggle_url_section(self) -> None:
        url_input = self.query_one("#auth-url-input", Input)
        toggle_btn = self.query_one("#auth-toggle-url", Button)

        if url_input.has_class("url-hidden"):
            url_input.remove_class("url-hidden")
            toggle_btn.label = "Hide base URL"
        else:
            url_input.add_class("url-hidden")
            toggle_btn.label = "Advanced: Base URL (optional)"

    def _do_save(self) -> None:
        from loom.agent.credential import CredentialInfo, credentials

        key_input = self.query_one("#auth-key-input", Input)
        api_key = key_input.value.strip()

        if not api_key:
            self.app.notify("API key is required", severity="error")
            key_input.focus()
            return

        url_input = self.query_one("#auth-url-input", Input)
        base_url = url_input.value.strip() or None

        info = CredentialInfo(
            provider_id=self._provider_id,
            api_key=api_key,
            base_url=base_url,
            kind="api",
        )
        credentials.set(self._provider_id, info)

        # Look up display name again (it may have changed between compose and save).
        display_name = self._lookup_display_name()
        self.app.notify(
            f"Logged in to {display_name}",
            severity="information",
        )
        self.dismiss(self._provider_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
