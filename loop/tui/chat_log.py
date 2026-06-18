"""Chat log widget for the loop TUI.

Displays the conversation as a scrollable Markdown view.  All widget
methods are called from ``on_X`` handlers on the main event loop, so
async operations are safe to schedule via ``asyncio.ensure_future``.
"""

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

MAX_TOOL_OUTPUT_LINES = 30
_HEAD_LINES = 15
_TAIL_LINES = 15

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_SLOW_THRESHOLD_SECONDS = 10.0
_TICK_INTERVAL_SECONDS = 0.05
_SLOW_TICKS_PER_FRAME = 4


def _is_structured_line(line: str) -> bool:
    """Check if a line starts a Markdown structure that needs newlines preserved."""
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped.startswith(("|", "#", ">", "```", "- ", "* ", "+ ")):
        return True
    if len(stripped) >= 3 and stripped[0].isdigit():
        dot_idx = stripped.find(". ")
        if dot_idx >= 1 and stripped[:dot_idx].isdigit():
            return True
    return False


def _normalize_for_stream(text: str) -> str:
    """Normalize text for Markdown streaming.

    Single newlines within plain paragraphs become spaces (Markdown
    treats them as line breaks otherwise).  Double newlines are
    preserved as paragraph breaks.  Structured content (tables, lists,
    headers, code blocks, blockquotes) keeps its newlines so the
    Markdown parser can detect the structure.
    """
    if "\n" not in text:
        return text
    if "\n\n" in text:
        parts = text.split("\n\n")
        return "\n\n".join(_normalize_for_stream(p) for p in parts)
    lines = text.split("\n")
    # If this paragraph contains any structured line, preserve as-is
    if any(_is_structured_line(ln) for ln in lines):
        return text
    # Plain paragraph: single newlines → spaces
    return " ".join(ln for ln in lines if ln)


def _truncate(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= MAX_TOOL_OUTPUT_LINES:
        return text
    head = lines[:_HEAD_LINES]
    tail = lines[-_TAIL_LINES:]
    omitted = len(lines) - _HEAD_LINES - _TAIL_LINES
    return (
        "\n".join(head)
        + f"\n\n… ({omitted} more lines omitted) …\n\n"
        + "\n".join(tail)
    )


_MD_SYNTAX_RE = re.compile(r'[#*`|\[>\-_~]|\n\n|^\d+\. |\n\d+\. ')


def _has_markdown_syntax(text: str) -> bool:
    sample = text[:500] if len(text) > 500 else text
    return bool(_MD_SYNTAX_RE.search(sample))


class TurnLabel(Static):
    DEFAULT_CSS = """
    TurnLabel {
        height: 1;
        color: $accent;
        text-style: bold dim;
        padding: 0;
        margin: 1 0 0 0;
    }
    TurnLabel.role-user {
        color: $primary;
    }
    TurnLabel.role-assistant {
        color: $accent;
    }
    """


class UserMessage(Markdown):
    DEFAULT_CSS = """
    UserMessage {
        background: $surface;
        color: $text;
        padding: 1 2;
        margin: 0 0 1 0;
        border: none;
    }
    """


class AssistantMessage(Markdown):
    DEFAULT_CSS = """
    AssistantMessage {
        background: $background;
        color: $text;
        padding: 0 2;
        margin: 0 0 1 0;
        border: none;
    }
    """


class StreamingOverlay(Markdown):
    DEFAULT_CSS = """
    StreamingOverlay {
        background: $background;
        color: $text;
        padding: 0 2;
        margin: 0 0 1 0;
        border: none;
    }
    """

    def update_content(self, text: str) -> None:
        self.update(_normalize_for_stream(text))


class SystemNote(Static):
    DEFAULT_CSS = """
    SystemNote {
        height: auto;
        color: $text-muted;
        text-style: italic dim;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    """


class ThinkingDisplay(Markdown):
    DEFAULT_CSS = """
    ThinkingDisplay {
        background: $boost 30%;
        color: $text-muted;
        padding: 0 2;
        margin: 0 0 1 2;
        text-style: italic;
        border: none;
    }
    ThinkingDisplay.hidden {
        display: none;
    }
    """


class ThinkingMarker(Static):
    can_focus = True

    DEFAULT_CSS = """
    ThinkingMarker {
        width: auto;
        height: 1;
        background: transparent;
        color: $text-muted;
        text-style: dim;
        padding: 0 0 0 2;
        margin: 0 0 0 0;
    }
    ThinkingMarker:hover {
        color: $accent;
        text-style: bold;
    }
    ThinkingMarker.thinking-long {
        color: $warning;
    }
    """

    def __init__(self, on_toggle: Callable[["ThinkingDisplay"], None], **kwargs: Any) -> None:
        super().__init__("⠋", **kwargs)
        self._on_toggle = on_toggle
        self._complete = False
        self._start_time: float | None = None
        self._final_elapsed: str = ""
        self._display: ThinkingDisplay | None = None

    def start_timer(self) -> None:
        self._start_time = time.monotonic()

    def _elapsed_str(self) -> str:
        if self._start_time is None:
            return ""
        elapsed = time.monotonic() - self._start_time
        if elapsed < 60:
            return f"{int(elapsed)}s"
        if elapsed < 3600:
            return f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"
        return f"{int(elapsed // 3600)}h{int((elapsed % 3600) // 60):02d}m"

    def update_spinner(self, frame: str) -> None:
        if not self._complete:
            if self._start_time is not None:
                elapsed = time.monotonic() - self._start_time
                if elapsed >= _SLOW_THRESHOLD_SECONDS:
                    self.add_class("thinking-long")
            self.update(f"{frame} thinking · {self._elapsed_str()}")

    def on_click(self, event: Click) -> None:
        if self._display is not None:
            self._on_toggle(self._display)

    def on_press(self) -> None:
        if self._display is not None:
            self._on_toggle(self._display)

    def set_complete(self) -> None:
        if not self._complete:
            self._complete = True
            self._final_elapsed = self._elapsed_str()
            self.update(f"◦ thought · {self._final_elapsed}")


class ToolCallModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    ToolCallModal {
        align: center middle;
    }
    #tc-modal-container {
        width: 80%;
        height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    #tc-modal-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #tc-modal-body {
        height: 1fr;
        overflow-y: auto;
        border: solid $primary-darken-2;
        padding: 1;
    }
    #tc-modal-close {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, title: str, args_str: str, output_str: str, is_error: bool) -> None:
        super().__init__()
        self._title = title
        self._args_str = args_str
        self._output_str = output_str
        self._is_error = is_error

    def compose(self) -> ComposeResult:
        icon = "❌" if self._is_error else "📄"
        with Vertical(id="tc-modal-container"):
            yield Static(f"🔧 {self._title}", id="tc-modal-title")
            body_parts: list[str] = []
            if self._args_str:
                body_parts.append(f"**Args:**\n```json\n{self._args_str}\n```")
            body_parts.append(f"**{icon} Result:**\n```text\n{self._output_str}\n```")
            yield Markdown("\n\n".join(body_parts), id="tc-modal-body")
            yield Button("Close", id="tc-modal-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    async def action_dismiss(self, result: Any = None) -> None:
        self.dismiss(result)


class ToolCallMarker(Static):
    can_focus = True

    DEFAULT_CSS = """
    ToolCallMarker {
        width: auto;
        height: 1;
        background: transparent;
        color: $accent;
        padding: 0 0 0 2;
        margin: 0 0 0 0;
    }
    ToolCallMarker:hover {
        text-style: bold underline;
    }
    ToolCallMarker.tool-error {
        color: $error;
    }
    ToolCallMarker.tool-done {
        color: $success;
        text-style: dim;
    }
    """

    def __init__(self, tool_name: str, args_str: str, **kwargs: Any) -> None:
        super().__init__(f"⊙ {tool_name} · running", **kwargs)
        self._tool_name = tool_name
        self._args_str = args_str
        self._output_str = ""
        self._is_error = False
        self._complete = False

    def on_click(self, event: Click) -> None:
        self._open_modal()

    def on_press(self) -> None:
        self._open_modal()

    def _open_modal(self) -> None:
        self.app.push_screen(ToolCallModal(
            self._tool_name, self._args_str, self._output_str, self._is_error
        ))

    def set_complete(self, output: str, is_error: bool) -> None:
        if self._complete:
            return
        self._complete = True
        self._output_str = _truncate(output)
        self._is_error = is_error
        glyph = "⊗" if is_error else "⊙"
        status = "error" if is_error else "done"
        self.update(f"{glyph} {self._tool_name} · {status}")
        self.add_class("tool-error" if is_error else "tool-done")


class ChatLog(Vertical):
    _sticky: bool = True

    def watch_scroll_y(self, old_y: float, new_y: float) -> None:
        if new_y < old_y:
            self._sticky = False
        elif new_y > old_y and self.is_vertical_scroll_end:
            self._sticky = True

    def compose(self) -> Any:
        self._stream: Any = None
        self._current_body: Markdown | None = None
        self._current_overlay: StreamingOverlay | None = None
        self._thinking_widget: ThinkingMarker | None = None
        self._thinking_display: ThinkingDisplay | None = None
        self._thinking_stream: Any = None
        self._spinner_timer: Any = None
        self._spinner_idx: int = 0
        self._spinner_tick: int = 0
        self._thinking_reasoning: str = ""
        self._tool_markers: dict[str, ToolCallMarker] = {}
        self._assistant_label_mounted: bool = False
        self._stream_full_text: str = ""
        self._stream_flush_timer: Any = None
        self._STREAM_FLUSH_INTERVAL = 0.05
        return iter(())

    async def append_user_message(self, text: str) -> None:
        self._stream = None
        self._current_body = None
        self._assistant_label_mounted = False
        self._stream_full_text = ""
        self._sticky = True
        label = TurnLabel("▎ you", classes="role-user")
        body = UserMessage(text)
        await self.mount(label)
        await self.mount(body)
        self.scroll_end()

    def show_thinking_spinner(self) -> None:
        if self._thinking_display is not None:
            self._thinking_display.add_class("hidden")
        self._dismiss_thinking_widget()
        self._stream = None
        self._current_body = None
        if not self._assistant_label_mounted:
            asyncio.create_task(self._mount_async(TurnLabel("▎ assistant", classes="role-assistant")))
            self._assistant_label_mounted = True
        self._thinking_reasoning = ""
        self._thinking_display = None
        self._thinking_stream = None
        self._mount_thinking_widget()
        self._start_new_body()

    def _start_new_body(self) -> None:
        if self._current_body is not None:
            return
        body_md = AssistantMessage()
        self._current_body = body_md
        asyncio.create_task(self._mount_and_open_stream(body_md))

    def _start_new_overlay(self) -> None:
        overlay = StreamingOverlay()
        self._current_overlay = overlay
        asyncio.create_task(self._mount_async(overlay))

    async def _mount_and_open_stream(self, body_md: Markdown) -> None:
        await self.mount(body_md)
        if self._current_body is body_md:
            self._stream = Markdown.get_stream(body_md)

    def _mount_thinking_widget(self) -> None:
        if self._thinking_widget is not None:
            return
        widget = ThinkingMarker(
            on_toggle=self._toggle_thinking_display,
        )
        widget.start_timer()
        self._thinking_widget = widget
        self._spinner_idx = 0
        self._spinner_tick = 0
        self._spinner_timer = self.set_interval(
            _TICK_INTERVAL_SECONDS, self._tick_spinner, name="spinner"
        )
        asyncio.create_task(self._mount_async(widget))

    def _toggle_thinking_display(self, display: "ThinkingDisplay") -> None:
        if display is None:
            return
        display.toggle_class("hidden")

    async def _mount_async(self, widget: Any) -> None:
        await self.mount(widget)

    def _tick_spinner(self) -> None:
        if self._thinking_widget is None:
            return
        self._spinner_tick += 1
        is_slow = self._thinking_widget.has_class("thinking-long")
        ticks_per_frame = _SLOW_TICKS_PER_FRAME if is_slow else 1
        if self._spinner_tick % ticks_per_frame == 0:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self._thinking_widget.update_spinner(_SPINNER_FRAMES[self._spinner_idx])

    def _dismiss_thinking_widget(self) -> None:
        if self._thinking_widget is not None:
            if self._spinner_timer is not None:
                self._spinner_timer.stop()
                self._spinner_timer = None
            self._thinking_widget.set_complete()
            self._thinking_widget = None

    def append_thinking_text(self, text: str) -> None:
        self._thinking_reasoning += text
        if self._thinking_display is None:
            display = ThinkingDisplay()
            self._thinking_display = display
            if self._thinking_widget is not None:
                self._thinking_widget._display = display
            asyncio.create_task(self._mount_and_open_thinking_stream(display, text))
        elif self._thinking_stream is not None:
            asyncio.create_task(self._thinking_stream.write(_normalize_for_stream(text)))

    async def _mount_and_open_thinking_stream(self, display: ThinkingDisplay, initial_text: str) -> None:
        body = self._current_body
        if body is not None and body.parent is self:
            await self.mount(display, before=body)
        else:
            await self.mount(display)
        self._thinking_stream = Markdown.get_stream(display)
        if initial_text:
            await self._thinking_stream.write(_normalize_for_stream(initial_text))

    def append_streaming_text(self, text: str) -> None:
        self._dismiss_thinking_widget()
        if self._current_overlay is None:
            self._start_new_overlay()
        self._stream_full_text += text
        if self._stream_flush_timer is None:
            self._stream_flush_timer = self.set_interval(
                self._STREAM_FLUSH_INTERVAL, self._flush_stream_buffer
            )

    def _flush_stream_buffer(self) -> None:
        if self._stream_full_text and self._current_overlay is not None:
            self._current_overlay.update_content(self._stream_full_text)

    def _force_flush_stream_buffer(self) -> None:
        if self._stream_full_text and self._current_overlay is not None:
            self._current_overlay.update_content(self._stream_full_text)
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            self._stream_flush_timer = None

    def _finalize_streaming(self) -> None:
        if self._current_overlay is None:
            return
        final_text = self._stream_full_text
        overlay = self._current_overlay
        self._current_overlay = None
        self._stream_full_text = ""
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            self._stream_flush_timer = None
        final_message = AssistantMessage(_normalize_for_stream(final_text))
        asyncio.create_task(self._mount_final_message(overlay, final_message))

    async def _mount_final_message(
        self, overlay: StreamingOverlay, final: AssistantMessage
    ) -> None:
        await overlay.remove()
        await self.mount(final)
        self._current_body = final

    async def _write_stream(self, text: str) -> None:
        await self._stream.write(_normalize_for_stream(text))
        if self._sticky:
            self.scroll_end()

    async def _update_body(self, text: str) -> None:
        if self._current_body is not None:
            if not _has_markdown_syntax(text):
                await self._current_body.update(text)
            else:
                await self._current_body.update(_normalize_for_stream(text))
            if self._stream_flush_timer is not None and self._sticky:
                self.scroll_end()

    def add_tool_call_inline(self, name: str, inp: dict, tool_id: str) -> None:
        self._force_flush_stream_buffer()
        self._dismiss_thinking_widget()
        self._finalize_streaming()
        args_str = json.dumps(inp, ensure_ascii=False, indent=2) if inp else ""
        marker = ToolCallMarker(name, args_str)
        self._tool_markers[tool_id] = marker
        asyncio.create_task(self._mount_async(marker))
        self._stream = None

    def complete_tool_call_inline(self, tool_id: str, output: str, is_error: bool) -> None:
        marker = self._tool_markers.pop(tool_id, None)
        if marker is not None:
            marker.set_complete(output, is_error)
        self._thinking_display = None
        self._thinking_stream = None

    def append_system_note(self, text: str) -> None:
        self._force_flush_stream_buffer()
        self._stream = None
        self._current_body = None
        asyncio.create_task(self._mount_async(SystemNote(f"· {text}")))

    async def clear_content(self) -> None:
        self._force_flush_stream_buffer()
        self._dismiss_thinking_widget()
        for child in list(self.children):
            await child.remove()
        self._tool_markers.clear()
        self._stream = None
        self._current_body = None
        self._current_overlay = None
        self._thinking_display = None
        self._thinking_stream = None
        self._thinking_reasoning = ""
        self._assistant_label_mounted = False
