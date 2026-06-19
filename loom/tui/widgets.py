from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual.widgets import Static

_DEFAULT_CSS = """\
ToolCallCard {
    border: solid $warning;
    height: auto;
    margin: 0 0 1 0;
}

ToolCallCard.tool-running {
    border: solid yellow;
}

ToolCallCard.tool-completed {
    border: solid green;
}

ToolCallCard.tool-error {
    border: solid red;
}
"""


class ToolCallCard(Static):
    DEFAULT_CSS = _DEFAULT_CSS

    def __init__(self, tool_name: str, args: dict[str, Any], tool_id: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.args = args
        self.tool_id = tool_id
        self.state = "running"
        self.add_class("tool-running")

    def render(self) -> Text:
        icon = self._state_icon()
        header = f"{icon} {self.tool_name} ({self.tool_id[:8]})"
        args_preview = json.dumps(self.args, default=str)[:120]
        body = f"  args: {args_preview}"
        return Text(f"{header}\n{body}")

    def complete(self, output: str, is_error: bool) -> None:
        self.state = "error" if is_error else "completed"
        self.remove_class("tool-running")
        if is_error:
            self.add_class("tool-error")
        else:
            self.add_class("tool-completed")
        self.refresh()

    def _state_icon(self) -> str:
        if self.state == "running":
            return "\u23f3"
        if self.state == "error":
            return "\u2717"
        return "\u2713"
