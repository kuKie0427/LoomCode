"""Chat log widget for the loop TUI.

Displays the conversation as a scrollable Markdown view.  All widget
methods are called from ``on_X`` handlers on the main event loop, so
async operations are safe to schedule via ``asyncio.ensure_future``.
"""

import asyncio
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from loop.tui.widgets import ToolCallCard


class ChatLog(VerticalScroll):
    """Scrollable Markdown chat display."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tool_cards: dict[str, ToolCallCard] = {}

    def compose(self) -> Any:
        yield Markdown(id="md")
        self._stream: Any = None

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
        card = ToolCallCard(name, inp, tool_id)
        self._tool_cards[tool_id] = card
        asyncio.ensure_future(self.mount(card))
        self.scroll_end()

    def complete_tool_card(self, tool_id: str, output: str, is_error: bool) -> None:
        card = self._tool_cards.get(tool_id)
        if card is not None:
            card.complete(output, is_error)
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
