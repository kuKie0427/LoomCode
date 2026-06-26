"""Session picker modal for the loom TUI.

Lists all saved sessions from the SessionStore, allowing the user to:
  - Enter: switch to the selected session
  - d:     delete the selected session (with confirmation)
  - n:     start a new session
  - Esc:   cancel (return to current session)

Layout mirrors the ModelPicker pattern for visual consistency.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from loom.agent.session_store import SessionMeta, SessionStore


class SessionPicker(ModalScreen[str | None]):
    """Modal screen showing saved sessions.

    Returns the selected session_id via dismiss() on Enter.
    Returns None on Esc (cancel) or when starting a new session
    (the caller handles /new separately).
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("d", "delete_session", "Delete"),
        ("n", "new_session", "New"),
    ]

    DEFAULT_CSS = """
    SessionPicker {
        align: center middle;
    }
    #session-picker-dialog {
        width: 70%;
        height: 70%;
        border: solid $accent;
        padding: 1;
        background: $surface 90%;
    }
    #session-picker-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    #session-picker-list {
        height: 1fr;
    }
    ListItem {
        padding: 0 1;
    }
    .session-entry {
        padding: 0 0 0 2;
    }
    .session-empty {
        padding: 1 0;
        color: $text-muted;
        text-style: italic;
    }
    .session-header {
        padding: 1 0 0 0;
        color: $accent;
        text-style: bold;
    }
    #session-footer {
        padding: 1 1 0 1;
        text-style: dim;
    }
    """

    def __init__(self, current_session_id: str | None = None) -> None:
        super().__init__()
        self._current_session_id = current_session_id
        self._sessions: list[SessionMeta] = []
        # Map item index → session_id
        self._index_to_id: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        with Container(id="session-picker-dialog"):
            yield Static("Session History", id="session-picker-title")
            yield ListView(id="session-picker-list")
            yield Static(
                "[Enter] switch  [d] delete  [n] new  [Esc] cancel",
                id="session-footer",
            )

    async def on_mount(self) -> None:
        """Load sessions and populate the list."""
        try:
            list_view = self.query_one("#session-picker-list", ListView)
        except Exception:
            return
        try:
            self._load_sessions()
            self._populate_list(list_view)
            if list_view.children:
                list_view.index = 0
            list_view.focus()
        except Exception:
            pass

    def _load_sessions(self) -> None:
        """Load sessions from the SessionStore."""
        from loom.agent.loop import WORKDIR

        store = SessionStore(WORKDIR)
        self._sessions = store.list_sessions()

    def _populate_list(self, list_view: ListView) -> None:
        """Populate the ListView with session entries."""
        self._index_to_id.clear()
        # Clear existing items
        list_view.clear()

        if not self._sessions:
            item = ListItem(
                Label("No saved sessions yet. Press [n] to start a new one."),
                classes="session-empty",
                disabled=True,
            )
            list_view.append(item)
            return

        for i, meta in enumerate(self._sessions):
            self._index_to_id[i] = meta.session_id
            # Format: "name (msg_count msgs, updated_at)"
            # Show a marker for the current session.
            marker = " ●" if meta.session_id == self._current_session_id else ""
            label_text = (
                f"  {meta.name}{marker}\n"
                f"    {meta.message_count} messages · "
                f"{meta.tool_call_count} tool calls · "
                f"{meta.updated_at[:16]}"
            )
            item = ListItem(
                Label(label_text),
                classes="session-entry",
            )
            list_view.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Switch to the selected session."""
        idx = list_view_index(event.item, event.list_view)
        if idx is None:
            return
        session_id = self._index_to_id.get(idx)
        if session_id is None:
            return
        self.dismiss(session_id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_new_session(self) -> None:
        # Dismiss with a sentinel "new" value — the caller handles it.
        self.dismiss("__new__")

    def action_delete_session(self) -> None:
        """Delete the currently selected session."""
        list_view = self.query_one("#session-picker-list", ListView)
        if list_view.index is None:
            return
        idx = list_view.index
        session_id = self._index_to_id.get(idx)
        if session_id is None:
            return
        # Don't allow deleting the current session.
        if session_id == self._current_session_id:
            try:
                self.query_one("#session-footer", Static).update(
                    "Cannot delete the current session."
                )
            except Exception:
                pass
            return
        from loom.agent.loop import WORKDIR

        store = SessionStore(WORKDIR)
        store.delete_session(session_id)
        # Reload and repopulate.
        self._load_sessions()
        self._populate_list(list_view)
        if list_view.children:
            list_view.index = 0


def list_view_index(item: ListItem, list_view: ListView) -> int | None:
    """Get the numeric index of an item in a ListView."""
    for i, child in enumerate(list_view.children):
        if child is item:
            return i
    return None
