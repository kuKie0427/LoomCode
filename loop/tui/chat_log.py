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

    async def append_user_message(self, text: str) -> None:
        md = self.query_one("#md", Markdown)
        await md.append(f"## You\n\n{text}\n\n")
        self.scroll_end()

    def show_thinking_spinner(self) -> None:
        md = self.query_one("#md", Markdown)
        if self._stream is None:
            self._stream = Markdown.get_stream(md)
        asyncio.create_task(self._stream.write("### Assistant\n\n"))

    def append_streaming_text(self, text: str) -> None:
        asyncio.create_task(self._stream.write(text))
        self.scroll_end()

    def add_tool_call_inline(self, name: str, inp: dict, tool_id: str) -> None:
        args_str = json.dumps(inp, ensure_ascii=False, indent=2) if inp else ""
        if args_str:
            block = f"\n\n```\n[tool] {name}\n{args_str}\n```\n"
        else:
            block = f"\n\n```\n[tool] {name}\n```\n"
        asyncio.create_task(self._stream.write(block))
        self.scroll_end()

    def complete_tool_call_inline(self, tool_id: str, output: str, is_error: bool) -> None:
        tag = "[result:error]" if is_error else "[result]"
        block = f"\n```\n{tag}\n{output}\n```\n"
        asyncio.create_task(self._stream.write(block))
        self.scroll_end()

    def append_system_note(self, text: str) -> None:
        md = self.query_one("#md", Markdown)
        asyncio.ensure_future(md.append(f"\n*{text}*\n\n"))
        self.scroll_end()

    async def clear_content(self) -> None:
        md = self.query_one("#md", Markdown)
        md.update("")
        self._stream = None
