"""Model picker modal for the loom TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ModelPicker(ModalScreen[tuple[str, str]]):
    """Modal screen showing a list of providers and their models.
    
    Returns (provider_id, model_id) via dismiss() on Enter.
    ESC dismisses (returns None) — user cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
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
    """

    def __init__(self, recent: list | None = None) -> None:
        super().__init__()
        self._recent = recent or []

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
                        items.append(
                            ListItem(
                                Label(f"  {ref}"),
                                id=f"recent:{ref.provider_id}/{ref.model_id}",
                            )
                        )
                items.append(
                    ListItem(Label("── All Providers ──", classes="section-header"))
                )
                from loom.agent.providers import PROVIDERS  # lazy import

                for pid in sorted(PROVIDERS):
                    try:
                        inst = PROVIDERS[pid](api_key="", base_url=None)
                        for model in inst.supported_models:
                            cw = inst.context_window(model)
                            items.append(
                                ListItem(
                                    Label(f"  {pid}/{model} ({cw:,} ctx)"),
                                    id=f"model:{pid}/{model}",
                                )
                            )
                    except Exception:
                        items.append(ListItem(Label(f"  {pid}/?")))
                yield ListView(*items)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        if event.item.id.startswith("model:"):
            model_str = event.item.id[len("model:"):]
            pid, _, mid = model_str.partition("/")
            self.dismiss((pid, mid))
        elif event.item.id.startswith("recent:"):
            model_str = event.item.id[len("recent:"):]
            pid, _, mid = model_str.partition("/")
            self.dismiss((pid, mid))

    def action_cancel(self) -> None:
        self.dismiss(None)
