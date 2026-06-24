"""Full-screen centered welcome page for the loom TUI.

Displayed as a ``ModalScreen`` on startup.  Dismissed on Enter or when
the user clicks outside the input zone (which transfers focus to the
normal Composer below).  The submitted text is dispatched as the first
``Composer.Submitted`` event.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from loom.tui.completer import filter_commands
from loom.tui.slash_commands import SlashCommand


class WelcomeModal(ModalScreen[str]):
    """Full-screen centered welcome page.

    Shows brand identity + a centered input + command completion popup.
    Dismisses with the user's first prompt on Enter, or with an
    empty string on ESC.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    WelcomeModal {
        align: center middle;
        background: $background;
    }
    #welcome-container {
        width: 60%;
        max-width: 80;
        height: auto;
        align: center middle;
    }
    #welcome-brand {
        height: 3;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #welcome-tagline {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    #welcome-input {
        margin: 2 0 0 0;
        border: solid $accent;
        background: $surface;
        color: $text;
    }
    #welcome-input:focus {
        border: solid $accent;
    }
    #welcome-completions {
        height: auto;
        max-height: 8;
        margin: 0 0 0 0;
        display: none;
    }
    #welcome-completions.visible {
        display: block;
    }
    #welcome-status {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        text-style: dim;
        margin: 1 0 0 0;
    }
    #welcome-hints {
        height: 1;
        content-align: center middle;
        color: $text;
        text-style: dim;
        margin: 2 0 0 0;
    }
    """

    def __init__(self, model: str = "?") -> None:
        super().__init__()
        self._model = model
        self._completion_matches: list[SlashCommand] = []
        self._completion_selected: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-container"):
            yield Static("◆ loom", id="welcome-brand")
            yield Static("weaving intent into action", id="welcome-tagline")
            yield Input(
                placeholder="Type a prompt, / for commands",
                id="welcome-input",
            )
            yield Static("", id="welcome-completions")
            yield Static(
                f"[$text-faint]{self._model}[/]",
                id="welcome-status",
            )
            yield Static(
                "/help  /model <name>  /connect  /clear  /resume",
                id="welcome-hints",
            )

    def on_mount(self) -> None:
        self.query_one("#welcome-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        if text.startswith("/") and " " not in text:
            self._show_completions(text)
        else:
            self._hide_completions()

    def _show_completions(self, text: str) -> None:
        query = text.lstrip("/")
        self._completion_matches = filter_commands(query)
        self._completion_selected = 0
        widget = self.query_one("#welcome-completions", Static)
        if not self._completion_matches:
            widget.update("")
            widget.remove_class("visible")
            return
        lines: list[str] = []
        for i, cmd in enumerate(self._completion_matches):
            prefix = "▸" if i == self._completion_selected else " "
            alias_str = f" [dim]({', '.join(cmd.aliases)})[/dim]" if cmd.aliases else ""
            lines.append(f"{prefix} /{cmd.name}{alias_str} — [dim]{cmd.description}[/dim]")
        widget.update("\n".join(lines))
        widget.add_class("visible")

    def _hide_completions(self) -> None:
        self._completion_matches = []
        self._completion_selected = 0
        widget = self.query_one("#welcome-completions", Static)
        widget.update("")
        widget.remove_class("visible")

    def _apply_completion(self) -> None:
        if not self._completion_matches:
            return
        idx = min(self._completion_selected, len(self._completion_matches) - 1)
        cmd = self._completion_matches[idx]
        inp = self.query_one("#welcome-input", Input)
        inp.value = f"/{cmd.name} "
        inp.cursor_position = len(inp.value)
        self._hide_completions()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_key(self, event) -> None:
        if not self._completion_matches:
            return
        if event.key == "tab":
            event.stop()
            self._apply_completion()
        elif event.key == "up":
            event.stop()
            self._completion_selected = max(0, self._completion_selected - 1)
            self._refresh_completion_highlight()
        elif event.key == "down":
            event.stop()
            self._completion_selected = min(
                len(self._completion_matches) - 1, self._completion_selected + 1
            )
            self._refresh_completion_highlight()
        elif event.key == "escape":
            event.stop()
            self._hide_completions()

    def _refresh_completion_highlight(self) -> None:
        if not self._completion_matches:
            return
        widget = self.query_one("#welcome-completions", Static)
        lines: list[str] = []
        for i, cmd in enumerate(self._completion_matches):
            prefix = "▸" if i == self._completion_selected else " "
            alias_str = f" [dim]({', '.join(cmd.aliases)})[/dim]" if cmd.aliases else ""
            lines.append(f"{prefix} /{cmd.name}{alias_str} — [dim]{cmd.description}[/dim]")
        widget.update("\n".join(lines))

    def action_cancel(self) -> None:
        if self._completion_matches:
            self._hide_completions()
        else:
            self.dismiss("")
