"""Tests for HeaderSectionButton 1Hz pulse behavior (P2b Task 3).

Covers:
- Test 1: update_pulse(True) adds 'pulsing' CSS class + starts Python timer
- Test 2: update_pulse(False) removes 'pulsing' class + resets opacity to 1.0
- Test 3: Implementation uses Python set_interval (not CSS animation/@keyframes)
- Test 4: Header.update_state toggles pulse per section based on count
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, patch

from loom.tui.app import AgentTUIApp
from loom.tui.header import (
    SECTION_MCP,
    Header,
    HeaderSectionButton,
    HeaderState,
    MCPServer,
)


def test_section_button_pulse_class_when_count_positive():
    """update_pulse(True) adds 'pulsing' CSS class (toggle, not auto-start)."""
    btn = HeaderSectionButton(SECTION_MCP)
    with patch.object(btn, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        btn.update_pulse(True)
    assert btn.has_class("pulsing"), "pulsing class must be added when has_count=True"
    # Verify set_interval was called with 0.5s + name='header-pulse'
    mock_set.assert_called_once()
    call_args = mock_set.call_args
    assert call_args[0][0] == 0.5, f"interval must be 0.5s (1Hz toggle), got {call_args[0][0]}"
    assert call_args[1].get("name") == "header-pulse", (
        f"interval must be named 'header-pulse', got {call_args[1]!r}"
    )


def test_section_button_no_pulse_when_count_zero():
    """update_pulse(False) removes 'pulsing' CSS class + sets opacity to 1.0."""
    btn = HeaderSectionButton(SECTION_MCP)
    with patch.object(btn, "set_interval") as mock_set:
        mock_set.return_value = MagicMock()
        # First turn it on
        btn.update_pulse(True)
        assert btn.has_class("pulsing")
        # Then turn it off
        btn.update_pulse(False)
    assert not btn.has_class("pulsing"), "pulsing class must be removed when has_count=False"
    # Opacity must be reset to 1.0 (idle freeze)
    assert btn.styles.opacity == 1.0, (
        f"opacity must be 1.0 (idle freeze), got {btn.styles.opacity}"
    )


def test_section_button_pulse_python_timer_1hz():
    """HeaderSectionButton source uses Python set_interval(0.5, name='header-pulse')
    (NOT CSS animation/keyframes).

    Textual CSS parser v8.2.7 does not support @keyframes or animation property
    with steps() timing function. Pulse is implemented in Python via
    set_interval(0.5, ...) that toggles self.styles.opacity between 1.0 and 0.5
    — same 1Hz square wave semantics as CSS steps(2, end).
    """
    source = inspect.getsource(HeaderSectionButton)
    # MUST contain the Python timer call
    assert "set_interval(0.5" in source, (
        "HeaderSectionButton source must contain set_interval(0.5, ..., name='header-pulse')"
    )
    assert 'name="header-pulse"' in source, (
        "HeaderSectionButton source must name the interval 'header-pulse'"
    )
    # Opacity toggle pattern
    assert "opacity" in source.lower(), "HeaderSectionButton source must toggle opacity"
    # MUST NOT contain CSS animation/keyframes (Textual doesn't support them)
    assert "@keyframes" not in source, (
        "HeaderSectionButton source must NOT contain @keyframes (Textual CSS doesn't support it)"
    )
    assert "animation:" not in source, (
        "HeaderSectionButton source must NOT contain animation: (Textual CSS doesn't support it)"
    )


def test_header_button_pulse_toggle_on_state_change():
    """Header.update_state toggles pulse class on each button based on section count.

    Cycle: empty (no pulse) → populated (pulse on) → empty (pulse off).
    """

    async def driver():
        app = AgentTUIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            header = app.query_one(Header)
            mcp_btn = app.query_one("#header-btn-mcp", HeaderSectionButton)

            # Initially app's _header_state has all MCPs from TOOL_REGISTRY
            # → pulsing=True (we patch set_interval to avoid actual timer)
            with patch.object(mcp_btn, "set_interval") as mock_set:
                mock_set.return_value = MagicMock()
                # State 1: empty → no pulse
                header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
                await pilot.pause(0.05)
            assert not mcp_btn.has_class("pulsing"), "empty state should not pulse"
            assert mcp_btn._pulse_timer is None, "timer should be cleared when empty"

            # State 2: 3 MCPs → pulse on
            with patch.object(mcp_btn, "set_interval") as mock_set:
                mock_set.return_value = MagicMock()
                header.update_state(
                    HeaderState(
                        mcps=[
                            MCPServer("a", "connected"),
                            MCPServer("b", "connected"),
                            MCPServer("c", "connected"),
                        ],
                        todos=[],
                        subagents=[],
                    )
                )
                await pilot.pause(0.05)
            assert mcp_btn.has_class("pulsing"), "3 MCPs should pulse"
            assert mcp_btn._pulse_timer is not None, "timer should be set when count > 0"

            # State 3: back to empty → pulse off
            header.update_state(HeaderState(mcps=[], todos=[], subagents=[]))
            await pilot.pause(0.05)
            assert not mcp_btn.has_class("pulsing"), "empty state should remove pulse"
            assert mcp_btn._pulse_timer is None, "timer should be cleared when count → 0"

    asyncio.run(driver())
