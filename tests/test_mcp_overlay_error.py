"""Tests for P1-3: MCP overlay displays error details.

When an MCP server fails discovery or is evicted at runtime, the TUI Header
overlay must show the last recorded error message so the user knows why the
server is down.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from loom.tui.app import AgentTUIApp
from loom.tui.header import SECTION_MCP, HeaderOverlay, HeaderState, MCPServer


def test_mcp_server_dataclass_carries_error():
    """MCPServer accepts an optional error detail field."""
    server = MCPServer(name="fs", state="error", error="handshake timeout")
    assert server.error == "handshake timeout"


def test_header_overlay_mcp_shows_error_detail():
    """The MCP overlay yields an extra detail row for error-state servers."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 24)) as pilot:
            state = HeaderState(
                mcps=[
                    MCPServer(name="db", state="connected"),
                    MCPServer(name="fs", state="error", error="connection refused"),
                ]
            )
            overlay = HeaderOverlay(SECTION_MCP, state)
            pilot.app.mount(overlay)
            await pilot.pause(0.05)
            texts = [str(w.render()) for w in pilot.app.query("HeaderOverlay Static")]
            assert any("connection refused" in t for t in texts)

    asyncio.run(driver())


def test_header_overlay_mcp_truncates_long_error():
    """Very long error messages are truncated to keep the overlay compact."""

    async def driver():
        app = AgentTUIApp()
        async with app.run_test(size=(80, 24)) as pilot:
            long_error = "x" * 300
            state = HeaderState(
                mcps=[MCPServer(name="fs", state="error", error=long_error)]
            )
            overlay = HeaderOverlay(SECTION_MCP, state)
            pilot.app.mount(overlay)
            await pilot.pause(0.05)
            texts = [str(w.render()) for w in pilot.app.query("HeaderOverlay Static")]
            detail = next(t for t in texts if "x" * 10 in t)
            assert len(detail) <= 220  # 200 chars + prefix + ellipsis
            assert "…" in detail

    asyncio.run(driver())


def test_mcp_manager_snapshot_includes_error():
    """get_server_snapshot returns the last recorded error per server."""
    from loom.agent import mcp_manager as mm

    mm._ACTIVE_SERVERS.clear()
    mm._CONFIGURED_SERVER_NAMES.clear()
    mm._SERVER_ERRORS.clear()
    mm._CONFIGURED_SERVER_NAMES.add("bad-server")
    mm._SERVER_ERRORS["bad-server"] = "exec not found"

    snapshot = mm.get_server_snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["name"] == "bad-server"
    assert snapshot[0]["state"] == "error"
    assert snapshot[0]["error"] == "exec not found"


def test_mcp_manager_clears_error_on_success():
    """A successful discovery removes any prior error for that server."""
    from loom.agent import mcp_manager as mm
    from loom.agent.config import HarnessConfig, MCPConfig, MCPServerConfig
    from loom.agent.mcp_client import MCPServer as MCPClientServer

    mm._ACTIVE_SERVERS.clear()
    mm._CONFIGURED_SERVER_NAMES.clear()
    mm._SERVER_ERRORS.clear()
    mm._DISCOVERY_STARTED = False

    mm._SERVER_ERRORS["good-server"] = "stale error"

    cfg = HarnessConfig.from_defaults().__class__(
        policy=HarnessConfig.from_defaults().policy,
        checkpoint=HarnessConfig.from_defaults().checkpoint,
        mcp=MCPConfig(
            servers=(MCPServerConfig(name="good-server", command="ignored"),)
        ),
    )

    def fake_start(server: MCPClientServer) -> None:
        server.tools = [
            {
                "name": "fake_tool",
                "description": "fake",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    with patch.object(mm, "mcp_start", side_effect=fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    assert "good-server" in mm._ACTIVE_SERVERS
    assert "good-server" not in mm._SERVER_ERRORS


def test_app_build_initial_header_state_passes_error():
    """AgentTUIApp propagates the error field from snapshot to MCPServer."""
    from loom.agent import mcp_manager as mm

    async def driver():
        mm._ACTIVE_SERVERS.clear()
        mm._CONFIGURED_SERVER_NAMES.clear()
        mm._SERVER_ERRORS.clear()
        mm._CONFIGURED_SERVER_NAMES.add("bad")
        mm._SERVER_ERRORS["bad"] = "permission denied"

        app = AgentTUIApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.05)
            server = next(s for s in app._header_state.mcps if s.name == "bad")
            assert server.state == "error"
            assert server.error == "permission denied"

    asyncio.run(driver())
