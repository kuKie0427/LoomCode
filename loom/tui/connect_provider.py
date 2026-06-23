"""Connect provider modal for the loom TUI.

Lists all registered providers from PROVIDERS, distinguishes connected
vs unconnected via CredentialManager, and allows selecting a provider.
Connected providers return (provider_id, "") — the caller pushes ModelPicker.
Unconnected providers return (provider_id, None) — the caller pushes AuthInputModal.
ESC dismisses (returns None) — user cancelled.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ConnectProviderModal(ModalScreen[tuple[str, str | None] | None]):
    """Modal screen showing all registered providers with connection status.

    Connected providers return ``(provider_id, "")`` via dismiss —
    the app's handler pushes ModelPicker for that provider.
    Unconnected providers return ``(provider_id, None)`` via dismiss —
    the app's handler pushes AuthInputModal for that provider.
    ESC dismisses (returns ``None``) — user cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConnectProviderModal {
        align: center middle;
    }
    #connect-dialog {
        width: 60%;
        height: 70%;
        border: solid $accent;
        padding: 1;
        background: $surface 90%;
    }
    #connect-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    ListView {
        height: 1fr;
    }
    ListItem {
        padding: 0 1;
    }
    .section-header {
        text-style: bold underline;
        padding: 1 0 0 0;
        color: $accent;
    }
    .connected {
        color: $success;
    }
    """

    def compose(self) -> ComposeResult:
        from loom.agent.credential import credentials  # lazy import
        from loom.agent.providers import PROVIDERS  # lazy import

        connected_items: list[ListItem] = []
        unconnected_items: list[ListItem] = []

        for pid in sorted(PROVIDERS):
            try:
                inst = PROVIDERS[pid](api_key="", base_url=None)
                display = inst.display_name or pid
                # Show context window of first model as representative metadata.
                cw_str = ""
                if inst.supported_models:
                    try:
                        cw = inst.context_window(inst.supported_models[0])
                        cw_str = f" ({cw:,} ctx)"
                    except Exception:
                        cw_str = ""
            except Exception:
                display = pid
                cw_str = ""

            cred = credentials.get(pid)
            if cred is not None:
                connected_items.append(
                    ListItem(
                        Label(f"✓ {display}{cw_str}", classes="connected"),
                        id=f"connect:{pid}",
                    )
                )
            else:
                unconnected_items.append(
                    ListItem(
                        Label(f"  {display}{cw_str} (not connected)"),
                        id=f"connect:{pid}",
                    )
                )

        with Container(id="connect-dialog"):
            yield Static("Connect a Provider", id="connect-title")
            with Vertical():
                all_items: list[ListItem] = []
                if connected_items:
                    all_items.append(
                        ListItem(
                            Label("── Connected ──", classes="section-header")
                        )
                    )
                    all_items.extend(connected_items)
                if unconnected_items:
                    all_items.append(
                        ListItem(
                            Label("── Not Connected ──", classes="section-header")
                        )
                    )
                    all_items.extend(unconnected_items)
                yield ListView(*all_items)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        if event.item.id.startswith("connect:"):
            pid = event.item.id[len("connect:"):]
            from loom.agent.credential import credentials  # lazy import

            cred = credentials.get(pid)
            if cred is not None:
                # Connected → return (pid, "") to signal "show model picker"
                self.dismiss((pid, ""))
            else:
                # Unconnected → return (pid, None) to signal "show auth input"
                self.dismiss((pid, None))

    def action_cancel(self) -> None:
        self.dismiss(None)
