"""Chat log widget for the loom TUI.

Displays the conversation as a scrollable Markdown view.  All widget
methods are called from ``on_X`` handlers on the main event loop, so
async operations are safe to schedule via ``asyncio.ensure_future``.
"""

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
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


def _markdown_parser_factory():
    """Build a markdown-it parser that disables the linkify (URL auto-detect)
    rule.

    The default ``gfm-like`` preset enabled by ``textual.widgets.Markdown``
    turns on ``linkify-it``, which matches any ``domain.tld`` against the
    public-suffix list and turns it into a clickable link. File names with
    a TLD-shaped extension (e.g. ``conftest.py``, ``setup.sh``, ``README.md``,
    ``a.py``) get re-rendered as ``http://conftest.py`` and clicked in the
    user's terminal — they open the OS default browser pointed at a
    non-existent domain. We disable the linkify rule while keeping the rest
    of the gfm-like preset (tables, strikethrough, inline HTML).
    """
    from markdown_it import MarkdownIt

    parser = MarkdownIt("gfm-like")
    parser.options["linkify"] = False
    return parser


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


class TurnSeparator(Static):
    """Hairline divider mounted before each user turn.

    Visual: a single `─` line in $border, content-column padding (0 2) so
    it aligns with the labels below. Per §2 rule 5, this is decoration on
    the same outer column as TurnLabel, not a new indentation tier.
    """

    DEFAULT_CSS = """
    TurnSeparator {
        height: 1;
        color: $border;
        text-style: dim;
        padding: 0 2;
        margin: 1 0 0 0;
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

    def __init__(self, markdown: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("parser_factory", _markdown_parser_factory)
        super().__init__(markdown, **kwargs)


class AssistantMessage(Markdown):
    DEFAULT_CSS = """
    AssistantMessage {
        background: $background;
        color: $text;
        padding: 0 2;
        margin: 0 0 1 0;
        border-left: outer $accent-dim;
    }
    """

    def __init__(self, markdown: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("parser_factory", _markdown_parser_factory)
        super().__init__(markdown, **kwargs)


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

    def __init__(self, markdown: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("parser_factory", _markdown_parser_factory)
        super().__init__(markdown, **kwargs)

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


class WelcomeBanner(Static):
    """Static idle-state splash shown while the ChatLog is empty.

    Composition (revised 2026-06-21, #5): a 3D extruded stencil wordmark
    in opencode's reference style, with a three-tone gradient that
    produces the "3D block sitting on a surface" effect:

        $accent-light  ── row 0 (top face, the lit edge)
        $accent        ── rows 1–3 (body, the front face)
        $accent-dim    ── row 4 (bottom face, the shadow)

    Per the opencode reference image (the actual rendered logo, not
    the simplified `logo.ts` constants): 5-row stencil letters with
    a clear upper-face / body / lower-face color band. The 'o' has
    a stencil cutout (a 1-cell `▀` slot in row 2) that mimics the
    filled-square hole in opencode's 'o'. Letter widths: l=3, o=5,
    o=5, m=7 (with 1-col separators + 2-col leading padding = 25
    cols × 5 rows).

    Pure still image — no animation (§2 rule 2). Dismissed on first
    user message, re-shown after /clear.
    """

    DEFAULT_CSS = """
    WelcomeBanner {
        height: auto;
        width: 1fr;
        content-align: center middle;
        padding: 2 0 0 0;
        margin: 0 0 1 0;
    }
    """

    # 3D extruded "loom" wordmark, 25 cols × 5 rows. Row 2 has a
    # 1-cell `▀` stencil cutout inside each 'o' (opencode-style filled
    # hole). Row 4 is split so that `▄` chars use $accent-dim (shadow)
    # and `█` chars stay in $accent (body color, matching the side edges).
    _LOOM_WORDMARK_ROW0 = "  ▀▀▀ █▀▀▀█ █▀▀▀█ █▀▀█▀▀█"
    _LOOM_WORDMARK_ROW1 = "  ███ █   █ █   █ █  █  █"
    _LOOM_WORDMARK_ROW2 = "  ███ █ ▀ █ █ ▀ █ █  █  █"
    _LOOM_WORDMARK_ROW3 = "  ███ █   █ █   █ █  █  █"
    _LOOM_WORDMARK_BOTTOM = "  ▄▄▄ █▄▄▄█ █▄▄▄█ █▄▄█▄▄█"

    @staticmethod
    def _colorize_top(row: str) -> str:
        """Row 0 — top face. `▀` in $accent-light, `█` in $accent."""
        out: list[str] = []
        for ch in row:
            if ch == "▀":
                out.append("[$accent-light]▀[/]")
            elif ch == "█":
                out.append("[$accent]█[/]")
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _colorize_body(row: str) -> str:
        """Body rows 1–3 — `▀` (stencil cutout) in $accent-light, rest in $accent."""
        out: list[str] = []
        for ch in row:
            if ch == "▀":
                out.append("[$accent-light]▀[/]")
            elif ch == "█":
                out.append("[$accent]█[/]")
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _colorize_bottom(row: str) -> str:
        """Row 4 — bottom face. `▄` in $accent-dim, `█` in $accent."""
        out: list[str] = []
        for ch in row:
            if ch == "▄":
                out.append("[$accent-dim]▄[/]")
            elif ch == "█":
                out.append("[$accent]█[/]")
            else:
                out.append(ch)
        return "".join(out)

    def __init__(self, model: str = "loom", **kwargs: Any) -> None:
        body = (
            f"{self._colorize_top(self._LOOM_WORDMARK_ROW0)}\n"
            f"{self._colorize_body(self._LOOM_WORDMARK_ROW1)}\n"
            f"{self._colorize_body(self._LOOM_WORDMARK_ROW2)}\n"
            f"{self._colorize_body(self._LOOM_WORDMARK_ROW3)}\n"
            f"{self._colorize_bottom(self._LOOM_WORDMARK_BOTTOM)}\n\n"
            f"[$text-muted]weaving intent into action[/]\n\n"
            f"[$text-faint]/help · /model · /clear · /resume[/]"
        )
        super().__init__(body, **kwargs)


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
    """

    def __init__(self, markdown: str = "", **kwargs: Any) -> None:
        kwargs.setdefault("parser_factory", _markdown_parser_factory)
        super().__init__(markdown, **kwargs)
        self.display = False


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


class CollapsibleToolOutput(Vertical):
    DEFAULT_CSS = """
    CollapsibleToolOutput {
        max-height: 20;
        overflow-y: auto;
        background: $surface;
        padding: 1 2;
        margin: 0 0 1 2;
        border: none;
    }
    """

    def __init__(self, output: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._output = output
        self.display = False

    def compose(self) -> ComposeResult:
        yield Markdown(_truncate(self._output), parser_factory=_markdown_parser_factory)

    def toggle(self) -> None:
        self.display = not self.display

    def set_output(self, text: str) -> None:
        self._output = text
        try:
            md = self.query_one(Markdown)
            md.update(_truncate(text))
        except Exception:
            pass


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
            yield Markdown(
                "\n\n".join(body_parts),
                id="tc-modal-body",
                parser_factory=_markdown_parser_factory,
            )
            yield Button("Close", id="tc-modal-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    async def action_dismiss(self, result: Any = None) -> None:
        self.dismiss(result)


class SubagentMarker(Static):
    can_focus = True

    DEFAULT_CSS = """
    SubagentMarker {
        width: auto;
        height: 1;
        background: transparent;
        color: $accent;
        text-style: bold;
        padding: 0 0 0 2;
        margin: 0 0 0 0;
    }
    SubagentMarker:hover {
        text-style: bold underline;
    }
    SubagentMarker.marker-done {
        color: $success;
        text-style: dim;
    }
    SubagentMarker.marker-error {
        color: $error;
    }
    """

    def __init__(self, subagent_id: str, description: str, **kwargs: Any) -> None:
        super().__init__(f"◐ task: {description}", **kwargs)
        self._subagent_id = subagent_id
        self._description = description

    @property
    def description(self) -> str:
        """The task description shown in the marker text."""
        return self._description


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
        self._output_widget: CollapsibleToolOutput | None = None

    def on_click(self, event: Click) -> None:
        if event.chain == 2:
            self._open_modal()
        else:
            self._toggle_output()

    def on_press(self) -> None:
        self._toggle_output()

    def set_output_widget(self, widget: CollapsibleToolOutput) -> None:
        self._output_widget = widget

    def _toggle_output(self) -> None:
        if self._output_widget is not None:
            self._output_widget.toggle()

    def _open_modal(self) -> None:
        self.app.push_screen(ToolCallModal(
            self._tool_name, self._args_str, self._output_str, self._is_error
        ))

    def set_complete(self, output: str, is_error: bool) -> None:
        if self._complete:
            return
        self._complete = True
        self._output_str = output
        self._is_error = is_error
        glyph = "⊗" if is_error else "⊙"
        status = "error" if is_error else "done"
        self.update(f"{glyph} {self._tool_name} · {status}")
        self.add_class("tool-error" if is_error else "tool-done")


class ChatLog(VerticalScroll):
    _sticky: bool = True

    def watch_scroll_y(self, old_y: float, new_y: float) -> None:
        if new_y < old_y:
            self._sticky = False
        elif new_y > old_y and self.is_vertical_scroll_end:
            self._sticky = True

    def compose(self) -> Any:
        self._current_body: Markdown | None = None
        self._current_overlay: StreamingOverlay | None = None
        self._thinking_widget: ThinkingMarker | None = None
        self._thinking_display: ThinkingDisplay | None = None
        self._spinner_timer: Any = None
        self._spinner_idx: int = 0
        self._spinner_tick: int = 0
        self._thinking_reasoning: str = ""
        self._tool_markers: dict[str, ToolCallMarker] = {}
        self._tool_outputs: dict[str, CollapsibleToolOutput] = {}
        self._subagent_markers: dict[str, SubagentMarker] = {}
        self._last_todo_summary: str = ""
        self._assistant_label_mounted: bool = False
        self._stream_full_text: str = ""
        self._stream_flush_timer: Any = None
        self._STREAM_FLUSH_INTERVAL = 0.05
        self._welcome: WelcomeBanner | None = None
        return iter(())

    def mount_welcome(self) -> None:
        if self._welcome is not None or self.children:
            return
        banner = WelcomeBanner()
        self._welcome = banner
        asyncio.create_task(self._mount_async(banner))

    def _dismiss_welcome(self) -> None:
        if self._welcome is not None:
            asyncio.create_task(self._remove_widget(self._welcome))
            self._welcome = None

    async def _remove_widget(self, widget: Any) -> None:
        await widget.remove()

    async def append_user_message(self, text: str) -> None:
        self._dismiss_welcome()
        self._current_body = None
        self._assistant_label_mounted = False
        self._stream_full_text = ""
        self._tool_outputs.clear()
        # _subagent_markers and _last_todo_summary are intentionally NOT
        # reset here — they are timeline state that persists across user
        # turns (mirroring _tool_markers, which also persists). Use
        # clear_content() (the /clear slash command) to reset both.
        self._sticky = True
        await self.mount(TurnSeparator("─" * 60))
        label = TurnLabel("▎ you", classes="role-user")
        body = UserMessage(text)
        await self.mount(label)
        await self.mount(body)
        self.scroll_end()

    def show_thinking_spinner(self) -> None:
        if self._thinking_widget is not None and not self._thinking_widget._complete:
            return
        if self._thinking_display is not None:
            self._thinking_display.display = False
        self._dismiss_thinking_widget()
        self._current_body = None
        if not self._assistant_label_mounted:
            asyncio.create_task(self._mount_async(TurnLabel("▎ assistant", classes="role-assistant")))
            self._assistant_label_mounted = True
        self._thinking_reasoning = ""
        self._thinking_display = None
        self._mount_thinking_widget()

    def _start_new_overlay(self) -> None:
        overlay = StreamingOverlay()
        self._current_overlay = overlay
        asyncio.create_task(self._mount_async(overlay))

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
        display.display = not display.display

    async def _mount_async(self, widget: Any) -> None:
        await self.mount(widget)

    async def _remove_async(self, widget: SubagentMarker) -> None:
        await widget.remove()

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
            asyncio.create_task(self._mount_thinking_display(display))
        else:
            self._thinking_display.update(_normalize_for_stream(self._thinking_reasoning))

    async def _mount_thinking_display(self, display: ThinkingDisplay) -> None:
        body = self._current_body
        if body is not None and body.parent is self:
            await self.mount(display, before=body)
        else:
            await self.mount(display)
        display.update(_normalize_for_stream(self._thinking_reasoning))
        if self._sticky:
            self.scroll_end()

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
        if self._sticky:
            self.scroll_end()

    def _force_flush_stream_buffer(self) -> None:
        if self._stream_full_text and self._current_overlay is not None:
            self._current_overlay.update_content(self._stream_full_text)
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            self._stream_flush_timer = None
        if self._sticky:
            self.scroll_end()

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

    def add_tool_call_inline(self, name: str, inp: dict, tool_id: str) -> None:
        self._force_flush_stream_buffer()
        self._dismiss_thinking_widget()
        self._finalize_streaming()
        args_str = json.dumps(inp, ensure_ascii=False, indent=2) if inp else ""
        marker = ToolCallMarker(name, args_str)
        output = CollapsibleToolOutput("")
        marker.set_output_widget(output)
        self._tool_markers[tool_id] = marker
        self._tool_outputs[tool_id] = output
        asyncio.create_task(self._mount_async(marker))
        asyncio.create_task(self._mount_tool_output(marker, output))
        if self._sticky:
            self.scroll_end()

    async def _mount_tool_output(
        self, marker: ToolCallMarker, output: CollapsibleToolOutput
    ) -> None:
        await self.mount(output, after=marker)

    def complete_tool_call_inline(self, tool_id: str, output: str, is_error: bool) -> None:
        marker = self._tool_markers.pop(tool_id, None)
        if marker is not None:
            marker.set_complete(output, is_error)
        out_widget = self._tool_outputs.get(tool_id)
        if out_widget is not None:
            out_widget.set_output(output)
        self._thinking_display = None
        if self._sticky:
            self.scroll_end()

    def add_subagent_marker(self, subagent_id: str, description: str) -> None:
        self._force_flush_stream_buffer()
        self._finalize_streaming()
        self._current_body = None
        existing = self._subagent_markers.get(subagent_id)
        if existing is not None:
            asyncio.create_task(self._remove_async(existing))
        marker = SubagentMarker(subagent_id, description)
        self._subagent_markers[subagent_id] = marker
        asyncio.create_task(self._mount_async(marker))
        if self._sticky:
            self.scroll_end()

    def complete_subagent_marker(
        self, subagent_id: str, elapsed: float, state: Literal["done", "error"]
    ) -> None:
        marker = self._subagent_markers.get(subagent_id)
        if marker is None:
            return
        elapsed_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{int(elapsed)}s"
        if state == "done":
            marker.update(f"◑ task: {marker.description} · done {elapsed_str}")
            marker.add_class("marker-done")
        else:
            marker.update(f"⊗ task: {marker.description} · error {elapsed_str}")
            marker.add_class("marker-error")
        if self._sticky:
            self.scroll_end()

    def emit_todo_note(self, summary: str) -> None:
        if summary == self._last_todo_summary:
            return
        self._last_todo_summary = summary
        self.append_system_note(f"todos: {summary}")

    def append_system_note(self, text: str) -> None:
        self._force_flush_stream_buffer()
        self._finalize_streaming()
        self._current_body = None
        asyncio.create_task(self._mount_async(SystemNote(f"· {text}")))
        if self._sticky:
            self.scroll_end()

    async def clear_content(self) -> None:
        self._force_flush_stream_buffer()
        self._dismiss_thinking_widget()
        for child in list(self.children):
            await child.remove()
        self._tool_markers.clear()
        self._tool_outputs.clear()
        self._subagent_markers.clear()
        self._last_todo_summary = ""
        self._current_body = None
        self._current_overlay = None
        self._thinking_display = None
        self._thinking_reasoning = ""
        self._assistant_label_mounted = False
        self._welcome = None
        self.mount_welcome()
