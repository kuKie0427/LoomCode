"""TUI screen components for the loom coding agent."""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PermissionScreen(ModalScreen[str]):
    """Modal asking user to allow/deny a tool call.

    Returns: "allow" | "allow_always" | "deny" via dismiss().
    """

    BINDINGS = [
        ("escape", "deny", "Deny"),
        ("a", "allow_once", "Allow once"),
        ("A", "allow_always", "Allow always"),
        ("d", "deny", "Deny"),
    ]

    DEFAULT_CSS = """
    PermissionScreen {
        align: center middle;
    }
    #perm-dialog {
        width: 70%;
        height: auto;
        border: thick $error;
        padding: 1;
    }
    #perm-buttons {
        height: 3;
        align-horizontal: center;
    }
    Button { margin: 0 1; }
    """

    def __init__(self, tool_name: str, args: dict, reason: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.args = args
        self.reason = reason

    def compose(self) -> ComposeResult:
        with Container(id="perm-dialog"):
            yield Static(f"⚠  [b]{self.reason}[/b]", id="perm-reason")
            yield Static(f"Tool: [$secondary]{self.tool_name}[/]")
            yield Static(f"Args: {json.dumps(self.args, indent=2)[:300]}")
            with Horizontal(id="perm-buttons"):
                yield Button("Allow once (a)", id="btn-allow", variant="success")
                yield Button("Allow always (A)", id="btn-allow-always")
                yield Button("Deny (d/Esc)", id="btn-deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-allow":
            self.dismiss("allow")
        elif event.button.id == "btn-allow-always":
            self.dismiss("allow_always")
        else:
            self.dismiss("deny")

    def action_allow_once(self) -> None:
        self.dismiss("allow")

    def action_allow_always(self) -> None:
        self.dismiss("allow_always")

    def action_deny(self) -> None:
        self.dismiss("deny")
