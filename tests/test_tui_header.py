"""Tests for the TUI Header (summary rail) — f-tui-header-summary-rail.

Locks the spec contract from ``docs/tui-design-language.md`` §4.3:

  * Pure helper functions (mcp_glyph, todo_glyph, subagent_glyph) must
    return worst-state aggregate glyphs per spec §4.3.1.
  * The zero-count hide rule must hide entire sections, not show
    ``0/M`` placeholders.
  * The collapsed 1-line Header must render all visible sections in
    spec order (MCP → Todos → Subagent), each on the same line.
  * The expanded overlay panel must mount below the collapsed line
    and show 3 sections with 2-col indented detail rows.

Test layers:
  1. Pure helper unit tests (no Textual app needed) — 8 cases.
  2. Snapshot tests via pytest-textual-snapshot — 4 cases covering
     the visual contract.

Mock data only (DEFAULT_MOCK_STATE from loom.tui.header).
"""

from __future__ import annotations

import asyncio

from loom.tui.app import AgentTUIApp
from loom.tui.header import (
    DEFAULT_MOCK_STATE,
    Header,
    HeaderOverlay,
    HeaderState,
    MCPServer,
    Subagent,
    TodoItem,
    mcp_glyph,
    subagent_glyph,
    todo_glyph,
)

# ── Pure helper unit tests (no Textual app needed) ──────────────────────────


def test_mcp_glyph_all_connected():
    """All MCPs healthy → green ●, connected_count = total."""
    servers = [MCPServer("db", "connected"), MCPServer("fs", "connected")]
    assert mcp_glyph(servers) == ("●", 2, 2)


def test_mcp_glyph_any_error():
    """Any MCP error → yellow ◌, connected_count = 0 (worst state)."""
    servers = [MCPServer("db", "connected"), MCPServer("gh", "error")]
    assert mcp_glyph(servers) == ("◌", 0, 2)


def test_mcp_glyph_empty():
    """No MCPs → dim ○, counts 0/0 (hidden from collapsed line)."""
    assert mcp_glyph([]) == ("○", 0, 0)


def test_todo_glyph_has_active():
    """Any active todo → yellow ◐."""
    items = [TodoItem("x", "done"), TodoItem("y", "active")]
    assert todo_glyph(items) == ("◐", 0, 2)


def test_todo_glyph_all_done():
    """All todos done → green ✓."""
    items = [TodoItem("x", "done"), TodoItem("y", "done")]
    assert todo_glyph(items) == ("✓", 0, 2)


def test_todo_glyph_empty():
    """No todos → dim ○, counts 0/0 (hidden from collapsed line)."""
    assert todo_glyph([]) == ("○", 0, 0)


def test_subagent_glyph_zero_count():
    """Empty subagent list → (None, 0) — entire section hidden."""
    assert subagent_glyph([]) == (None, 0)


def test_subagent_glyph_has_running():
    """At least one running subagent → yellow ◐ + count."""
    items = [Subagent("extract-001", "running", "4s")]
    assert subagent_glyph(items) == ("◐", 1)


# ── Snapshot tests (Textual snapshot framework) ────────────────────────────


def test_header_collapsed_empty(snap_compare):
    """All 3 sections empty → collapsed line shows only the ▼ affordance."""
    app = AgentTUIApp()
    # Will be overridden by run_before so we can control the state.
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
        await pilot.pause(0.1)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_collapsed_populated(snap_compare):
    """DEFAULT_MOCK_STATE → all 3 sections visible in collapsed line."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        # on_mount already injects DEFAULT_MOCK_STATE; force a re-render
        # to make the snapshot deterministic against the layout state.
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        await pilot.pause(0.1)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_collapsed_subagent_hidden(snap_compare):
    """subagent count=0 → subagent section hidden, MCP+Todo visible."""
    app = AgentTUIApp()
    state = HeaderState(
        mcps=[MCPServer("db", "connected")],
        todos=[TodoItem("only one", "active")],
        subagents=[],
    )
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(state)
        await pilot.pause(0.1)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_expanded(snap_compare):
    """Clicking the collapsed line expands the overlay panel."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        # Click the header to mount the overlay.
        header.post_message(Header.Toggle())
        await pilot.pause(0.2)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


# ── Behavioral tests (no snapshot) ──────────────────────────────────────────


def test_header_renders_collapsed_line_with_all_sections():
    """The collapsed 1-line render must include the 3 visible sections."""
    header = Header()
    header.update_state(DEFAULT_MOCK_STATE)
    text = header.render()
    assert "MCP" in text, f"missing MCP section: {text!r}"
    assert "todos" in text, f"missing todos section: {text!r}"
    assert "subagent" in text, f"missing subagent section: {text!r}"
    assert text.count("\n") == 0, f"collapsed line must be 1 line, got: {text!r}"


def test_header_renders_no_sections_when_all_empty():
    """Empty state → collapsed line is just the ▼ affordance (no 0/M placeholders)."""
    header = Header()
    header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
    text = header.render()
    assert "MCP" not in text, f"empty MCP should be hidden: {text!r}"
    assert "todos" not in text, f"empty todos should be hidden: {text!r}"
    assert "subagent" not in text, f"empty subagent should be hidden: {text!r}"
    assert "0" not in text, f"no 0 placeholders allowed: {text!r}"


def test_header_hides_subagent_section_when_zero():
    """subagent count=0 must hide the entire subagent section."""
    header = Header()
    state = HeaderState(
        mcps=[MCPServer("db", "connected")],
        todos=[TodoItem("one", "active")],
        subagents=[],
    )
    header.update_state(state)
    text = header.render()
    assert "MCP" in text
    assert "todos" in text
    assert "subagent" not in text


def test_header_update_state_replaces_state():
    """update_state must replace the previous state and re-render."""
    header = Header()
    header.update_state(DEFAULT_MOCK_STATE)
    assert "subagent" in header.render()

    header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
    assert "subagent" not in header.render()


def test_header_click_posts_toggle_message():
    """Clicking the collapsed line posts Header.Toggle (bubbles to App)."""
    from textual.events import Click

    posted: list[object] = []
    header = Header()
    original_post = header.post_message

    def capture(msg):
        posted.append(msg)
        return original_post(msg)

    header.post_message = capture  # type: ignore[method-assign]
    try:
        ev = Click(header, 0, 0, 0, 0, 0, False, False, False)
        header.on_click(ev)
    finally:
        header.post_message = original_post  # type: ignore[method-assign]

    assert any(isinstance(m, Header.Toggle) for m in posted), (
        f"Header click must post Toggle, got: {posted}"
    )


def test_app_compose_yields_header_before_chatlog():
    """Spec §3 — Header must be yielded before ChatLog (dock-top invariant)."""
    import inspect

    src = inspect.getsource(AgentTUIApp.compose)
    header_pos = src.find('Header(id="header")')
    chatlog_pos = src.find('ChatLog(id="chat-log")')
    assert header_pos != -1, "Header yield missing from compose()"
    assert chatlog_pos != -1, "ChatLog yield missing from compose()"
    assert header_pos < chatlog_pos, (
        f"Header must be yielded BEFORE ChatLog (dock-top invariant), "
        f"got Header at {header_pos}, ChatLog at {chatlog_pos}"
    )


def test_app_css_has_no_transition():
    """Spec §6 — #header-overlay must NOT have any transition CSS."""
    css = AgentTUIApp.CSS
    assert "#header-overlay" in css, "CSS missing #header-overlay block"
    # The whole CSS string must not contain "transition" — spec §6 forbids
    # any easing for header-related widgets.
    assert "transition" not in css, (
        f"Header CSS must have no transition (spec §6), got: {css}"
    )


def test_app_dock_top_invariant():
    """The #header CSS block must include ``dock: top`` per spec §4.3.1."""
    css = AgentTUIApp.CSS
    # The #header block is between "#header {" and the next closing brace.
    start = css.find("#header {")
    assert start != -1, "CSS missing #header block"
    end = css.find("}", start)
    block = css[start:end]
    assert "dock: top" in block, (
        f"#header block must include ``dock: top``, got: {block!r}"
    )
    assert "height: 1" in block, (
        f"#header block must include ``height: 1`` (spec §2 rule 1), got: {block!r}"
    )


def test_app_uses_custom_header_not_textual_builtin():
    """The App must use loom's custom Header (Static subclass), NOT Textual's
    built-in ``textual.widgets.Header``.

    Per spec §4.3, loom's Header is a Static-based widget with dock-top CSS and
    click-to-expand overlay. Textual's built-in Header is a different widget
    (a title bar with default bindings) and would conflict with our custom
    layout.

    Note: ``app.query(Header)`` matches by CSS class name (which both widgets
    have: ``Header``), so we use exact ``type(w) is X`` identity checks.
    """

    async def driver():
        from textual.widgets import Header as BuiltinHeader

        from loom.tui.header import Header as LoomHeader

        app = AgentTUIApp()
        async with app.run_test(size=(120, 25)) as pilot:
            await pilot.pause(0.05)
            builtin_count = sum(
                1 for w in app.screen.walk_children() if type(w) is BuiltinHeader
            )
            assert builtin_count == 0, (
                f"Textual's built-in Header should NOT be present — loom uses "
                f"its own Header. Found {builtin_count} instance(s)."
            )
            loom_count = sum(
                1 for w in app.screen.walk_children() if type(w) is LoomHeader
            )
            assert loom_count == 1, (
                f"loom Header (loom.tui.header.Header) must be present "
                f"per spec §4.3. Found {loom_count} instance(s)."
            )

    asyncio.run(driver())


def test_overlay_appears_on_header_toggle():
    """Clicking the Header mounts the HeaderOverlay; clicking again removes it."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            # Initially: no overlay mounted
            try:
                app.query_one(HeaderOverlay)
                initial_present = True
            except Exception:
                initial_present = False
            assert not initial_present, "HeaderOverlay must not be mounted on start"

            # First click → overlay mounted
            app.post_message(Header.Toggle())
            await pilot.pause(0.2)
            try:
                overlay = app.query_one(HeaderOverlay)
            except Exception:
                overlay = None
            assert overlay is not None, "HeaderOverlay must be mounted after first click"
            assert overlay.id == "header-overlay"

            # Second click → overlay removed
            app.post_message(Header.Toggle())
            await pilot.pause(0.2)
            try:
                app.query_one(HeaderOverlay)
                still_present = True
            except Exception:
                still_present = False
            assert not still_present, "HeaderOverlay must be removed on second click"

    asyncio.run(driver())


def test_overlay_contains_three_sections():
    """Expanded overlay must show MCP / Todos / Subagent sections."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            app.post_message(Header.Toggle())
            await pilot.pause(0.2)
            overlay = app.query_one(HeaderOverlay)
            sections = overlay.query("Vertical.header-section")
            assert len(sections) == 3, (
                f"overlay must contain 3 sections (MCP/Todos/Subagent), "
                f"got {len(sections)}: {sections}"
            )

    asyncio.run(driver())
