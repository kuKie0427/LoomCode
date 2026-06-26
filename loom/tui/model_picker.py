"""Model picker modal for the loom TUI.

Layout (matching opencode's dialog-model design):

  ┌─ Select a model ─────────────────────┐
  │  🔍 Filter models…                    │
  │                                       │
  │  ── Recent ──                         │
  │    anthropic/claude-sonnet-4-5        │
  │                                       │
  │  ● Provider Name  (✓ connected)       │  ← section header
  │    model-name                         │  ← model entry
  │    model-name                         │
  │  ● Another Provider                   │  ← section header
  │    model-name                         │
  │                                       │
  │  [c] Connect a new provider           │
  └───────────────────────────────────────┘
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

from loom.agent.credential import credentials
from loom.agent.model_state import ModelRef


class ModelPicker(ModalScreen[tuple[str, str]]):
    """Modal screen showing providers grouped by section with models underneath.

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
    #model-picker-input {
        margin: 0 0 1 0;
    }
    ListView {
        height: 1fr;
    }
    ListItem {
        padding: 0 1;
    }
    .section-header {
        padding: 1 0 0 0;
        color: $accent;
    }
    .section-header Label {
        text-style: bold underline;
    }
    .section-header.connected {
        color: $text;
    }
    .model-entry {
        padding: 0 0 0 2;
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
        # (label_text, item_id_or_None, classes_str) tuples — built once,
        # used to create fresh ListItems on each filter.
        self._rows: list[tuple[str, str | None, str]] = []

    def compose(self) -> ComposeResult:
        with Container(id="model-picker-dialog"):
            yield Static("Select a model", id="model-picker-title")
            yield Input(id="model-picker-input", placeholder="Filter models\u2026")
            yield ListView(id="model-picker-list")

    async def on_mount(self) -> None:
        """Focus the model list by default. Typing switches to the filter."""
        try:
            list_view = self.query_one("#model-picker-list", ListView)
        except Exception:
            # Defensive: if compose hasn't completed or the widget is missing,
            # defer to call_later so on_mount doesn't crash the app.
            self.call_later(self._deferred_mount)
            return
        await self._finish_mount(list_view)

    async def _deferred_mount(self) -> None:
        """Fallback mount logic called via call_later if query_one failed."""
        try:
            list_view = self.query_one("#model-picker-list", ListView)
        except Exception:
            return  # give up silently — the dialog is still usable
        await self._finish_mount(list_view)

    async def _finish_mount(self, list_view: ListView) -> None:
        """Build rows, populate the list, and focus it."""
        try:
            self._build_rows()
            await self._refresh_list("")
        except Exception:
            pass  # _build_rows / _refresh_list failures shouldn't crash the app
        # Start on the first non-header item (headers are disabled).
        try:
            first_enabled = next(
                (i for i, c in enumerate(list_view.children) if not c.disabled),
                0,
            )
            list_view.index = first_enabled
            list_view.focus()
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        """Printable keys auto-redirect to the filter Input.

        Initial focus is on the model list (for ↑↓ navigation).  When the
        user starts typing, this handler redirects the first printable key
        to the ``Input`` widget, which then stays focused for subsequent
        characters.
        """
        input_w = self.query_one("#model-picker-input", Input)
        if event.is_printable and not input_w.has_focus:
            event.stop()
            input_w.focus()
            if event.character is not None:
                input_w.value = event.character

    async def on_input_changed(self, event: Input.Changed) -> None:
        await self._refresh_list(event.value)

    def _build_rows(self) -> None:
        from loom.agent.providers import PROVIDERS  # lazy import

        self._rows = []
        seq = 0

        # Recent section (top)
        if self._recent:
            self._rows.append(("── Recent ──", None, "section-header"))
            for ref in self._recent:
                safe_id = f"r{seq}"
                seq += 1
                self._model_items[safe_id] = (ref.provider_id, ref.model_id)
                display = f"{ref.provider_id}/{ref.model_id}"
                self._rows.append((f"  {display}", safe_id, "model-entry"))

        # Provider sections — only show providers with credentials connected.
        # Use credentials.all() (single disk read) instead of calling
        # credentials.get() for each of the 120+ registered providers.
        connected = set(credentials.all().keys())
        for pid in sorted(PROVIDERS):
            if pid not in connected:
                continue
            try:
                inst = PROVIDERS[pid](api_key="", base_url=None)
                display_name = inst.display_name or pid

                # Get models from models.dev catalog if available,
                # falling back to the provider's hardcoded list.
                from loom.agent.models_dev import list_models_sorted

                dev_models = list_models_sorted(pid)
                if dev_models is not None:
                    model_ids_with_names = dev_models
                else:
                    model_ids_with_names = [(m, m) for m in inst.supported_models]

                if not model_ids_with_names:
                    continue

                # Provider section header
                cls = "section-header connected"
                self._rows.append((f"● {display_name}", None, cls))

                # Models under this provider (sorted by family → release_date)
                for model, display_name in model_ids_with_names:
                    safe_id = f"m{seq}"
                    seq += 1
                    self._model_items[safe_id] = (pid, model)
                    self._rows.append((f"  {display_name}", safe_id, "model-entry"))
            except Exception:
                self._rows.append((f"  {pid}/?", None, ""))

    async def _refresh_list(self, query: str) -> None:
        """Rebuild the ListView with only items matching the query."""
        list_view = self.query_one("#model-picker-list", ListView)
        await list_view.clear()

        query_lower = query.lower()
        items: list[ListItem] = []
        pending_header: tuple[str, str | None, str] | None = None

        for label_text, item_id, classes in self._rows:
            is_header = item_id is None  # section headers have no id

            if is_header:
                # Save header; show it only if any child matches below
                pending_header = (label_text, item_id, classes)
                continue

            if query_lower and query_lower not in label_text.lower():
                continue

            # First matching child → emit the pending header
            if pending_header is not None:
                ht, hi, hc = pending_header
                items.append(self._make_item(ht, hi, hc))
                pending_header = None

            items.append(self._make_item(label_text, item_id, classes))

        for item in items:
            await list_view.append(item)

        if list_view.children:
            list_view.index = 0

    def _make_item(self, text: str, item_id: str | None, classes: str) -> ListItem:
        # Section headers (id=None) should not be focusable via arrow keys.
        item = ListItem(id=item_id, classes=classes, disabled=item_id is None)
        item.compose_add_child(Label(text))
        return item

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        pid_mid = self._model_items.get(event.item.id)
        if pid_mid is None:
            return  # section header — not selectable

        pid, mid = pid_mid

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
