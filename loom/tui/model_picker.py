"""Model picker modal for the loom TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from loom.agent.credential import credentials
from loom.agent.model_state import ModelRef


class ModelPicker(ModalScreen[tuple[str, str]]):
    """Modal screen showing a list of providers and their models.

    Returns (provider_id, model_id) via dismiss() on Enter.
    ESC dismisses (returns None) — user cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("c", "connect_provider", "Connect a new provider"),
    ]

    DEFAULT_CSS = """
    ModelPicker {
        align: center middle;
    }
    #model-picker-dialog {
        width: 70%;
        height: 70%;
        border: solid $accent;
        padding: 1;
        background: $surface 90%;
    }
    #model-picker-title {
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
    #connect-footer {
        padding: 1 1 0 1;
        text-style: dim;
    }
    """

    def __init__(self, recent: list[ModelRef] | None = None) -> None:
        super().__init__()
        self._recent = recent or []
        # Maps Textual-safe widget IDs to (provider_id, model_id) tuples.
        self._model_items: dict[str, tuple[str, str]] = {}

    def compose(self) -> ComposeResult:
        with Container(id="model-picker-dialog"):
            yield Static("Select a model", id="model-picker-title")
            with Vertical():
                items: list[ListItem] = []
                if self._recent:
                    items.append(
                        ListItem(Label("── Recent ──", classes="section-header"))
                    )
                    for ref in self._recent:
                        safe_id = f"recent-{len(items)}"
                        self._model_items[safe_id] = (ref.provider_id, ref.model_id)
                        items.append(
                            ListItem(
                                Label(f"  {ref}"),
                                id=safe_id,
                            )
                        )
                items.append(
                    ListItem(Label("── All Providers ──", classes="section-header"))
                )
                from loom.agent.providers import PROVIDERS  # lazy import

                for pid in sorted(PROVIDERS):
                    try:
                        inst = PROVIDERS[pid](api_key="", base_url=None)
                        has_creds = credentials.get(pid) is not None
                        for model in inst.supported_models:
                            cw = inst.context_window(model)
                            status_icon = " [$accent]✓[/]" if has_creds else ""
                            safe_id = f"model-{len(items)}"
                            self._model_items[safe_id] = (pid, model)
                            items.append(
                                ListItem(
                                    Label(
                                        f"  {pid}/{model} ({cw:,} ctx){status_icon}"
                                    ),
                                    id=safe_id,
                                )
                            )
                    except Exception:
                        items.append(ListItem(Label(f"  {pid}/?")))
                yield ListView(*items)
                yield Static("[dim]Press [b]c[/b] to connect a new provider[/]", id="connect-footer")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        # Look up model info via _model_items dict (set by compose).
        pid_mid = self._model_items.get(event.item.id)
        if pid_mid is not None:
            pid, mid = pid_mid
        elif event.item.id.startswith("model:"):
            model_str = event.item.id[len("model:"):]
            pid, _, mid = model_str.partition("/")
        elif event.item.id.startswith("recent:"):
            model_str = event.item.id[len("recent:"):]
            pid, _, mid = model_str.partition("/")
        else:
            return

        from loom.agent.credential import credentials  # lazy import

        if credentials.get(pid) is None:
            from loom.tui.auth_input import AuthInputModal  # lazy import

            self.app.push_screen(
                AuthInputModal(pid),
                lambda result: self._on_login_then_switch(pid, mid, result),
            )
            return

        self.dismiss((pid, mid))

    def _on_login_then_switch(
        self, provider_id: str, model_id: str, login_result: str | None
    ) -> None:
        if login_result is None:
            return
        self.dismiss((provider_id, model_id))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_connect_provider(self) -> None:
        from loom.tui.connect_provider import ConnectProviderModal

        self.app.push_screen(ConnectProviderModal(), self._on_connect_from_picker)

    def _on_connect_from_picker(
        self, result: tuple[str, str | None] | None
    ) -> None:
        if result is None:
            return
        provider_id, model_id_info = result
        if model_id_info is None:
            from loom.tui.auth_input import AuthInputModal

            self.app.push_screen(
                AuthInputModal(provider_id), self._on_auth_from_picker
            )
        elif model_id_info == "":
            self.dismiss((provider_id, ""))

    def _on_auth_from_picker(self, result: str | None) -> None:
        if result is None:
            return
        provider_id = result
        self.dismiss((provider_id, ""))
