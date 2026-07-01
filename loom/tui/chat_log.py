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
from pathlib import Path
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click, Key
from textual.reactive import reactive
from textual.widgets import Markdown, Static

from loom.agent.subagent_templates import MAIN_AGENT_NAME

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


def _needs_space_between(prev: str, next: str) -> bool:
    """Return True if a space is needed between ``prev`` and ``next`` char
    when joining two lines that were separated by a single ``\\n``.

    The goal: merge LLM stream chunks split by ``\\n`` back into one line,
    inserting a space ONLY when both sides are ASCII letters — this
    prevents merging English words (``"hello\\nworld" → "hello world"``).
    For CJK characters, digits, punctuation, or any mixed junction,
    no space is needed:

    - ``"已\\n创"`` → ``"已创"``    (CJK, no space)
    - ``"215\\n66"`` → ``"21566"``  (digits, no space — prevents splitting numbers)
    - ``"JS\\n已"`` → ``"JS已"``     (ASCII + CJK, no space)
    - ``"hello\\nworld"`` → ``"hello world"``  (ASCII letters, space)

    Without this, the old ``" ".join(lines)`` behavior inserted spaces
    between every CJK token, causing Static word-wrap to break Chinese
    text every few characters.
    """
    if not prev or not next:
        return False
    # Both ASCII letters → need space to avoid merging English words
    if prev.isascii() and prev.isalpha() and next.isascii() and next.isalpha():
        return True
    # Everything else (CJK, digits, punctuation, mixed) → join directly
    return False


def _normalize_for_stream(text: str) -> str:
    """Normalize text for Markdown streaming.

    Single newlines within plain paragraphs are merged into one line.
    Double newlines are preserved as paragraph breaks.  Structured
    content (tables, lists, headers, code blocks, blockquotes) keeps
    its newlines so the Markdown parser can detect the structure.

    When merging single newlines, a space is inserted ONLY between two
    ASCII letters (see ``_needs_space_between``). CJK text, digits, and
    punctuation join directly without a space — this prevents the
    "every-few-Chinese-chars-on-its-own-line" word-wrap bug caused by
    stray spaces between CJK tokens.
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
    # Plain paragraph: merge single newlines with smart spacing
    result = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if result:
            prev_char = result[-1]
            next_char = line[0]
            if _needs_space_between(prev_char, next_char):
                result += " "
        result += line
    return result


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


def _normalize_tool_name(name: str) -> str:
    """Shorten tool names for display in the marker line.

    read_file → read, write_file → write, edit_file → edit.
    Other names pass through unchanged.
    """
    _TOOL_DISPLAY_NAMES = {"read_file": "read", "write_file": "write", "edit_file": "edit"}
    return _TOOL_DISPLAY_NAMES.get(name, name)


def _truncate_summary(text: str, max_len: int = 50) -> str:
    """Truncate *text* to *max_len* characters, appending ``…`` (U+2026) if truncated.

    Whitespace at both ends is stripped first.
    """
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "\u2026"


def _summarize_tool_args(tool_name: str, tool_input: dict) -> str:
    """Return a short (≤50 chars) textual summary of *tool_input* for *tool_name*.

    Look-up by tool name:

    * ``bash``          — ``tool_input["command"]``
    * ``read_file``,
      ``write_file``,
      ``edit_file``     — ``Path(path).name``
    * ``glob``          — ``tool_input["pattern"]``
    * ``todo_write``    — ``f"{len(todos)} tasks"``

    Defensive: all field accesses use ``.get()``.  Missing/None/unknown → ``""``.
    Never raises.
    """
    if not isinstance(tool_input, dict):
        return ""
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        if isinstance(cmd, str):
            return _truncate_summary(cmd)
        return ""
    if tool_name in ("read_file", "write_file", "edit_file"):
        path = tool_input.get("path", "")
        if isinstance(path, str) and path:
            return _truncate_summary(Path(path).name)
        return ""
    if tool_name == "glob":
        pattern = tool_input.get("pattern", "")
        if isinstance(pattern, str):
            return _truncate_summary(pattern)
        return ""
    if tool_name == "todo_write":
        todos = tool_input.get("todos")
        if isinstance(todos, list):
            return _truncate_summary(f"{len(todos)} tasks")
        return ""
    return ""


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
        padding: 0 2;
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


class StreamingOverlay(Static):
    # Uses Static(markup=False) per Working Rule #16 — Markdown.update()
    # returns unawaited AwaitComplete, causing rendering races during
    # rapid streaming (each token wrapping to its own line).

    DEFAULT_CSS = """
    StreamingOverlay {
        width: 1fr;
        background: $background;
        color: $text;
        padding: 0 2;
        margin: 0 0 1 0;
        border: none;
    }
    """

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content, markup=False, **kwargs)

    def update_content(self, text: str) -> None:
        # Normalize single newlines → spaces (same logic as the final
        # AssistantMessage), so \n tokens from the LLM stream don't cause
        # each word fragment to wrap to its own line during live rendering.
        self.update(_normalize_for_stream(text))


class SystemNote(Static):
    DEFAULT_CSS = """
    SystemNote {
        width: 1fr;
        height: auto;
        color: $text-muted;
        text-style: italic dim;
        padding: 0 2;
        margin: 0 0 1 0;
    }
    SystemNote.severity-error {
        background: $error 10%;
        color: $text;
        text-style: bold;
        border-left: outer $error;
        padding: 0 2;
    }
    """


class AssistantSummary(Static):
    """Summary bar shown after each assistant turn, modeled after
    opencode's ``assistant-summary`` component:

      ▣ <model> · <duration>
    """

    DEFAULT_CSS = """
    AssistantSummary {
        width: 1fr;
        height: auto;
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
            f"[$text-faint]/help · /model · /connect · /clear · /resume[/]"
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
    ThinkingMarker:focus {
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
        self._handle_toggle()

    def on_key(self, event: Key) -> None:
        """Enter/Space toggle the thinking display like a click."""
        if event.key in ("enter", "space"):
            event.stop()
            self._handle_toggle()

    def _handle_toggle(self) -> None:
        """Toggle the thinking display, or notify the user when no thinking
        content has been streamed yet (e.g. non-thinking model, or extended
        thinking disabled). Without this feedback, the click is a silent
        no-op and the user is left wondering why the spinner won't expand.
        """
        if self._display is not None:
            self._on_toggle(self._display)
        else:
            from loom.tui.messages import ShowNotification

            self.post_message(ShowNotification("No thinking content for this response", severity="warning"))

    def set_complete(self) -> None:
        if not self._complete:
            self._complete = True
            self._final_elapsed = self._elapsed_str()
            self.update(f"◦ thought · {self._final_elapsed}")


class CollapsibleToolOutput(Vertical):
    DEFAULT_CSS = """
    CollapsibleToolOutput {
        height: auto;
        max-height: 20;
        overflow-y: auto;
        background: $surface;
        padding: 1 2;
        margin: 0 0 1 2;
        border: none;
    }
    CollapsibleToolOutput.expanded {
        background: $surface 90%;
        border-left: outer $accent-dim;
    }
    CollapsibleToolOutput > Static {
        height: auto;
    }
    """

    def __init__(self, output: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._output = output
        self.display = False

    def compose(self) -> ComposeResult:
        yield Static(_truncate(self._output), markup=False)

    def toggle(self) -> None:
        self.display = not self.display
        self.set_class(self.display, "expanded")

    def set_output(self, text: str) -> None:
        self._output = text
        try:
            static = self.query_one(Static)
            static.update(_truncate(text))
        except Exception:
            pass


class SubagentMarker(Static):
    # Display-only marker: not interactive, so it does not participate in
    # the Tab focus order. Users interact with subagents through the Header
    # overlay rows (SubagentRow) and the linked chat-log ToolCallMarker.
    can_focus = False

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

    def __init__(self, subagent_id: str, description: str, agent_name: str = "织针", **kwargs: Any) -> None:
        super().__init__(f"◐ {agent_name}: {description}", **kwargs)
        self._subagent_id = subagent_id
        self._description = description
        self._agent_name = agent_name

    @property
    def description(self) -> str:
        """The task description shown in the marker text."""
        return self._description

    @property
    def agent_name(self) -> str:
        """The weaving-themed agent display name (织针 / 飞梭 / 经线 / 织补 / 验布)."""
        return self._agent_name


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
    ToolCallMarker:focus {
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

    # §2.2.3 primitive 2: running glyph cycle `⊙⊚◎` 1Hz
    _RUNNING_GLYPHS = ("⊙", "⊚", "◎")

    engine_state: reactive[str] = reactive("idle")
    _cycle_idx: reactive[int] = reactive(0)

    def __init__(self, tool_name: str, args_str: str, *, tool_input: dict | None = None, **kwargs: Any) -> None:
        super().__init__(
            f"{self._RUNNING_GLYPHS[0]} {_normalize_tool_name(tool_name)} · running",
            **kwargs,
        )
        self._tool_name = tool_name
        self._args_str = args_str
        self._tool_input = tool_input or {}
        self._summary = _summarize_tool_args(tool_name, tool_input) if tool_input else ""
        self._output_str = ""
        self._is_error = False
        self._complete = False
        self._output_widget: CollapsibleToolOutput | None = None
        self.engine_state = "idle"
        self._cycle_idx = 0
        self._cycle_timer: Any = None

    def watch_engine_state(self, _old: str, new: str) -> None:
        if self._complete:
            return  # §2.2.4 freeze on complete
        if new == "executing":
            self._start_cycle_timer()
        else:
            self._stop_cycle_timer()
            self._cycle_idx = 0
            self._refresh_glyph()

    def _start_cycle_timer(self) -> None:
        if getattr(self, "_cycle_timer", None) is not None:
            return
        self._cycle_timer = self.set_interval(1.0, self._tick_cycle, name="tool-cycle")

    def _stop_cycle_timer(self) -> None:
        if getattr(self, "_cycle_timer", None) is not None:
            self._cycle_timer.stop()
            self._cycle_timer = None

    def _tick_cycle(self) -> None:
        if self._complete:
            self._stop_cycle_timer()
            return
        self._cycle_idx = (self._cycle_idx + 1) % len(self._RUNNING_GLYPHS)
        self._refresh_glyph()

    def _refresh_glyph(self) -> None:
        if self._complete:
            return
        glyph = self._RUNNING_GLYPHS[self._cycle_idx]
        display_name = _normalize_tool_name(self._tool_name)
        self.update(f"{glyph} {display_name} · {self._summary or 'running'}")

    def on_click(self, event: Click) -> None:
        self._toggle_output()

    def on_key(self, event: Key) -> None:
        """Enter/Space toggle the tool output like a click."""
        if event.key in ("enter", "space"):
            event.stop()
            self._toggle_output()

    def set_output_widget(self, widget: CollapsibleToolOutput) -> None:
        self._output_widget = widget

    def _toggle_output(self) -> None:
        if self._output_widget is not None:
            self._output_widget.toggle()

    def set_complete(self, output: str, is_error: bool) -> None:
        if self._complete:
            return
        self._stop_cycle_timer()  # §2.2.4 freeze on complete
        self._complete = True
        self._output_str = output
        self._is_error = is_error
        glyph = "⊗" if is_error else "⊙"
        status = "error" if is_error else "done"
        display_name = _normalize_tool_name(self._tool_name)
        self.update(f"{glyph} {display_name} · {self._summary or status}")
        self.add_class("tool-error" if is_error else "tool-done")


class ChatLog(VerticalScroll):
    # The ChatLog itself is not a focus target: scrolling is handled globally
    # by the App (mouse wheel + Shift+PgUp/Dn + Ctrl+Home/End), and the user
    # navigates to interactive children (markers) with Tab. Keeping it
    # unfocused also prevents click-to-focus from swallowing App-level
    # "click outside the overlay" collapse behavior.
    can_focus = False

    _sticky: bool = True

    # §2.2.3 primitive 2: ChatLog receives engine_state from App, propagates to all live tool markers
    engine_state: reactive[str] = reactive("idle")

    def watch_scroll_y(self, old_y: float, new_y: float) -> None:
        if new_y < old_y:
            self._sticky = False
        elif new_y > old_y and self.is_vertical_scroll_end:
            self._sticky = True

    def watch_engine_state(self, _old: str, new: str) -> None:
        for marker in self._tool_markers.values():
            marker.engine_state = new

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
        self._finalized_with_overlay: bool = False
        self._pending_summary: AssistantSummary | None = None
        self._assistant_label_mounted: bool = False
        self._stream_full_text: str = ""
        self._stream_flush_timer: Any = None
        self._STREAM_FLUSH_INTERVAL = 0.05
        # Thinking-text flush state — mirrors _stream_full_text/_stream_flush_timer
        # but for ThinkingDelta chunks. Without throttling, extended-thinking
        # models emit hundreds of chunks/second and each one triggers an
        # expensive Markdown.update (full re-parse). 50ms batching keeps
        # the main thread responsive (P0-1 perf fix).
        self._thinking_flush_timer: Any = None
        self._THINKING_FLUSH_INTERVAL = 0.05
        return iter(())

    async def append_user_message(self, text: str) -> None:
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

    def _reset_thinking_state(self) -> None:
        """Reset thinking accumulation between LLM calls within a turn.

        Called from ``on_assistant_message_start`` before each LLM call.
        Hides the previous thinking display and clears the accumulation
        buffer so the new call's thinking starts fresh.
        """
        if self._thinking_display is not None:
            self._thinking_display.display = False
        self._thinking_reasoning = ""
        self._thinking_display = None

    def mount_assistant_label(self) -> None:
        """Mount the "▎ 织轴" TurnLabel for the main agent if not already mounted.

        Idempotent via ``_assistant_label_mounted`` — safe to call from
        ``on_assistant_turn_start`` for every LLM call within a turn;
        the label is mounted once per turn and reset by
        ``_finalize_streaming`` / ``append_user_message``.
        """
        if not self._assistant_label_mounted:
            asyncio.create_task(self._mount_async(TurnLabel(f"▎ {MAIN_AGENT_NAME}", classes="role-assistant")))
            self._assistant_label_mounted = True

    def show_thinking_spinner(self) -> None:
        if self._thinking_widget is not None and not self._thinking_widget._complete:
            return
        if self._thinking_display is not None:
            self._thinking_display.display = False
        self._dismiss_thinking_widget()
        self._current_body = None
        self.mount_assistant_label()
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
        # Flush any pending thinking text before tearing down the marker,
        # so the final thinking content is visible in the toggle.
        self._force_flush_thinking_buffer()
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
                # Pre-existing marker (from show_thinking_spinner) —
                # marker controls visibility via toggle.
                self._thinking_widget._display = display
            else:
                # No pre-existing marker — create one so the thinking
                # content has a clickable toggle ("◦ thought · Xs").
                self._mount_thinking_widget()
                self._thinking_widget._display = display
                # display stays False (default) — marker toggles it.
            asyncio.create_task(self._mount_thinking_display(display))
            # Schedule the first content flush so the display isn't blank
            # until the next chunk arrives.
            self._start_thinking_flush_timer()
        else:
            # Already mounted — just ensure the flush timer is running.
            # Each chunk only appends to _thinking_reasoning; the actual
            # Markdown.update (expensive) happens in _flush_thinking_buffer
            # at most once per 50ms.
            self._start_thinking_flush_timer()

    def _start_thinking_flush_timer(self) -> None:
        """Ensure the thinking flush timer is running (50ms batched update)."""
        if self._thinking_flush_timer is None:
            self._thinking_flush_timer = self.set_interval(
                self._THINKING_FLUSH_INTERVAL, self._flush_thinking_buffer
            )

    def _flush_thinking_buffer(self) -> None:
        """Batched update of the thinking Markdown display.

        Called every 50ms while thinking chunks are arriving. Coalesces
        all accumulated chunks into a single Markdown.update, avoiding
        per-token re-parse overhead (P0-1 perf fix).
        """
        if self._thinking_display is not None and self._thinking_reasoning:
            asyncio.create_task(
                self._update_thinking_display(
                    _normalize_for_stream(self._thinking_reasoning)
                )
            )

    def _force_flush_thinking_buffer(self) -> None:
        """Flush pending thinking text immediately and stop the timer."""
        if self._thinking_flush_timer is not None:
            self._thinking_flush_timer.stop()
            self._thinking_flush_timer = None
        if self._thinking_display is not None and self._thinking_reasoning:
            asyncio.create_task(
                self._update_thinking_display(
                    _normalize_for_stream(self._thinking_reasoning)
                )
            )

    async def _update_thinking_display(self, text: str) -> None:
        if self._thinking_display is not None:
            await self._thinking_display.update(text)

    async def _mount_thinking_display(self, display: ThinkingDisplay) -> None:
        body = self._current_body
        if body is not None and body.parent is self:
            await self.mount(display, before=body)
        else:
            await self.mount(display)
        await display.update(_normalize_for_stream(self._thinking_reasoning))
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
        self._finalized_with_overlay = True  # signal for append_assistant_summary
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
        # Mount any pending summary below the final message.
        if self._pending_summary is not None:
            await self.mount(self._pending_summary)
            self._pending_summary = None
            if self._sticky:
                self.scroll_end()

    def add_tool_call_inline(self, name: str, inp: dict, tool_id: str) -> None:
        self._force_flush_stream_buffer()
        self._dismiss_thinking_widget()
        self._finalize_streaming()
        args_str = json.dumps(inp, ensure_ascii=False, indent=2) if inp else ""
        marker = ToolCallMarker(name, args_str, tool_input=inp)
        marker.engine_state = self.engine_state  # §2.2.3 primitive 2: sync before mount
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

    def add_subagent_marker(self, subagent_id: str, description: str, agent_name: str = "织针") -> None:
        self._force_flush_stream_buffer()
        self._finalize_streaming()
        self._current_body = None
        existing = self._subagent_markers.get(subagent_id)
        if existing is not None:
            asyncio.create_task(self._remove_async(existing))
        marker = SubagentMarker(subagent_id, description, agent_name)
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
        name = marker.agent_name
        if state == "done":
            marker.update(f"◑ {name}: {marker.description} · done {elapsed_str}")
            marker.add_class("marker-done")
        else:
            marker.update(f"⊗ {name}: {marker.description} · error {elapsed_str}")
            marker.add_class("marker-error")
        if self._sticky:
            self.scroll_end()

    def emit_todo_note(self, summary: str) -> None:
        if summary == self._last_todo_summary:
            return
        self._last_todo_summary = summary
        self.append_system_note(f"todos: {summary}")

    def append_system_note(self, text: str, severity: str = "info") -> None:
        self._force_flush_stream_buffer()
        self._finalize_streaming()
        self._current_body = None
        token = {
            "info": "text-muted",
            "success": "success",
            "warning": "warning",
            "error": "error",
        }.get(severity, "text-muted")
        if severity == "error":
            note = SystemNote(f"[$error]✗[/] [$error]{text}[/]")
            note.add_class("severity-error")
        else:
            note = SystemNote(f"[$text-muted]·[/] [${token}]{text}[/]")
        asyncio.create_task(self._mount_async(note))
        if self._sticky:
            self.scroll_end()

    def append_assistant_summary(self, model: str, duration: float) -> None:
        """Append a summary bar below the assistant's final message.

        When the summary is created while a stream finalization is in
        flight (``_finalized_with_overlay`` is set), it is handed to
        ``_mount_final_message`` so that it mounts *after* the response
        text.  Otherwise it is mounted directly.
        """
        secs = int(duration)
        if secs < 60:
            elapsed = f"{secs}s"
        else:
            elapsed = f"{secs // 60}m {secs % 60:02d}s"
        widget = AssistantSummary(
            f"[$accent]▣[/]  {model} · [$text-muted]{elapsed}[/]"
        )
        if self._finalized_with_overlay:
            self._finalized_with_overlay = False
            self._pending_summary = widget
        else:
            asyncio.create_task(self._mount_async(widget))

    async def append_replayed_assistant(self, text: str) -> None:
        """Mount a full AssistantMessage for session replay (non-streaming).

        Mirrors ``append_user_message`` but for the assistant side: mounts a
        TurnLabel + AssistantMessage so the replayed conversation looks the
        same as a live one.
        """
        self._current_body = None
        self._assistant_label_mounted = False
        self._stream_full_text = ""
        self._sticky = True
        label = TurnLabel(f"▎ {MAIN_AGENT_NAME}", classes="role-assistant")
        body = AssistantMessage(text)
        await self.mount(label)
        await self.mount(body)
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
