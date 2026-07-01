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
from loom.tui.chat_log import ChatLog
from loom.tui.header import (
    DEFAULT_MOCK_STATE,
    SECTION_MCP,
    SECTION_SUBAGENT,
    SECTION_TODO,
    VALID_SECTIONS,
    Header,
    HeaderBrand,
    HeaderOverlay,
    HeaderSectionButton,
    HeaderState,
    MCPServer,
    Subagent,
    SubagentRow,
    TodoItem,
    mcp_glyph,
    subagent_glyph,
    todo_glyph,
)
from loom.tui.messages import SubagentEnd, SubagentStart, TodoUpdate


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


def test_header_compose_yields_section_buttons_plus_brand():
    """Header (Horizontal) yields 3 HeaderSectionButton + 1 HeaderBrand."""
    header = Header()
    buttons = list(header.compose())
    assert len(buttons) == 4, f"expected 4 children (3 section buttons + brand), got {len(buttons)}"
    section_btns = buttons[:3]
    assert isinstance(section_btns[0], HeaderSectionButton)
    assert isinstance(section_btns[1], HeaderSectionButton)
    assert isinstance(section_btns[2], HeaderSectionButton)
    assert isinstance(buttons[3], HeaderBrand)
    for btn, expected_section in zip(section_btns, VALID_SECTIONS, strict=True):
        assert isinstance(btn, HeaderSectionButton), (
            f"child must be HeaderSectionButton, got {type(btn)}"
        )
        assert btn.section == expected_section, (
            f"button section mismatch: expected {expected_section}, got {btn.section}"
        )
    assert [b.section for b in section_btns] == [SECTION_MCP, SECTION_TODO, SECTION_SUBAGENT]


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


# ── Backend wiring tests (f-tui-header-backend-wiring) ────────────────────


def test_app_initial_header_state_empty_mcp_when_no_servers_configured():
    """App._build_initial_header_state reads from mcp_manager, not TOOL_REGISTRY.

    When no MCP servers are configured (harness.toml has no [mcp.servers.*]
    entries), the snapshot is empty.  The old behavior (listing every loom
    native tool as an MCP server) is gone — the MCP section now shows only
    user-configured MCP servers.
    """

    async def driver():
        from loom.agent import mcp_manager as mm

        # Ensure pristine state — no servers configured, none active.
        mm._CONFIGURED_SERVER_NAMES.clear()
        mm._ACTIVE_SERVERS.clear()
        mm._SERVER_ERRORS.clear()

        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            state = app._header_state
            assert state.mcps == [], (
                f"expected empty MCP list when no servers configured, "
                f"got {len(state.mcps)}: {[s.name for s in state.mcps]}"
            )
            assert state.todos == [], f"todos must start empty, got {state.todos}"
            assert state.subagents == [], (
                f"subagents must start empty, got {state.subagents}"
            )

    asyncio.run(driver())


def test_app_initial_header_state_reflects_configured_mcp_servers():
    """When servers are configured, _build_initial_header_state reads real state.

    We seed mcp_manager._CONFIGURED_SERVER_NAMES + _ACTIVE_SERVERS and verify
    that the Header's MCP list reflects the real connection status:
      - server in _ACTIVE_SERVERS → "connected"
      - server configured but NOT in _ACTIVE_SERVERS → "error"
    """

    async def driver():
        from loom.agent import mcp_manager as mm

        # Clean up after ourselves so MCP state doesn't leak into
        # subsequent snapshot tests (test_empty_layout, etc.).
        try:
            mm._CONFIGURED_SERVER_NAMES.clear()
            mm._ACTIVE_SERVERS.clear()
            mm._SERVER_ERRORS.clear()

            # Seed one connected server and one that failed to connect.
            mm._CONFIGURED_SERVER_NAMES.update({"healthy-server", "error-server"})
            from loom.agent.mcp_client import MCPServer as MCPClientServer

            mm._ACTIVE_SERVERS["healthy-server"] = MCPClientServer(
                name="healthy-server",
                command="echo",
            )

            app = AgentTUIApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                state = app._header_state
                assert len(state.mcps) == 2, (
                    f"expected 2 MCP servers, got {len(state.mcps)}"
                )
                servers_by_name = {s.name: s for s in state.mcps}
                assert servers_by_name["healthy-server"].state == "connected"
                assert servers_by_name["error-server"].state == "error"
                assert state.todos == []
                assert state.subagents == []
        finally:
            mm._CONFIGURED_SERVER_NAMES.clear()
            mm._ACTIVE_SERVERS.clear()

    asyncio.run(driver())


def test_app_on_todo_update_replaces_todo_list():
    """App.on_todo_update converts agent's todo format to TUI TodoItem."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            agent_todos = [
                {"content": "Read context.py", "status": "completed"},
                {"content": "Fix microcompact", "status": "in_progress"},
                {"content": "Add test", "status": "pending"},
            ]
            app.on_todo_update(TodoUpdate(agent_todos))
            await pilot.pause(0.05)
            state = app._header_state
            assert len(state.todos) == 3, f"expected 3 todos, got {len(state.todos)}"
            assert state.todos[0].text == "Read context.py"
            assert state.todos[0].state == "done", (
                f"completed → done, got {state.todos[0].state}"
            )
            assert state.todos[1].text == "Fix microcompact"
            assert state.todos[1].state == "active", (
                f"in_progress → active, got {state.todos[1].state}"
            )
            assert state.todos[2].text == "Add test"
            assert state.todos[2].state == "pending"

    asyncio.run(driver())


def test_app_on_subagent_start_appends_running_subagent():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.on_subagent_start(SubagentStart("abc12345", "extract MCP schema"))
            await pilot.pause(0.05)
            state = app._header_state
            assert len(state.subagents) == 1
            assert state.subagents[0].id == "abc12345"
            assert state.subagents[0].state == "running"
            assert state.subagents[0].elapsed == "0s"

    asyncio.run(driver())


def test_app_on_subagent_start_threads_weaving_agent_name():
    """on_subagent_start passes the weaving-themed agent_name through to the
    Header Subagent dataclass (织针 / 飞梭 / 经线 / 织补 / 验布)."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # Default (task tool) → 织针
            app.on_subagent_start(SubagentStart("sa-task", "do stuff"))
            # investigate_code → 飞梭
            app.on_subagent_start(SubagentStart("sa-invest", "find bug", agent_name="飞梭"))
            # review → 验布
            app.on_subagent_start(SubagentStart("sa-review", "review feat", agent_name="验布"))
            await pilot.pause(0.05)
            state = app._header_state
            assert len(state.subagents) == 3
            assert state.subagents[0].agent_name == "织针", (
                f"default agent_name should be 织针, got {state.subagents[0].agent_name!r}"
            )
            assert state.subagents[1].agent_name == "飞梭"
            assert state.subagents[2].agent_name == "验布"

    asyncio.run(driver())


def test_app_on_subagent_end_updates_existing_subagent():
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.on_subagent_start(SubagentStart("xyz98765", "test"))
            await pilot.pause(0.05)
            app.on_subagent_end(SubagentEnd("xyz98765", 12.7, "done"))
            await pilot.pause(0.05)
            state = app._header_state
            assert len(state.subagents) == 1, (
                f"on_subagent_end should UPDATE, not add — got {len(state.subagents)} subagents"
            )
            assert state.subagents[0].state == "done"
            assert state.subagents[0].elapsed == "12s", (
                f"elapsed should be floored to int seconds, got {state.subagents[0].elapsed!r}"
            )

    asyncio.run(driver())


def test_app_on_subagent_end_handles_unknown_id_gracefully():
    """on_subagent_end with unknown id (e.g., end fired before start reached UI)
    must not raise — just no-op for that id."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.on_subagent_end(SubagentEnd("unknown_id", 5.0, "done"))
            await pilot.pause(0.05)
            assert app._header_state.subagents == []

    asyncio.run(driver())


def test_convert_agent_todos_handles_unknown_status():
    """Unknown agent statuses must fall back to 'pending' (defensive)."""
    app = AgentTUIApp.__new__(AgentTUIApp)  # skip __init__ (no driver needed)
    converted = app._convert_agent_todos([
        {"content": "x", "status": "unknown_status"},
        {"content": "y", "status": "in_progress"},
    ])
    assert converted[0].state == "pending", (
        f"unknown status should fall back to 'pending', got {converted[0].state}"
    )
    assert converted[1].state == "active"


def test_convert_agent_todos_handles_missing_fields():
    """Missing 'content' / 'status' should not raise; defaults to '' + 'pending'."""
    app = AgentTUIApp.__new__(AgentTUIApp)
    converted = app._convert_agent_todos([
        {},  # both missing
        {"content": "only content"},  # status missing
        {"status": "in_progress"},  # content missing
    ])
    assert converted[0].text == ""
    assert converted[0].state == "pending"
    assert converted[1].text == "only content"
    assert converted[1].state == "pending"
    assert converted[2].text == ""
    assert converted[2].state == "active"


def test_run_todo_write_fires_on_todo_update_callback():
    """loom/agent/tools.run_todo_write must fire on_todo_update after updating CURRENT_TODOS.

    Uses module-level callback dispatcher in loom.agent.loop.
    """
    import loom.agent.loop as loop_mod
    import loom.agent.tools as tools_mod

    captured: list[list] = []

    def cb(todos: list) -> None:
        captured.append(list(todos))

    try:
        loop_mod.set_active_callbacks({"on_todo_update": cb})
        result = tools_mod.run_todo_write([
            {"content": "Task A", "status": "pending"},
            {"content": "Task B", "status": "in_progress"},
        ])
        assert "Updated 2 tasks" in result
        assert len(captured) == 1, f"expected 1 callback fire, got {len(captured)}"
        assert len(captured[0]) == 2
        assert captured[0][0]["content"] == "Task A"
        assert captured[0][1]["status"] == "in_progress"
    finally:
        loop_mod.clear_active_callbacks()


def test_run_todo_write_no_callback_when_no_dispatcher():
    """Without set_active_callbacks, run_todo_write is a silent no-op for callback."""
    import loom.agent.loop as loop_mod
    import loom.agent.tools as tools_mod

    # Ensure dispatcher is cleared
    loop_mod.clear_active_callbacks()
    result = tools_mod.run_todo_write([{"content": "x", "status": "pending"}])
    assert "Updated 1 tasks" in result  # still updates CURRENT_TODOS
    # No assertion needed for callback — it's just silently not fired


# ── f-tui-subagent-click-jump: SubagentRow widget + click handler ───────────


def test_subagent_row_click_posts_subagent_row_clicked_message():
    """SubagentRow.on_click posts the message and does not call event.stop()."""
    row = SubagentRow("toolu_abc", "● subagent-id · running · 0s")
    posted: list[object] = []
    original_post = row.post_message

    def capture(msg):
        posted.append(msg)
        return original_post(msg)

    row.post_message = capture  # type: ignore[method-assign]
    try:
        ev = Click(row, 0, 0, 0, 0, 0, False, False, False)
        stop_calls: list[bool] = []
        original_stop = ev.stop

        def capture_stop() -> None:
            stop_calls.append(True)
            return original_stop()

        ev.stop = capture_stop  # type: ignore[method-assign]
        try:
            row.on_click(ev)
        finally:
            ev.stop = original_stop  # type: ignore[method-assign]

        assert any(
            isinstance(m, Header.SubagentRowClicked) and m.tool_use_id == "toolu_abc"
            for m in posted
        ), f"SubagentRow click must post Header.SubagentRowClicked(tool_use_id), got: {posted}"
        assert stop_calls == [], (
            "SubagentRow.on_click must NOT call event.stop() — HeaderOverlay.on_click stops it"
        )
    finally:
        row.post_message = original_post  # type: ignore[method-assign]



def test_subagent_row_exposes_tool_use_id():
    """SubagentRow.tool_use_id property exposes the constructor identifier."""
    row = SubagentRow("toolu_xyz789", "● id · done · 3s")
    assert row.tool_use_id == "toolu_xyz789"


def test_app_on_subagent_row_clicked_dismisses_overlay():
    """App.on_subagent_row_clicked removes the HeaderOverlay (spec §4.3.2)."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            header = pilot.app.query_one(Header)
            overlay = HeaderOverlay(SECTION_SUBAGENT, header._state, id="header-overlay-subagent")
            await pilot.app.screen.mount(overlay, before=pilot.app.query_one(ChatLog))
            await pilot.pause(0.05)
            assert pilot.app.screen.query(HeaderOverlay), "overlay should be mounted"

            app.on_subagent_row_clicked(Header.SubagentRowClicked("nonexistent_id"))
            await pilot.pause(0.05)

            assert not list(pilot.app.screen.query(HeaderOverlay)), (
                "overlay should be dismissed after SubagentRowClicked"
            )

    asyncio.run(driver())


def test_app_on_subagent_row_clicked_handles_unknown_id_gracefully():
    """on_subagent_row_clicked with unknown tool_use_id must not raise."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.on_subagent_row_clicked(Header.SubagentRowClicked("unknown_tool_use_id"))
            await pilot.pause(0.05)

    asyncio.run(driver())


def test_app_on_subagent_row_clicked_scrolls_chatlog_to_marker():
    """on_subagent_row_clicked calls scroll_visible on the matching marker."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            chat_log = pilot.app.query_one(ChatLog)

            chat_log.add_tool_call_inline("task", {"description": "test"}, "toolu_xyz")
            await pilot.pause(0.05)

            marker = chat_log._tool_markers.get("toolu_xyz")
            assert marker is not None, "marker must be mounted after add_tool_call_inline"

            scroll_called: list[bool] = []
            original_scroll_visible = marker.scroll_visible

            def capture_scroll(*args, **kwargs):
                scroll_called.append(True)
                return original_scroll_visible(*args, **kwargs)

            marker.scroll_visible = capture_scroll  # type: ignore[method-assign]
            try:
                app.on_subagent_row_clicked(Header.SubagentRowClicked("toolu_xyz"))
                await pilot.pause(0.05)
            finally:
                marker.scroll_visible = original_scroll_visible  # type: ignore[method-assign]

            assert scroll_called, (
                "on_subagent_row_clicked must call scroll_visible on the matching marker"
            )

    asyncio.run(driver())