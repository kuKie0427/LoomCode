"""Tests for mcp_manager (Phase PM-1) — server lifecycle and registration.

Covers the manager behavior that wires MCP server discovery into
TOOL_REGISTRY:

  - start_discovery spawns one daemon thread per configured server (M8)
  - discovery failure logs a warning; the main process survives (no raise)
  - shutdown_all is idempotent (safe to call twice) and clears state
  - shutdown_all unregisters all mcp__<name>__* tools from TOOL_REGISTRY
  - shutdown_all without active servers is a no-op

All tests mock mcp_client.start to a no-op (FakeProcess or stub) so no
real subprocess is spawned. The thread coordination is the unit of
test, not the JSON-RPC framing — that lives in tests/test_mcp_client.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from loom.agent import mcp_manager as mm
from loom.agent.config import HarnessConfig, MCPConfig, MCPServerConfig
from loom.agent.mcp_client import MCPServer
from loom.agent.tools import TOOL_REGISTRY

# ── Shared fixture: clean manager state between tests ──────────────────────


@pytest.fixture(autouse=True)
def _isolate_manager_state():
    """Wipe mcp_manager module-level state + remove any leaked MCP tools.

    The manager holds _ACTIVE_SERVERS, _PER_SERVER_LOCKS, and
    _DISCOVERY_THREADS as module-level singletons. We clear them
    between tests so concurrent test runs don't leak processes or
    see each other's servers.
    """
    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
    mm._CONFIGURED_SERVER_NAMES.clear()
    mm._SERVER_ERRORS.clear()
    mm._DISCOVERY_STARTED = False
    # Sweep any stale mcp__* tools left by previous tests
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass
    yield
    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
    mm._CONFIGURED_SERVER_NAMES.clear()
    mm._SERVER_ERRORS.clear()
    mm._DISCOVERY_STARTED = False
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass


def _make_cfg(*specs: MCPServerConfig) -> HarnessConfig:
    """Build a HarnessConfig whose MCPConfig carries only `specs`."""
    base = HarnessConfig.from_defaults()
    return HarnessConfig.from_defaults().__class__(
        policy=base.policy,
        checkpoint=base.checkpoint,
        mcp=MCPConfig(servers=tuple(specs)),
    )


def _fake_start(server: MCPServer) -> None:
    """Stand-in for mcp_client.start: pretend handshake succeeded.

    Seeds a single fake tool so the manager has something to register.
    """
    server.tools = [
        {
            "name": "fake_tool",
            "description": f"fake tool from {server.name}",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# ── 14. start_discovery spawns one daemon thread per configured server ──────


def test_start_discovery_spawns_thread_per_server() -> None:
    """M8: one daemon thread per [mcp.servers.*] entry."""
    cfg = _make_cfg(
        MCPServerConfig(name="a", command="ignored"),
        MCPServerConfig(name="b", command="ignored"),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        # Two threads, named for each server
        assert len(mm._DISCOVERY_THREADS) == 2
        names = {t.name for t in mm._DISCOVERY_THREADS}
        assert "mcp-discovery-a" in names
        assert "mcp-discovery-b" in names
        # Wait briefly for them to finish
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
        assert "a" in mm._ACTIVE_SERVERS
        assert "b" in mm._ACTIVE_SERVERS
        assert "mcp__a__fake_tool" in TOOL_REGISTRY.names()
        assert "mcp__b__fake_tool" in TOOL_REGISTRY.names()


def test_start_discovery_with_no_servers_spawns_zero_threads() -> None:
    """Empty MCPConfig → zero threads, no state change."""
    cfg = HarnessConfig.from_defaults()
    assert cfg.mcp.servers == ()
    mm.start_discovery(cfg)
    assert mm._DISCOVERY_THREADS == []


# ── 15. discovery failure → warning, main process not crashed ──────────────


def test_discovery_failure_logs_warning_and_survives() -> None:
    """start() raising MCPError → log warning, server not in cache."""
    cfg = _make_cfg(MCPServerConfig(name="ghost", command="ignored"))
    with patch.object(mm, "mcp_start", side_effect=RuntimeError("boom")):
        # The whole call must not raise — background thread catches.
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
    assert "ghost" not in mm._ACTIVE_SERVERS
    assert "ghost" not in mm._PER_SERVER_LOCKS
    # No tool was registered
    assert "mcp__ghost__fake_tool" not in TOOL_REGISTRY.names()


def test_discovery_with_malformed_schema_skips_tool() -> None:
    """If mcp_tool_to_loom_tool returns None (M4), the tool is skipped."""
    cfg = _make_cfg(MCPServerConfig(name="bad", command="ignored"))

    def _fake_start_bad(server: MCPServer) -> None:
        server.tools = [
            {
                "name": "broken",
                "description": "bad schema",
                "inputSchema": {"type": "string"},  # not object
            },
            {
                "name": "good",
                "description": "good schema",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    with patch.object(mm, "mcp_start", side_effect=_fake_start_bad):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
    assert "mcp__bad__broken" not in TOOL_REGISTRY.names()
    assert "mcp__bad__good" in TOOL_REGISTRY.names()


# ── 16. shutdown_all unregisters tools, is idempotent ───────────────────────


def test_shutdown_all_unregisters_mcp_tools() -> None:
    """After shutdown_all, TOOL_REGISTRY has no mcp__* entries."""
    cfg = _make_cfg(MCPServerConfig(name="x", command="ignored"))
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
    assert "mcp__x__fake_tool" in TOOL_REGISTRY.names()
    mm.shutdown_all()
    assert "mcp__x__fake_tool" not in TOOL_REGISTRY.names()
    assert "x" not in mm._ACTIVE_SERVERS
    assert "x" not in mm._PER_SERVER_LOCKS


def test_shutdown_all_is_idempotent() -> None:
    """Calling shutdown_all twice in a row does not raise."""
    mm.shutdown_all()
    mm.shutdown_all()  # must not raise


def test_shutdown_all_with_no_active_servers_is_noop() -> None:
    """shutdown_all on empty state is a no-op (no error)."""
    assert mm._ACTIVE_SERVERS == {}
    mm.shutdown_all()
    assert mm._ACTIVE_SERVERS == {}


def test_shutdown_all_continues_on_individual_stop_failure() -> None:
    """If one server's stop() raises, the others are still cleaned up."""
    cfg = _make_cfg(
        MCPServerConfig(name="good", command="ignored"),
        MCPServerConfig(name="bad", command="ignored"),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    real_stop = mm.mcp_stop
    def _stop_with_one_fail(server: MCPServer):
        if server.name == "bad":
            raise RuntimeError("simulated stop failure")
        return real_stop(server)

    with patch.object(mm, "mcp_stop", side_effect=_stop_with_one_fail):
        mm.shutdown_all()  # must not raise
    assert "good" not in mm._ACTIVE_SERVERS
    assert "bad" not in mm._ACTIVE_SERVERS
    assert "mcp__good__fake_tool" not in TOOL_REGISTRY.names()
    assert "mcp__bad__fake_tool" not in TOOL_REGISTRY.names()


# ── get_server_snapshot ───────────────────────────────────────────────────


def test_get_server_snapshot_empty_when_nothing_configured() -> None:
    assert mm.get_server_snapshot() == []


def test_get_server_snapshot_shows_configured_but_not_connected_as_error() -> None:
    mm._CONFIGURED_SERVER_NAMES.add("gh")
    snapshot = mm.get_server_snapshot()
    assert snapshot == [{"name": "gh", "state": "error", "error": ""}]


def test_get_server_snapshot_shows_active_servers_as_connected() -> None:
    mm._CONFIGURED_SERVER_NAMES.update({"gh", "fs"})
    server = MCPServer(name="fs", command="echo")
    mm._ACTIVE_SERVERS["fs"] = server
    snapshot = mm.get_server_snapshot()
    assert {"name": "fs", "state": "connected", "error": ""} in snapshot
    assert {"name": "gh", "state": "error", "error": ""} in snapshot
    assert len(snapshot) == 2


def test_get_server_snapshot_includes_orphan_active_servers() -> None:
    """A server in _ACTIVE_SERVERS but not in _CONFIGURED_SERVER_NAMES
    (e.g. injected directly by a test) still appears as 'connected'.
    """
    server = MCPServer(name="orphan", command="echo")
    mm._ACTIVE_SERVERS["orphan"] = server
    snapshot = mm.get_server_snapshot()
    assert snapshot == [{"name": "orphan", "state": "connected", "error": ""}]
