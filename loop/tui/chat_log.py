"""Chat log widget for the loop TUI.

Displays the conversation as a scrollable Markdown view.  All widget
methods are called from ``on_X`` handlers on the main event loop, so
async operations are safe to schedule via ``asyncio.ensure_future``.
"""

import asyncio
import json
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Markdown


class ChatLog(VerticalScroll):
    """Scrollable Markdown chat display."""

    def compose(self) -> Any:
        yield Markdown(id="md")
        self._stream: Any = None
        self._tool_cards: dict[str, str] = {}

    async def append_user_message(self, text: str) -> None:
        md = self.query_one("#md", Markdown)
        await md.append(f"## You\n\n{text}\n\n")
        self.scroll_end()

    def show_thinking_spinner(self) -> None:
        md = self.query_one("#md", Markdown)
        if self._stream is None:
            self._stream = Markdown.get_stream(md)
        asyncio.ensure_future(md.append("### Assistant\n\n"))

    def append_streaming_text(self, text: str) -> None:
        md = self.query_one("#md", Markdown)
        asyncio.ensure_future(md.append(text))
        self.scroll_end()

    def add_tool_card(self, name: str, inp: dict, tool_id: str) -> None:
        md = self.query_one("#md", Markdown)
        self._tool_cards[tool_id] = f"▶ {name}"
        snippet = json.dumps(inp)[:100]
        asyncio.ensure_future(md.append(f"▶ **{name}** `{snippet}`\n"))
        self.scroll_end()

    def complete_tool_card(self, tool_id: str, output: str, is_error: bool) -> None:
        md = self.query_one("#md", Markdown)
        marker = "✗" if is_error else "✓"
        preview = output[:200].replace("\n", "↩ ")
        asyncio.ensure_future(md.append(f"  {marker} {preview}\n"))
        self.scroll_end()

    def append_system_note(self, text: str) -> None:
        md = self.query_one("#md", Markdown)
        asyncio.ensure_future(md.append(f"\n*{text}*\n\n"))
        self.scroll_end()

    async def clear_content(self) -> None:
        md = self.query_one("#md", Markdown)
        md.update("")
        self._tool_cards.clear()
        self._stream = None
