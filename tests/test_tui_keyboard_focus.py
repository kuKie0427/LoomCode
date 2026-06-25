"""Tests for keyboard focus and activation in the TUI.

After removing the composer focus lock, interactive widgets (Header buttons,
tool markers, thinking markers, subagent rows) must be reachable via Tab and
activatable via Enter/Space.
"""

from __future__ import annotations

import asyncio

from textual.events import Key

from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import ChatLog, ThinkingMarker, ToolCallMarker
from loom.tui.header import (
    SECTION_MCP,
    SECTION_SUBAGENT,
    Header,
    HeaderSectionButton,
    HeaderState,
    MCPServer,
    Subagent,
    SubagentRow,
)


def _capture_posted(widget):
    """Return (messages, restore) so tests can inspect posted messages."""
    posted = []
    original = widget.post_message

    def capture(msg):
        posted.append(msg)
        return original(msg)

    widget.post_message = capture  # type: ignore[method-assign]
    return posted, lambda: setattr(widget, "post_message", original)  # type: ignore


def test_header_section_button_key_posts_toggle():
    """Enter/Space on a focused HeaderSectionButton posts SectionToggle."""
    btn = HeaderSectionButton(SECTION_MCP)
    for key in ("enter", "space"):
        posted, restore = _capture_posted(btn)
        try:
            ev = Key(key, " " if key == "space" else None)
            btn.on_key(ev)
        finally:
            restore()
        assert any(
            isinstance(m, Header.SectionToggle) and m.section == SECTION_MCP
            for m in posted
        ), f"{key} on HeaderSectionButton should post SectionToggle"


def test_header_section_button_key_ignored_when_hidden():
    """A hidden section button ignores Enter/Space activation."""
    btn = HeaderSectionButton(SECTION_MCP)
    btn.add_class("section-hidden")
    posted, restore = _capture_posted(btn)
    try:
        btn.on_key(Key("enter", None))
    finally:
        restore()
    assert not any(isinstance(m, Header.SectionToggle) for m in posted)


def test_tab_focuses_header_button_in_app():
    """Tab from Composer cycles focus to the first HeaderSectionButton."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(
                HeaderState(mcps=[MCPServer(name="db", state="connected")])
            )
            await pilot.pause(0.05)

            composer = app.query_one("#composer")
            composer.focus()
            await pilot.pause(0.05)
            assert app.focused is composer

            await pilot.press("tab")
            await pilot.pause(0.05)
            focused = app.focused
            assert isinstance(focused, HeaderSectionButton), (
                f"Tab from composer should focus a header button, got {focused!r}"
            )
            assert focused.section == SECTION_MCP

    asyncio.run(driver())


def test_subagent_row_key_posts_clicked():
    """Enter/Space on a SubagentRow posts SubagentRowClicked(tool_use_id)."""
    row = SubagentRow("toolu_abc", "● id · running · 3s")
    for key in ("enter", "space"):
        posted, restore = _capture_posted(row)
        try:
            row.on_key(Key(key, " " if key == "space" else None))
        finally:
            restore()
        assert any(
            isinstance(m, Header.SubagentRowClicked) and m.tool_use_id == "toolu_abc"
            for m in posted
        ), f"{key} on SubagentRow should post SubagentRowClicked"


def test_tool_marker_key_toggles_output():
    """Enter/Space on a ToolCallMarker toggles its linked CollapsibleToolOutput."""
    from loom.tui.chat_log import CollapsibleToolOutput

    marker = ToolCallMarker("bash", "ls", tool_input={"command": "ls"})
    output = CollapsibleToolOutput("file1\nfile2\n")
    marker.set_output_widget(output)
    assert not output.display

    marker.on_key(Key("enter", None))
    assert output.display, "Enter should expand tool output"

    marker.on_key(Key("space", " "))
    assert not output.display, "Space should collapse tool output"


def test_thinking_marker_key_toggles_display():
    """Enter/Space on a ThinkingMarker calls the toggle callback."""
    toggled = []

    def on_toggle(display):
        toggled.append(display)

    marker = ThinkingMarker(on_toggle)
    marker._display = object()  # dummy display so _handle_toggle calls on_toggle
    marker.on_key(Key("enter", None))
    assert len(toggled) == 1, "Enter should trigger thinking toggle callback"


async def _open_header_section(app: AgentTUIApp, pilot, section: str) -> None:
    """Focus the requested header section button and press Enter to open it."""
    app.query_one("#composer").focus()
    await pilot.pause(0.05)
    for _ in range(10):
        focused = app.focused
        if isinstance(focused, HeaderSectionButton) and focused.section == section:
            break
        await pilot.press("tab")
        await pilot.pause(0.05)
    else:
        raise AssertionError(f"Could not focus header button for {section}")
    await pilot.press("enter")
    await pilot.pause(0.1)


def test_subagent_row_focus_and_enter_dismisses_overlay():
    """Tab to a SubagentRow inside the overlay and press Enter to dismiss."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            header.update_state(
                HeaderState(
                    subagents=[Subagent(id="toolu_abc", state="running", elapsed="3s")]
                )
            )
            await pilot.pause(0.05)

            await _open_header_section(app, pilot, SECTION_SUBAGENT)
            assert app.query_one(f"#header-overlay-{SECTION_SUBAGENT}")

            # Focus the SubagentRow inside the overlay and press Enter.
            row = app.query_one(SubagentRow)
            assert row.tool_use_id == "toolu_abc"
            row.focus()
            await pilot.pause(0.05)
            assert app.focused is row

            # Press Enter on the focused SubagentRow; the App handler should
            # dismiss the overlay and scroll to the subagent marker.
            await pilot.press("enter")
            await pilot.pause(0.1)
            overlay_still_present = False
            try:
                app.query_one(f"#header-overlay-{SECTION_SUBAGENT}")
                overlay_still_present = True
            except Exception:
                pass
            assert not overlay_still_present, (
                "Enter on SubagentRow should dismiss the overlay"
            )

    asyncio.run(driver())


def test_tool_marker_focus_and_enter_toggles_output():
    """Tab reaches a ToolCallMarker and Enter expands its output."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            chat_log = app.query_one(ChatLog)
            chat_log.add_tool_call_inline("bash", {"command": "ls"}, "tool_1")
            chat_log._tool_markers["tool_1"].set_complete(
                "file1\nfile2\n", is_error=False
            )
            await pilot.pause(0.1)

            app.query_one("#composer").focus()
            await pilot.pause(0.05)
            for _ in range(10):
                await pilot.press("tab")
                await pilot.pause(0.05)
                focused = app.focused
                if isinstance(focused, ToolCallMarker):
                    break
            else:
                raise AssertionError("Could not focus ToolCallMarker")

            output_widget = chat_log._tool_outputs["tool_1"]
            assert not output_widget.display
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert output_widget.display, "Enter should expand tool output"

    asyncio.run(driver())


def test_thinking_marker_focus_and_enter_toggles_display():
    """Tab reaches a ThinkingMarker and Enter toggles its display."""
    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause(0.05)
            chat_log = app.query_one(ChatLog)
            chat_log.append_thinking_text("step one")
            await pilot.pause(0.1)

            app.query_one("#composer").focus()
            await pilot.pause(0.05)
            for _ in range(10):
                await pilot.press("tab")
                await pilot.pause(0.05)
                focused = app.focused
                if isinstance(focused, ThinkingMarker):
                    break
            else:
                raise AssertionError("Could not focus ThinkingMarker")

            display = chat_log._thinking_display
            assert display is not None
            before = display.display
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert display.display is not before, (
                "Enter should toggle thinking display visibility"
            )

    asyncio.run(driver())
