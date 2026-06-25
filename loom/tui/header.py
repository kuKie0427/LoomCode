"""TUI Header (summary rail) — dock-top 1-line collapsed + per-section click-to-expand overlay.

Implements ``docs/tui-design-language.md`` §4.3 — the 6th layout region
that aggregates three subsystems (MCP / Todo / Subagent) into one glanceable
line. The Header is a long-loop aesthetic: it is exactly 1 line when collapsed
(§2 rule 1 — bounded re-layout), shows the worst-state aggregate glyph per
section (§4.3.1), hides zero-count sections (§4.3.1 hide rule), and toggles
its overlay panel **instantly** with no animation (§6 — instant replace).

Per-section toggle (deviation from initial 2026-06-19 spec, locked 2026-06-20):
Each section in the collapsed line is an independently clickable button
(HeaderSectionButton). Clicking a section's button:
  * expands its overlay if no overlay is open OR a different section is open
  * collapses the overlay if the same section is already open (toggle)

Only ONE section overlay is visible at a time (mutual exclusion).
Collapse also fires on ESC key or click outside the overlay (chat log /
status bar / composer).

Data flow:
    1. The App builds an initial ``HeaderState`` in ``__init__`` via
       ``_build_initial_header_state()``: MCP servers are read from
       ``mcp_manager.get_server_snapshot()`` (real connection state);
       todos and subagent lists start empty and are populated by
       agent_loop callbacks during a session.
    2. ``Header.update_state(state)`` re-renders each section button.
    3. Clicking a section button posts ``Header.SectionToggle(section)`` which
       the App handles by mounting/removing/replacing the ``HeaderOverlay``.

The pure helper functions (``mcp_glyph``, ``todo_glyph``, ``subagent_glyph``)
are the spec's contract — they are unit-tested without any Textual app
and locked by eval cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click, Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

# ── Glyph constants (spec §4.3.1) ────────────────────────────────────────────

_GLYPH_HEALTHY = "●"            # MCP all connected / Todo all done
_GLYPH_DONE = "✓"               # Todo all done (alias; spec uses ✓)
_GLYPH_WARNING = "◌"            # MCP any error
_GLYPH_ACTIVE = "◐"             # Todo has active / Subagent has running
_GLYPH_DISABLED = "○"           # all disabled / empty

# Section identifiers (per-section toggle keys).
SECTION_MCP = "mcp"
SECTION_TODO = "todo"
SECTION_SUBAGENT = "subagent"
VALID_SECTIONS = (SECTION_MCP, SECTION_TODO, SECTION_SUBAGENT)


# ── Data classes (mock) ──────────────────────────────────────────────────────


@dataclass
class MCPServer:
    """Mock representation of a connected MCP server.

    ``state`` follows the spec's three-state machine: a server is either
    fully connected, in error (one bad server fails the whole MCP aggregate),
    or disabled by the user. ``error`` carries the last failure detail when
    ``state == "error"`` so the overlay can show why the server is down.
    """

    name: str
    state: Literal["connected", "error", "disabled"]
    error: str = ""


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


# ── Pure glyph functions (spec contract) ────────────────────────────────────


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
    ($warning) — has-running or any non-empty state.
    """
    count = len(items)
    if count == 0:
        return (None, 0)
    if any(s.state == "running" for s in items):
        return (_GLYPH_ACTIVE, count)
    # Non-empty but no running agents still shows the active glyph so the
    # section is visible (spec §4.3.1 — only count=0 hides the section).
    return (_GLYPH_ACTIVE, count)


# ── Rich-text glyph wrappers (for collapsed + overlay) ───────────────────────


def _mcp_glyph_rich(glyph: str) -> str:
    if glyph == _GLYPH_HEALTHY:
        return f"[$success]{glyph}[/]"
    if glyph == _GLYPH_WARNING:
        return f"[$warning]{glyph}[/]"
    return f"[$text-muted]{glyph}[/]"


def _todo_glyph_rich(glyph: str, state: str | None = None) -> str:
    if state == "done":
        return f"[$success]{glyph}[/]"
    if state == "active":
        return f"[$warning]{glyph}[/]"
    if state == "pending":
        return f"[$text-muted]{glyph}[/]"
    if glyph == _GLYPH_DONE:
        return f"[$success]{glyph}[/]"
    if glyph == _GLYPH_ACTIVE:
        return f"[$warning]{glyph}[/]"
    return f"[$text-muted]{glyph}[/]"


def _subagent_glyph_rich(glyph: str, state: str | None = None) -> str:
    if state == "error":
        return f"[$warning]{glyph}[/]"
    if state == "done":
        return f"[$success]{glyph}[/]"
    if state == "running":
        return f"[$warning]{glyph}[/]"
    if glyph == _GLYPH_ACTIVE:
        return f"[$warning]{glyph}[/]"
    return f"[$text-muted]{glyph}[/]"


# ── Widgets ──────────────────────────────────────────────────────────────────


class SubagentRow(Static):
    """Clickable subagent row inside ``HeaderOverlay`` (§4.3.2).

    Posts ``Header.SubagentRowClicked(self._tool_use_id)`` on click;
    ``App.on_subagent_row_clicked`` dismisses the overlay and scrolls
    the ChatLog to the ToolCallMarker for that tool_use_id. The event
    is intentionally NOT stopped here — ``HeaderOverlay.on_click``
    stops it to block the App-level catch-all collapse handler.
    """

    can_focus = True

    DEFAULT_CSS = """
    SubagentRow {
        width: 100%;
        height: 1;
        background: transparent;
    }
    SubagentRow:hover {
        text-style: underline;
        color: $accent;
    }
    SubagentRow:focus {
        background: $boost 5%;
        text-style: underline;
        color: $accent;
    }
    """

    def __init__(self, tool_use_id: str, content: str, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._tool_use_id = tool_use_id

    @property
    def tool_use_id(self) -> str:
        return self._tool_use_id

    def on_click(self, event: Click) -> None:
        self.post_message(Header.SubagentRowClicked(self._tool_use_id))

    def on_key(self, event: Key) -> None:
        """Enter/Space activate the subagent row like a click."""
        if event.key in ("enter", "space"):
            event.stop()
            self.post_message(Header.SubagentRowClicked(self._tool_use_id))


class HeaderSectionButton(Static):
    """One section's clickable button in the collapsed Header line.

    Each of the 3 sections (MCP / Todo / Subagent) has its own
    ``HeaderSectionButton``. Buttons are independently clickable:
    clicking a button posts ``Header.SectionToggle(self._section)`` —
    the App handles expand/switch/collapse logic for that section.

    Visual: each button is ``width: 1fr`` so the 3 buttons fill the
    Header's 1-line horizontal track evenly (no dead zones for clicks
    to fall through to the Header container).
    """

    can_focus = True

    pulse_phase: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    HeaderSectionButton {
        width: 1fr;
        height: 1;
        background: transparent;
        padding: 0 1;
        border-left: solid $hairline;
    }
    HeaderSectionButton.first {
        border-left: none;
    }
    HeaderSectionButton:hover {
        text-style: bold;
        color: $text;
        border-left: solid $accent-dim;
    }
    HeaderSectionButton:focus {
        background: $boost 5%;
        text-style: bold;
        border-left: solid $accent-dim;
    }
    HeaderSectionButton.section-hidden {
        visibility: hidden;
        border-left: none;
    }
    HeaderSectionButton {
        opacity: 1.0;
    }
    HeaderSectionButton.pulsing {
        opacity: 1.0;
    }
    /* §4.3 active-state: raised background + bold text when this section's
       overlay is open.  Mirrors the opencode HTML mockup (state 7): the
       active button gets a slightly lighter ground + bolder weight so the
       user knows which section is expanded. */
    HeaderSectionButton.header-btn-active {
        background: $boost 8%;
        text-style: bold;
        color: $text;
    }
    """

    def __init__(self, section: str, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._section: str = section
        self._state: HeaderState | None = None
        self.pulse_phase = 0
        self._pulse_timer: Any | None = None

    @property
    def section(self) -> str:
        """Section identifier (one of VALID_SECTIONS)."""
        return self._section

    def update_state(self, state: HeaderState) -> None:
        """Re-render this button from the new aggregate state."""
        self._state = state
        rendered = self._render_section()
        self.set_class(rendered == "", "section-hidden")
        self.update(rendered)

    def render(self) -> str:
        return self._render_section()

    def _render_section(self) -> str:
        """Format this section's collapsed button text.

        Returns ``""`` when the section should be hidden (count = 0),
        which combined with ``section-hidden`` visibility makes the
        button invisible AND not clickable. Hide rule per spec §4.3.1.
        """
        if self._state is None:
            return ""
        if self._section == SECTION_MCP:
            return self._render_mcp()
        if self._section == SECTION_TODO:
            return self._render_todo()
        if self._section == SECTION_SUBAGENT:
            return self._render_subagent()
        return ""

    def _render_mcp(self) -> str:
        assert self._state is not None
        glyph, _connected, total = mcp_glyph(self._state.mcps)
        if total == 0:
            return ""
        return f"{_mcp_glyph_rich(glyph)} MCP:{total}/{total}"

    def _render_todo(self) -> str:
        assert self._state is not None
        glyph, _active, total = todo_glyph(self._state.todos)
        if total == 0:
            return ""
        return f"{_todo_glyph_rich(glyph)} {total}/{total} todos"

    def _render_subagent(self) -> str:
        assert self._state is not None
        glyph, count = subagent_glyph(self._state.subagents)
        if glyph is None or count == 0:
            return ""
        return f"{_subagent_glyph_rich(glyph)} {count} subagent"

    def on_click(self, event: Click) -> None:
        # Stop propagation so App.on_click doesn't immediately collapse
        # the overlay we're about to mount.
        event.stop()
        # Defensive: if this button is hidden, ignore clicks (visibility
        # already prevents visual click but DOM events can still fire).
        if self.has_class("section-hidden"):
            return
        self.post_message(Header.SectionToggle(self._section))

    def on_key(self, event: Key) -> None:
        """Enter/Space activate the section toggle like a click."""
        if event.key in ("enter", "space"):
            event.stop()
            if self.has_class("section-hidden"):
                return
            self.post_message(Header.SectionToggle(self._section))

    def update_pulse(self, has_count: bool) -> None:
        """§2.2.3 primitive 4: toggle 1Hz pulse based on section count.

        ``has_count=True`` activates the pulse (50% amplitude, 1Hz square
        wave via Python ``set_interval``). ``has_count=False`` freezes
        opacity at 1.0 (idle-freeze invariant §2.2.4 — no motion when idle).
        """
        if has_count:
            self.add_class("pulsing")
            self._start_pulse()
        else:
            self.remove_class("pulsing")
            self._stop_pulse()

    def _start_pulse(self) -> None:
        """Start the 1Hz opacity pulse (50% amplitude)."""
        if self._pulse_timer is not None:
            return
        self._pulse_timer = self.set_interval(0.5, self._tick_pulse, name="header-pulse")

    def _stop_pulse(self) -> None:
        """Stop the pulse and freeze opacity at 1.0 (idle-freeze invariant)."""
        if self._pulse_timer is not None:
            self._pulse_timer.stop()
            self._pulse_timer = None
        self.styles.opacity = 1.0

    def _tick_pulse(self) -> None:
        """Toggle opacity between 1.0 and 0.5 (1Hz square wave)."""
        if not self.has_class("pulsing"):
            self._stop_pulse()
            return
        self.pulse_phase += 1
        self.styles.opacity = 0.5 if self.styles.opacity >= 1.0 else 1.0


class HeaderBrand(Static):
    """Brand label shown in the collapsed Header when all sections are hidden.

    ``display: none`` by default; set ``.visible`` to show.  The label
    consumes only ``width: auto`` so it does not compete with the 3fr
    section buttons when content is present.
    """

    DEFAULT_CSS = """
    HeaderBrand {
        height: 1;
        width: auto;
        color: $text-muted;
        text-style: dim;
        padding: 0 1;
        display: none;
    }
    HeaderBrand.visible {
        display: block;
    }
    """

    def render(self) -> str:
        return "[$text-faint]◆ loom[/]"


class Header(Horizontal):
    """1-line collapsed summary rail docked at the top of the TUI.

    Spec §4.3.1: ``dock: top``, ``height: 1``, contains 3 clickable
    ``HeaderSectionButton`` children — one per subsystem (MCP / Todo /
    Subagent). Each button is independently togglable per the per-section
    toggle design (see module docstring).

    ``active_section`` tracks which section's overlay is currently open
    (``None`` when collapsed).  Each child button checks this reactive
    via ``watch_active_section`` and applies the ``header-btn-active``
    CSS class when its section matches.

    The container itself consumes clicks that fall in any padding/gap
    between buttons (``on_click`` event.stop) so they don't bubble to the
    App's catch-all ``on_click`` and accidentally collapse a freshly
    mounted overlay.
    """

    active_section: reactive[str | None] = reactive(None)

    DEFAULT_CSS = """
    Header {
        height: 1;
        dock: top;
        background: $panel;
        color: $text-muted;
        text-style: dim;
        padding: 0 2;
    }
    """

    class SectionToggle(Message):
        """Posted when a section button is clicked.

        The App handles expand / switch / collapse logic based on
        ``section`` and the current overlay state. No animation, no
        transition — instant mount/remove per spec §6.
        """

        def __init__(self, section: str) -> None:
            super().__init__()
            self.section: str = section

    class SubagentRowClicked(Message):
        """Posted when a subagent row is clicked inside the overlay.

        The App dismisses the overlay and scrolls the ChatLog to the
        ToolCallMarker with this tool_use_id (which is also the
        subagent_id since ``_run_tool_block`` uses ``block.id`` for
        subagent lifecycle callbacks — see
        ``loom/agent/loop.py::_run_tool_block``).
        """

        def __init__(self, tool_use_id: str) -> None:
            super().__init__()
            self.tool_use_id: str = tool_use_id

    # Textual's Message metaclass derives handler_name from the nested class
    # name, which would be ``on_header_subagent_row_clicked``. The App handler
    # is ``on_subagent_row_clicked``, so we pin the dispatch name after class
    # creation to match the existing handler.
    SubagentRowClicked.handler_name = "on_subagent_row_clicked"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._state: HeaderState = HeaderState()

    def watch_active_section(
        self, _old: str | None, new: str | None
    ) -> None:
        """Toggle ``header-btn-active`` CSS class on matching section button.

        Called automatically by Textual when the reactive changes.
        The active button gets a raised background + bold weight per the
        opencode HTML mockup active-state convention (§4.3 state 7).
        """
        for section in VALID_SECTIONS:
            try:
                btn = self.query_one(
                    f"#header-btn-{section}", HeaderSectionButton
                )
                btn.set_class(section == new, "header-btn-active")
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        """Yield the 3 section buttons + brand label.

        IDs use the ``header-btn-<section>`` naming so tests can query
        them individually via ``app.query_one(...)``.  The brand label
        (``HeaderBrand``) is hidden when any section button is visible.
        """
        yield HeaderSectionButton(SECTION_MCP, id="header-btn-mcp", classes="first")
        yield HeaderSectionButton(SECTION_TODO, id="header-btn-todo")
        yield HeaderSectionButton(SECTION_SUBAGENT, id="header-btn-subagent")
        yield HeaderBrand(id="header-brand")

    def update_state(self, state: HeaderState) -> None:
        """Replace the current state and re-render every section button."""
        self._state = state
        any_visible = False
        for section in VALID_SECTIONS:
            try:
                btn = self.query_one(f"#header-btn-{section}", HeaderSectionButton)
                btn.update_state(state)
                if not btn.has_class("section-hidden"):
                    any_visible = True
                # §2.2.3 primitive 4: pulse when section has active items
                if section == SECTION_MCP:
                    has_count = mcp_glyph(state.mcps)[2] > 0
                elif section == SECTION_TODO:
                    has_count = todo_glyph(state.todos)[2] > 0
                elif section == SECTION_SUBAGENT:
                    has_count = subagent_glyph(state.subagents)[1] > 0
                else:
                    has_count = False
                btn.update_pulse(has_count)
            except Exception:
                # Buttons not yet mounted (during __init__) — skip.
                pass
        # Brand label visible only when no section button has content.
        try:
            brand = self.query_one("#header-brand", HeaderBrand)
            brand.set_class(not any_visible, "visible")
        except Exception:
            pass

    def on_click(self, event: Click) -> None:
        # Consume clicks on the Header container (padding / dead zones
        # between buttons if any). Don't bubble to App.on_click — that
        # handler collapses the overlay on any non-Header click, and we
        # want clicks within the Header line to be no-ops rather than
        # collapse.
        event.stop()


class HeaderOverlay(Widget):
    """Expanded panel showing ONE section's detail rows.

    Per the per-section toggle design (2026-06-20 revision), the overlay
    shows only the currently-selected section (MCP / Todo / Subagent).
    Only one HeaderOverlay is mounted at a time.

    Spec §4.3.2: ``height: auto, max-height: 16`` (≈360px in Textual units),
    ``dock: top`` so it sits between the Header and the ChatLog without
    reflowing them. Sections with ``count = 0`` are not rendered (defensive
    — the App should not mount an overlay for a hidden section).

    Indentation (spec §2 rule 5 + §4.3.2):
      * Outer column: section header (``▼ glyph Label:N/M``).
      * 2-col right: detail rows.
      * No 4th tier.

    Clicks on the overlay are consumed (``on_click`` event.stop) so they
    don't bubble to the App's catch-all ``on_click`` and dismiss the
    overlay while the user is reading.
    """

    DEFAULT_CSS = """
    HeaderOverlay {
        height: auto;
        max-height: 16;
        overflow-y: auto;
        background: $panel 97%;
        padding: 1 2;
        border-bottom: solid $border;
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
    .header-row.row-error-detail {
        height: auto;
        max-height: 3;
        overflow-y: auto;
        padding-left: 2;
        color: $error;
        text-style: dim;
    }
    """

    def __init__(self, section: str, state: HeaderState, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._section: str = section
        self._state: HeaderState = state

    @property
    def section(self) -> str:
        """The section this overlay is rendering (``"mcp"``/``"todo"``/``"subagent"``)."""
        return self._section

    def update_state(self, state: HeaderState) -> None:
        """Replace the current state. Children re-render via reactive refresh.

        The overlay's children are built from ``self._state`` in ``compose()``,
        which Textual runs when the widget is mounted. For a mounted overlay,
        the state must therefore be set BEFORE mounting (see
        ``App.on_header_section_toggle``); calling ``update_state`` after
        mounting will refresh the Static children.
        """
        self._state = state
        # Force a re-render of the children widgets.
        for child in self.walk_children(Static):
            child.refresh()

    def compose(self) -> ComposeResult:
        if self._section == SECTION_MCP:
            yield from self._compose_mcp()
        elif self._section == SECTION_TODO:
            yield from self._compose_todo()
        elif self._section == SECTION_SUBAGENT:
            yield from self._compose_subagent()
        # If section is unknown, yield nothing (defensive).

    def _compose_mcp(self) -> ComposeResult:
        glyph, _connected, total = mcp_glyph(self._state.mcps)
        if total == 0:
            return
        with Vertical(classes="header-section"):
            yield Static(
                f"▼ {_mcp_glyph_rich(glyph)} MCP:{total}/{total}",
                classes="header-section-header",
            )
            for server in self._state.mcps:
                row_glyph = {
                    "connected": _GLYPH_HEALTHY,
                    "error": _GLYPH_WARNING,
                    "disabled": _GLYPH_DISABLED,
                }[server.state]
                # Semantic state label per opencode convention:
                # connected → $success, error → $error (with detail), disabled → $text-muted
                state_label = {
                    "connected": "[$success]●[/]",
                    "error": f"[$error]{server.state}[/]",
                    "disabled": f"[$text-muted]{server.state}[/]",
                }[server.state]
                row = (
                    f"{_mcp_glyph_rich(row_glyph)} "
                    f"[$secondary]{server.name}[/]"
                    f"  {state_label}"
                )
                classes = "header-row row-detail"
                if server.state == "error":
                    classes += " row-error"
                yield Static(row, classes=classes)
                if server.state == "error" and server.error:
                    detail = server.error[:200]
                    if len(server.error) > 200:
                        detail += "…"
                    yield Static(
                        f"   [$text-muted]└─[/] [$error]{detail}[/]",
                        classes="header-row row-error-detail",
                    )

    def _compose_todo(self) -> ComposeResult:
        glyph, _active, total = todo_glyph(self._state.todos)
        if total == 0:
            return
        with Vertical(classes="header-section"):
            yield Static(
                f"▼ {_todo_glyph_rich(glyph)} {total}/{total} todos",
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

    def _compose_subagent(self) -> ComposeResult:
        glyph, count = subagent_glyph(self._state.subagents)
        if glyph is None or count == 0:
            return
        with Vertical(classes="header-section"):
            yield Static(
                f"▼ {_subagent_glyph_rich(glyph)} {count} subagent",
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
                    f"[$secondary]{sub.id}[/]  "
                    f"[$text-muted]· {sub.state}[/]  "
                    f"[$text-muted]· {sub.elapsed}[/]"
                )
                yield SubagentRow(sub.id, row, classes="header-row row-detail")

    def on_click(self, event: Click) -> None:
        # Consume clicks within the overlay so App.on_click doesn't
        # dismiss the overlay while the user is reading.
        event.stop()


# ── Default mock state (used by App.on_mount) ───────────────────────────────


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