"""Tests for the TUI Header (summary rail) — f-tui-header-summary-rail.

Locks the spec contract from ``docs/tui-design-language.md`` §4.3 and the
per-section toggle design locked 2026-06-20:

  * Pure helper functions (mcp_glyph, todo_glyph, subagent_glyph) must
    return worst-state aggregate glyphs per spec §4.3.1.
  * The zero-count hide rule must hide entire sections, not show
    ``0/M`` placeholders.
  * The collapsed 1-line Header has 3 independently clickable section
    buttons (HeaderSectionButton) — MCP, Todos, Subagent.
  * Each section is independently togglable; only one overlay is visible
    at a time (mutual exclusion). Switching sections replaces the
    overlay; clicking the same section collapses it.
  * Collapse mechanisms: ESC key, click outside the overlay (chat log /
    status bar / composer), click the same section button twice.

Test layers:
  1. Pure helper unit tests (no Textual app needed) — 8 cases.
  2. Snapshot tests via pytest-textual-snapshot — collapsed + expanded
     states for each section.
  3. Behavioral tests — per-section toggle + collapse mechanisms.

Mock data only (DEFAULT_MOCK_STATE from loom.tui.header).
"""

from __future__ import annotations

import asyncio
import inspect

from textual.events import Click

from loom.tui.app import AgentTUIApp
from loom.tui.header import (
    DEFAULT_MOCK_STATE,
    SECTION_MCP,
    SECTION_SUBAGENT,
    SECTION_TODO,
    VALID_SECTIONS,
    Header,
    HeaderOverlay,
    HeaderSectionButton,
    HeaderState,
    MCPServer,
    Subagent,
    TodoItem,
    mcp_glyph,
    subagent_glyph,
    todo_glyph,
)


def test_mcp_glyph_all_connected():
    servers = [MCPServer("db", "connected"), MCPServer("fs", "connected")]
    assert mcp_glyph(servers) == ("●", 2, 2)


def test_mcp_glyph_any_error():
    servers = [MCPServer("db", "connected"), MCPServer("gh", "error")]
    assert mcp_glyph(servers) == ("◌", 0, 2)


def test_mcp_glyph_empty():
    assert mcp_glyph([]) == ("○", 0, 0)


def test_todo_glyph_has_active():
    items = [TodoItem("x", "done"), TodoItem("y", "active")]
    assert todo_glyph(items) == ("◐", 0, 2)


def test_todo_glyph_all_done():
    items = [TodoItem("x", "done"), TodoItem("y", "done")]
    assert todo_glyph(items) == ("✓", 0, 2)


def test_todo_glyph_empty():
    assert todo_glyph([]) == ("○", 0, 0)


def test_subagent_glyph_zero_count():
    assert subagent_glyph([]) == (None, 0)


def test_subagent_glyph_has_running():
    items = [Subagent("extract-001", "running", "4s")]
    assert subagent_glyph(items) == ("◐", 1)


def test_header_collapsed_empty(snap_compare):
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
        await pilot.pause(0.1)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_collapsed_populated(snap_compare):
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        await pilot.pause(0.1)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_collapsed_subagent_hidden(snap_compare):
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


def test_header_expanded_mcp(snap_compare):
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        await pilot.pause(0.05)
        pilot.app.query_one(f"#header-btn-{SECTION_MCP}", HeaderSectionButton).post_message(
            Header.SectionToggle(SECTION_MCP)
        )
        await pilot.pause(0.2)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_expanded_todo(snap_compare):
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        await pilot.pause(0.05)
        pilot.app.query_one(f"#header-btn-{SECTION_TODO}", HeaderSectionButton).post_message(
            Header.SectionToggle(SECTION_TODO)
        )
        await pilot.pause(0.2)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_expanded_subagent(snap_compare):
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.05)
        header = pilot.app.query_one(Header)
        header.update_state(DEFAULT_MOCK_STATE)
        await pilot.pause(0.05)
        pilot.app.query_one(
            f"#header-btn-{SECTION_SUBAGENT}", HeaderSectionButton
        ).post_message(Header.SectionToggle(SECTION_SUBAGENT))
        await pilot.pause(0.2)

    assert snap_compare(app, run_before=run_before, terminal_size=(120, 24))


def test_header_compose_yields_three_section_buttons():
    """Header (Horizontal) yields 3 HeaderSectionButton children in spec order MCP → Todo → Subagent."""
    header = Header()
    buttons = list(header.compose())
    assert len(buttons) == 3, f"expected 3 section buttons, got {len(buttons)}"
    for btn, expected_section in zip(buttons, VALID_SECTIONS, strict=True):
        assert isinstance(btn, HeaderSectionButton), (
            f"child must be HeaderSectionButton, got {type(btn)}"
        )
        assert btn.section == expected_section, (
            f"button section mismatch: expected {expected_section}, got {btn.section}"
        )
    assert [b.section for b in buttons] == [SECTION_MCP, SECTION_TODO, SECTION_SUBAGENT]


def test_section_button_renders_only_its_section():
    state = DEFAULT_MOCK_STATE
    btn_mcp = HeaderSectionButton(SECTION_MCP)
    btn_mcp.update_state(state)
    btn_todo = HeaderSectionButton(SECTION_TODO)
    btn_todo.update_state(state)
    btn_sub = HeaderSectionButton(SECTION_SUBAGENT)
    btn_sub.update_state(state)
    assert "MCP" in btn_mcp.render(), f"MCP button missing MCP: {btn_mcp.render()!r}"
    assert "todos" in btn_todo.render(), f"todo button missing todos: {btn_todo.render()!r}"
    assert "subagent" in btn_sub.render(), f"subagent button missing subagent: {btn_sub.render()!r}"


def test_section_button_hides_when_count_zero():
    header = Header()
    state = HeaderState(mcps=[], todos=[], subagents=[])
    header.update_state(state)
    buttons = list(header.query(HeaderSectionButton))
    for btn in buttons:
        assert btn.render() == "", (
            f"{btn.section} button should render empty when count=0, got {btn.render()!r}"
        )
        assert btn.has_class("section-hidden"), (
            f"{btn.section} button should have section-hidden class"
        )


def test_header_click_mcp_button_posts_section_toggle():
    posted: list[object] = []
    btn = HeaderSectionButton(SECTION_MCP)
    original_post = btn.post_message

    def capture(msg):
        posted.append(msg)
        return original_post(msg)

    btn.post_message = capture  # type: ignore[method-assign]
    try:
        ev = Click(btn, 0, 0, 0, 0, 0, False, False, False)
        btn.on_click(ev)
    finally:
        btn.post_message = original_post  # type: ignore[method-assign]

    assert any(
        isinstance(m, Header.SectionToggle) and m.section == SECTION_MCP for m in posted
    ), f"MCP click must post SectionToggle('mcp'), got: {posted}"


def test_header_click_todo_button_posts_section_toggle():
    posted: list[object] = []
    btn = HeaderSectionButton(SECTION_TODO)
    original_post = btn.post_message

    def capture(msg):
        posted.append(msg)
        return original_post(msg)

    btn.post_message = capture  # type: ignore[method-assign]
    try:
        ev = Click(btn, 0, 0, 0, 0, 0, False, False, False)
        btn.on_click(ev)
    finally:
        btn.post_message = original_post  # type: ignore[method-assign]

    assert any(
        isinstance(m, Header.SectionToggle) and m.section == SECTION_TODO for m in posted
    ), f"todo click must post SectionToggle('todo'), got: {posted}"


def test_header_click_subagent_button_posts_section_toggle():
    posted: list[object] = []
    btn = HeaderSectionButton(SECTION_SUBAGENT)
    original_post = btn.post_message

    def capture(msg):
        posted.append(msg)
        return original_post(msg)

    btn.post_message = capture  # type: ignore[method-assign]
    try:
        ev = Click(btn, 0, 0, 0, 0, 0, False, False, False)
        btn.on_click(ev)
    finally:
        btn.post_message = original_post  # type: ignore[method-assign]

    assert any(
        isinstance(m, Header.SectionToggle) and m.section == SECTION_SUBAGENT
        for m in posted
    ), f"subagent click must post SectionToggle('subagent'), got: {posted}"


def test_header_click_consumes_event():
    """event.stop() must be called so App.on_click doesn't immediately collapse
    the overlay the App is about to mount."""
    btn = HeaderSectionButton(SECTION_MCP)
    ev = Click(btn, 0, 0, 0, 0, 0, False, False, False)
    btn.on_click(ev)
    assert ev.is_forwarded is False or ev.stopped, (
        "Section button click must stop event propagation (event.stop())"
    )


def test_header_update_state_replaces_state():
    """update_state must propagate to all mounted section buttons."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)
            buttons = list(header.query(HeaderSectionButton))
            assert len(buttons) == 3, f"expected 3 mounted buttons, got {len(buttons)}"
            assert "subagent" in buttons[2].render(), (
                f"subagent button should show subagent, got {buttons[2].render()!r}"
            )

            empty_state = HeaderState(mcps=[], todos=[], subagents=[])
            header.update_state(empty_state)
            await pilot.pause(0.05)
            for btn in header.query(HeaderSectionButton):
                assert btn.render() == "", (
                    f"after empty update, button should be empty: {btn.render()!r}"
                )

    asyncio.run(driver())


def test_app_compose_yields_header_before_chatlog():
    """Spec §3 — Header must be yielded before ChatLog (dock-top invariant)."""
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
    """Spec §6 — HeaderOverlay CSS must NOT have any transition (instant toggle)."""
    css = AgentTUIApp.CSS
    assert "HeaderOverlay" in css, "CSS missing HeaderOverlay block"
    assert "transition" not in css, (
        f"Header CSS must have no transition (spec §6), got: {css}"
    )


def test_header_default_css_has_dock_top():
    """Header.DEFAULT_CSS must include ``dock: top`` + ``height: 1`` per spec §4.3.1."""
    css = Header.DEFAULT_CSS
    start = css.find("Header {")
    assert start != -1, "Header.DEFAULT_CSS missing Header block"
    end = css.find("}", start)
    block = css[start:end]
    assert "dock: top" in block, (
        f"Header block must include ``dock: top``, got: {block!r}"
    )
    assert "height: 1" in block, (
        f"Header block must include ``height: 1`` (spec §2 rule 1), got: {block!r}"
    )


def test_header_default_css_has_no_transition():
    """Header DEFAULT_CSS must not contain transition (spec §6 — instant toggle)."""
    assert "transition" not in Header.DEFAULT_CSS, (
        f"Header CSS must have no transition (spec §6), got: {Header.DEFAULT_CSS}"
    )


def test_app_uses_custom_header_not_textual_builtin():
    """The App must use loom's custom Header (Horizontal subclass), NOT Textual's
    built-in ``textual.widgets.Header``.

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


def test_overlay_appears_on_first_section_click():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            try:
                app.query_one(HeaderOverlay)
                initial_present = True
            except Exception:
                initial_present = False
            assert not initial_present, "HeaderOverlay must not be mounted on start"

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            overlay = app.query_one(HeaderOverlay)
            assert overlay.section == SECTION_MCP, (
                f"overlay should show MCP section, got {overlay.section}"
            )

    asyncio.run(driver())


def test_overlay_collapses_on_same_section_click():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            assert app.query_one(HeaderOverlay).section == SECTION_MCP

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            try:
                app.query_one(HeaderOverlay)
                still_present = True
            except Exception:
                still_present = False
            assert not still_present, (
                "HeaderOverlay must be removed when clicking same section"
            )

    asyncio.run(driver())


def test_overlay_switches_section_on_different_click():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            assert app.query_one(HeaderOverlay).section == SECTION_MCP

            app.post_message(Header.SectionToggle(SECTION_TODO))
            await pilot.pause(0.2)
            assert app.query_one(HeaderOverlay).section == SECTION_TODO, (
                "overlay should switch to todo section"
            )

    asyncio.run(driver())


def test_only_one_overlay_at_a_time():
    """Mutual exclusion: at most one HeaderOverlay mounted at any time."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            for section in VALID_SECTIONS:
                app.post_message(Header.SectionToggle(section))
                await pilot.pause(0.1)
                overlays = list(app.query(HeaderOverlay))
                assert len(overlays) == 1, (
                    f"only 1 overlay at a time, got {len(overlays)} after "
                    f"clicking {section}"
                )

    asyncio.run(driver())


def test_overlay_collapses_on_escape():
    """Spec §4.3.2 — ESC collapses the overlay."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            assert app.query_one(HeaderOverlay).section == SECTION_MCP

            await pilot.press("escape")
            await pilot.pause(0.2)
            try:
                app.query_one(HeaderOverlay)
                still_present = True
            except Exception:
                still_present = False
            assert not still_present, "ESC must collapse the overlay"

    asyncio.run(driver())


def test_overlay_collapses_on_click_outside():
    """Click on chat log (outside Header + overlay) collapses the overlay."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            assert app.query_one(HeaderOverlay).section == SECTION_MCP

            chat_log = app.query_one("#chat-log")
            # y=15 is below Header (y=0) and the overlay (y=1..N)
            ev = Click(chat_log, 60, 15, 60, 15, 0, False, False, False)
            chat_log.post_message(ev)
            await pilot.pause(0.2)
            try:
                app.query_one(HeaderOverlay)
                still_present = True
            except Exception:
                still_present = False
            assert not still_present, (
                "Click on chat log (outside Header+overlay) must collapse overlay"
            )

    asyncio.run(driver())


def test_overlay_stays_when_clicked_on_overlay_content():
    """Clicking the overlay CONTENT does NOT collapse (user is reading)."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            app.post_message(Header.SectionToggle(SECTION_MCP))
            await pilot.pause(0.2)
            overlay = app.query_one(HeaderOverlay)
            assert overlay.section == SECTION_MCP

            ev = Click(overlay, 5, 2, 5, 2, 0, False, False, False)
            overlay.post_message(ev)
            await pilot.pause(0.2)
            still_present = app.query_one(HeaderOverlay)
            assert still_present is not None, (
                "Clicking overlay content must NOT collapse it"
            )

    asyncio.run(driver())


def test_escape_is_noop_when_no_overlay():
    """Pressing ESC with no overlay open is a silent no-op (no exception)."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(DEFAULT_MOCK_STATE)
            await pilot.pause(0.05)

            try:
                app.query_one(HeaderOverlay)
                initial_present = True
            except Exception:
                initial_present = False
            assert not initial_present

            await pilot.press("escape")
            await pilot.pause(0.1)
            try:
                app.query_one(HeaderOverlay)
                still_present = True
            except Exception:
                still_present = False
            assert not still_present, "ESC with no overlay should not create one"

    asyncio.run(driver())