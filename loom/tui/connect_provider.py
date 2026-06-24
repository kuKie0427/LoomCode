"""Connect provider modal for the loom TUI.

Lists all registered providers from PROVIDERS, distinguishes connected
vs unconnected via CredentialManager, and allows selecting a provider.
Connected providers return (provider_id, "") — the caller pushes ModelPicker.
Unconnected providers return (provider_id, None) — the caller pushes AuthInputModal.
ESC dismisses (returns None) — user cancelled.
"""

from __future__ import annotations

import re

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

    def __init__(self) -> None:
        super().__init__()
        self._safe_id_map: dict[str, str] = {}  # safe_id → original_pid

    @staticmethod
    def _safe_id(pid: str) -> str:
        """Sanitize a provider ID for use as a Textual widget ID.

        Textual IDs may only contain letters, numbers, underscores, or
        hyphens.  Replace disallowed characters (dots, colons, etc.)
        with hyphens.
        """
        return f"connect-{re.sub(r'[^a-zA-Z0-9_-]', '-', pid)}"

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
            except Exception:
                display = pid

            cred = credentials.get(pid)
            if cred is not None:
                connected_items.append(
                    ListItem(
                        Label(f"✓ {display}", classes="connected"),
                        id=self._safe_id(pid),
                    )
                )
                self._safe_id_map[self._safe_id(pid)] = pid
            else:
                unconnected_items.append(
                    ListItem(
                        Label(f"  {display} (not connected)"),
                        id=self._safe_id(pid),
                    )
                )
                self._safe_id_map[self._safe_id(pid)] = pid

        with Container(id="connect-dialog"):
            yield Static("Connect a Provider", id="connect-title")
            with Vertical():
                all_items: list[ListItem] = []
                if connected_items:
                    all_items.append(
                        ListItem(
                            Label("── Connected ──", classes="section-header"),
                            disabled=True,
                        )
                    )
                    all_items.extend(connected_items)
                if unconnected_items:
                    all_items.append(
                        ListItem(
                            Label("── Not Connected ──", classes="section-header"),
                            disabled=True,
                        )
                    )
                    all_items.extend(unconnected_items)
                yield ListView(*all_items, id="provider-list")

    def on_mount(self) -> None:
        """Skip disabled section headers — start on the first real provider."""
        lv = self.query_one("#provider-list", ListView)
        for i, child in enumerate(lv.children):
            if not child.disabled:
                lv.index = i
                break

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        pid = self._safe_id_map.get(event.item.id)
        if pid is None and event.item.id.startswith("connect-"):
            pid = event.item.id[len("connect-"):]
        if pid is None and event.item.id.startswith("connect:"):
            pid = event.item.id[len("connect:"):]
        if pid is None:
            return
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
