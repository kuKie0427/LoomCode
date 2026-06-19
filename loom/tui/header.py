"""TUI Header (summary rail) — dock-top 1-line collapsed + click-to-expand overlay.

Implements ``docs/tui-design-language.md`` §4.3 — the 6th layout region
that aggregates three subsystems (MCP / Todo / Subagent) into one glanceable
line. The Header is a long-loop aesthetic: it is exactly 1 line when collapsed
(§2 rule 1 — bounded re-layout), shows the worst-state aggregate glyph per
section (§4.3.1), hides zero-count sections (§4.3.1 hide rule), and toggles
its overlay panel **instantly** with no animation (§6 — instant replace).

Data flow:
    1. The App injects an initial ``HeaderState`` (see ``DEFAULT_MOCK_STATE``)
       in ``on_mount``. Real backend wiring (MCP server state, todo list,
       subagent count) is deferred to a follow-up feature — for now the
       header is driven by mock data only.
    2. ``Header.update_state(state)`` re-renders the collapsed line.
    3. Clicking the collapsed line posts ``Header.Toggle`` which the App
       handles by mounting/removing a single ``HeaderOverlay`` instance.

The pure helper functions (``mcp_glyph``, ``todo_glyph``, ``subagent_glyph``)
are the spec's contract — they are unit-tested without any Textual app
and locked by eval cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

_GLYPH_HEALTHY = "●"            # MCP all connected
_GLYPH_DONE = "✓"               # Todo all done
_GLYPH_WARNING = "◌"            # MCP any error
_GLYPH_ACTIVE = "◐"             # Todo has active / Subagent has running
_GLYPH_DISABLED = "○"           # all disabled / empty
_GLYPH_TRIANGLE_DOWN = "▼"      # expand affordance


@dataclass
class MCPServer:
    """Mock representation of a connected MCP server.

    ``state`` follows the spec's three-state machine: a server is either
    fully connected, in error (one bad server fails the whole MCP aggregate),
    or disabled by the user.
    """

    name: str
    state: Literal["connected", "error", "disabled"]


@dataclass
class TodoItem:
    """Mock representation of a single todo entry.

    Three-state machine: ``pending`` (not started), ``active`` (current
    focus — drives the ``◐`` aggregate glyph), ``done`` (completed).
    """

    text: str
    state: Literal["pending", "active", "done"]


@dataclass
class Subagent:
    """Mock representation of a running / finished subagent invocation.

    The Header only stores summary state (id + state + elapsed) — the full
    subagent transcript lives in the ChatLog, so the overlay row is
    "summary only" per spec §4.3.2.
    """

    id: str
    state: Literal["running", "done", "error"]
    elapsed: str


@dataclass
class HeaderState:
    """Aggregate state for all three subsystems shown in the Header.

    Empty lists are valid: any section whose list is empty is hidden
    from the collapsed line per spec §4.3.1.
    """

    mcps: list[MCPServer] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    subagents: list[Subagent] = field(default_factory=list)


def mcp_glyph(servers: list[MCPServer]) -> tuple[str, int, int]:
    """Return the aggregate MCP glyph + (connected_count, total_count).

    Worst-state aggregation per spec §4.3.1:
      * all connected → ``("●", M, M)``
      * any error → ``("◌", 0, M)`` (one error fails the subsystem)
      * all disabled OR empty → ``("○", 0, 0)``
    """
    if not servers:
        return (_GLYPH_DISABLED, 0, 0)
    total = len(servers)
    if any(s.state == "error" for s in servers):
        return (_GLYPH_WARNING, 0, total)
    if all(s.state == "connected" for s in servers):
        return (_GLYPH_HEALTHY, total, total)
    # All-disabled (or connected+disabled mix with no error) collapses to
    # the disabled aggregate per the spec's three-state mapping.
    return (_GLYPH_DISABLED, 0, total)


def todo_glyph(items: list[TodoItem]) -> tuple[str, int, int]:
    """Return the aggregate Todo glyph + (active_count, total_count).

    Spec §4.3.1:
      * has any active → ``("◐", 0, M)``
      * all done (and non-empty) → ``("✓", 0, M)``
      * empty (or all pending) → ``("○", 0, 0)``
    """
    if not items:
        return (_GLYPH_DISABLED, 0, 0)
    total = len(items)
    if any(i.state == "active" for i in items):
        return (_GLYPH_ACTIVE, 0, total)
    if all(i.state == "done" for i in items):
        return (_GLYPH_DONE, 0, total)
    # All-pending falls through to the disabled aggregate.
    return (_GLYPH_DISABLED, 0, total)


def subagent_glyph(items: list[Subagent]) -> tuple[str | None, int]:
    """Return the subagent glyph + count, or ``(None, 0)`` when hidden.

    Spec §4.3.1: Subagent count = 0 hides the **entire** section
    (no ``0 subagent`` placeholder). When count > 0, the glyph is ``◐``
    (yellow) — has-running or any non-empty state.
    """
    count = len(items)
    if count == 0:
        return (None, 0)
    if any(s.state == "running" for s in items):
        return (_GLYPH_ACTIVE, count)
    # Non-empty but no running agents still shows the active glyph so the
    # section is visible (spec §4.3.1 — only count=0 hides the section).
    return (_GLYPH_ACTIVE, count)


def _mcp_glyph_rich(glyph: str) -> str:
    if glyph == _GLYPH_HEALTHY:
        return f"[green]{glyph}[/]"
    if glyph == _GLYPH_WARNING:
        return f"[yellow]{glyph}[/]"
    return f"[dim]{glyph}[/]"


def _todo_glyph_rich(glyph: str, state: str | None = None) -> str:
    if state == "done":
        return f"[green]{glyph}[/]"
    if state == "active":
        return f"[yellow]{glyph}[/]"
    if state == "pending":
        return f"[text-muted]{glyph}[/]"
    if glyph == _GLYPH_DONE:
        return f"[green]{glyph}[/]"
    if glyph == _GLYPH_ACTIVE:
        return f"[yellow]{glyph}[/]"
    return f"[dim]{glyph}[/]"


def _subagent_glyph_rich(glyph: str, state: str | None = None) -> str:
    if state == "error":
        return f"[yellow]{glyph}[/]"
    if state == "done":
        return f"[green]{glyph}[/]"
    if state == "running":
        return f"[yellow]{glyph}[/]"
    if glyph == _GLYPH_ACTIVE:
        return f"[yellow]{glyph}[/]"
    return f"[dim]{glyph}[/]"


class Header(Static):
    """1-line collapsed summary rail docked at the top of the TUI.

    Spec §4.3.1: ``dock: top``, ``height: 1``, click target for the whole
    line. Posts ``Header.Toggle`` on click — the App handles mounting /
    removing the overlay panel (instant, no animation per spec §6).
    """

    DEFAULT_CSS = """
    Header {
        height: 1;
    }
    /* NOTE: spec §4.3.1 calls for a hairline bottom border, but
       Textual's `border-bottom: solid` on a height: 1 widget collapses
       the content area to 0 (the border consumes the only line). The
       visual separation is provided by the $panel background change
       instead. See docs/tui-design-language.md §4.3.1. */
    """

    class Toggle(Message):
        """Posted when the collapsed line is clicked.

        Bubbles up to the App (default Textual bubble behavior), which is
        responsible for mounting or removing the ``HeaderOverlay`` panel.
        No animation, no transition — instant mount/remove per spec §6.
        """

        def __init__(self) -> None:
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(_GLYPH_TRIANGLE_DOWN, **kwargs)
        self._state: HeaderState = HeaderState()

    def update_state(self, state: HeaderState) -> None:
        """Replace the current state and re-render the collapsed line."""
        self._state = state
        self.refresh()
        self.update(self._render_collapsed())

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Toggle())

    def render(self) -> str:
        return self._render_collapsed()

    def _render_collapsed(self) -> str:
        """Format the collapsed 1-line summary per spec §4.3.1.

        Sections with ``None`` glyph or ``count=0`` are HIDDEN (not
        rendered as ``0/M`` placeholders). Separator is two spaces.
        """
        parts: list[str] = [_GLYPH_TRIANGLE_DOWN]

        mcp_g, _mcp_connected, mcp_total = mcp_glyph(self._state.mcps)
        if mcp_total > 0:
            parts.append(
                f"{_mcp_glyph_rich(mcp_g)} MCP:{mcp_total}/{mcp_total}"
            )

        todo_g, _todo_active, todo_total = todo_glyph(self._state.todos)
        if todo_total > 0:
            parts.append(f"{_todo_glyph_rich(todo_g)} {todo_total}/{todo_total} todos")

        sub_g, sub_count = subagent_glyph(self._state.subagents)
        if sub_g is not None and sub_count > 0:
            parts.append(f"{_subagent_glyph_rich(sub_g)} {sub_count} subagent")

        return "  ".join(parts)


class HeaderOverlay(Widget):
    """Expanded panel that shows 3 sections (MCP / Todo / Subagent).

    Spec §4.3.2: ``height: auto, max-height: 16`` (≈360px in Textual units),
    nested inside the docked ``#header-overlay`` CSS container. Sections
    with ``count=0`` are HIDDEN entirely. The overlay is mounted/removed
    **instantly** by the App (no transition CSS, per spec §6).

    Indentation (spec §2 rule 5 + §4.3.2):
      * Outer column: section headers (``▼ glyph Label:N/M``).
      * 2-col right: detail rows (per-server / per-todo / per-subagent).
      * No 4th tier.
    """

    DEFAULT_CSS = """
    HeaderOverlay {
        height: auto;
    }
    .header-section {
        height: auto;
    }
    .header-section-header {
        height: 1;
        color: $text-muted;
        text-style: dim;
    }
    .header-row {
        height: 1;
        padding-left: 2;
        color: $text;
    }
    .header-row.row-active {
        text-style: bold;
        color: $accent;
    }
    .header-row.row-error {
        color: $warning;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state: HeaderState = HeaderState()

    def update_state(self, state: HeaderState) -> None:
        """Replace the current state. The overlay's children are built
        from ``_state`` in ``compose()``, which Textual runs when the
        widget is mounted. The state must therefore be set BEFORE the
        widget is mounted (see ``App.on_header_toggle``).
        """
        self._state = state

    def compose(self) -> ComposeResult:
        yield from self._build_sections()

    def _build_sections(self):
        """Yield section widgets for the current state.

        Order matches the collapsed line: MCP → Todos → Subagent. Each
        section is wrapped in a ``Vertical`` (class=header-section) so
        the 3 sections can be queried as a group. IDs are not used on
        per-section containers because Textual requires widget IDs to
        be unique within a parent.
        """
        mcp_g, _, mcp_total = mcp_glyph(self._state.mcps)
        if mcp_total > 0:
            with Vertical(classes="header-section"):
                yield Static(
                    f"{_GLYPH_TRIANGLE_DOWN} {_mcp_glyph_rich(mcp_g)} MCP:{mcp_total}/{mcp_total}",
                    classes="header-section-header",
                )
                for server in self._state.mcps:
                    row_glyph = {
                        "connected": _GLYPH_HEALTHY,
                        "error": _GLYPH_WARNING,
                        "disabled": _GLYPH_DISABLED,
                    }[server.state]
                    row = (
                        f"{_mcp_glyph_rich(row_glyph)} "
                        f"[cyan]{server.name}[/]  [text-muted]{server.state}[/]"
                    )
                    yield Static(row, classes="header-row row-detail")

        todo_g, _, todo_total = todo_glyph(self._state.todos)
        if todo_total > 0:
            with Vertical(classes="header-section"):
                yield Static(
                    f"{_GLYPH_TRIANGLE_DOWN} {_todo_glyph_rich(todo_g)} {todo_total}/{todo_total} todos",
                    classes="header-section-header",
                )
                for idx, item in enumerate(self._state.todos, start=1):
                    row_glyph = {
                        "done": _GLYPH_DONE,
                        "active": _GLYPH_ACTIVE,
                        "pending": _GLYPH_DISABLED,
                    }[item.state]
                    row = f"{_todo_glyph_rich(row_glyph, item.state)} {idx}. {item.text}"
                    classes = "header-row row-detail"
                    if item.state == "active":
                        classes += " row-active"
                    yield Static(row, classes=classes)

        sub_g, sub_count = subagent_glyph(self._state.subagents)
        if sub_g is not None and sub_count > 0:
            with Vertical(classes="header-section"):
                yield Static(
                    f"{_GLYPH_TRIANGLE_DOWN} {_subagent_glyph_rich(sub_g)} {sub_count} subagent",
                    classes="header-section-header",
                )
                for sub in self._state.subagents:
                    row_glyph = {
                        "running": _GLYPH_ACTIVE,
                        "done": _GLYPH_DONE,
                        "error": _GLYPH_WARNING,
                    }[sub.state]
                    row = (
                        f"{_subagent_glyph_rich(row_glyph, sub.state)} "
                        f"[cyan]{sub.id}[/]  "
                        f"[text-muted]· {sub.state}[/]  "
                        f"[text-muted]· {sub.elapsed}[/]"
                    )
                    yield Static(row, classes="header-row row-detail")


DEFAULT_MOCK_STATE: HeaderState = HeaderState(
    mcps=[
        MCPServer(name="db", state="connected"),
        MCPServer(name="fs", state="connected"),
        MCPServer(name="gh", state="error"),
    ],
    todos=[
        TodoItem(text="Read context.py", state="done"),
        TodoItem(text="Fix microcompact preservation", state="active"),
        TodoItem(text="Add regression test", state="pending"),
        TodoItem(text="Update progress.md", state="pending"),
        TodoItem(text="Commit", state="pending"),
    ],
    subagents=[
        Subagent(id="extract-001", state="running", elapsed="4s"),
    ],
)
