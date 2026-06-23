"""Ctrl+P command palette modal for the loom TUI.

Filters and selects from :class:`SlashCommand` entries via a
keyboard-navigable list.  Used by AgentTUIApp as a Ctrl+P overlay.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

from loom.tui.completer import filter_commands
from loom.tui.slash_commands import SlashCommand, all_commands


class CommandPaletteModal(ModalScreen[SlashCommand | None]):
    """Modal screen showing a Ctrl+P command palette.

    Provides a filter ``Input`` at the top and a ``ListView`` of
    matching ``SlashCommand`` entries.  Keyboard navigation:
    ``Up`` / ``Down`` to move the highlight, ``Enter`` to select,
    ``Escape`` to cancel.

    Returns the selected ``SlashCommand`` via ``dismiss()`` on
    ``Enter`` or double-click; ``dismiss(None)`` on ``Escape``.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("up", "cursor_up", "Move up"),
        ("down", "cursor_down", "Move down"),
        ("enter", "select", "Select"),
    ]

    DEFAULT_CSS = """
    CommandPaletteModal {
        align: center middle;
    }
    #command-palette-dialog {
        width: 60;
        max-width: 80%;
        max-height: 20;
        border: solid $accent;
        padding: 1;
        background: $panel;
    }
    #command-palette-input {
        margin: 0 0 1 0;
    }
    ListView {
        height: 1fr;
    }
    ListItem {
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._all_commands = all_commands()
        self._current_matches: list[SlashCommand] = []

    def compose(self) -> ComposeResult:
        with Container(id="command-palette-dialog"):
            yield Input(id="command-palette-input", placeholder="Search commands\u2026")
            yield ListView(id="command-palette-list")

    async def on_mount(self) -> None:
        self.query_one("#command-palette-input", Input).focus()
        await self._populate("")

    async def _populate(self, query: str) -> None:
        matches = filter_commands(query, limit=20)
        self._current_matches = matches
        list_view = self.query_one("#command-palette-list", ListView)
        await list_view.clear()
        for cmd in matches:
            await list_view.append(
                ListItem(
                    Static(
                        f"[bold]/{cmd.name}[/bold]  [dim]{cmd.description}[/dim]"
                    ),
                )
            )
        list_view.index = 0

    async def on_input_changed(self, event: Input.Changed) -> None:
        await self._populate(event.value)

    def action_select(self) -> None:
        """Dismiss with the currently highlighted command, or None."""
        list_view = self.query_one("#command-palette-list", ListView)
        index = list_view.index
        if index is not None and 0 <= index < len(self._current_matches):
            self.dismiss(self._current_matches[index])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        """Dismiss with None (user cancelled)."""
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        """Move the ListView cursor up one row."""
        lv = self.query_one("#command-palette-list", ListView)
        if lv.index is not None and lv.index > 0:
            lv.index = lv.index - 1

    def action_cursor_down(self) -> None:
        """Move the ListView cursor down one row."""
        lv = self.query_one("#command-palette-list", ListView)
        if lv.index is None:
            lv.index = 0
        elif lv.index < len(self._current_matches) - 1:
            lv.index = lv.index + 1

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle mouse double-click or Enter when ListView is focused."""
        if event.index is not None and 0 <= event.index < len(self._current_matches):
            self.dismiss(self._current_matches[event.index])
