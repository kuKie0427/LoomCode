"""PM-1: mcp_manager — MCP server lifecycle (discovery / registration / shutdown).

Pattern mirrors lsp_manager (PL-2). One daemon thread per configured server
runs the JSON-RPC handshake + tools/list in the background. Discovered tools
are registered into the global TOOL_REGISTRY so the agent loop sees them on
its next LLM call (the lazy M14 accessor resolves the current state).

Thread safety:
- ``_LOCK`` guards the ``_ACTIVE_SERVERS`` / ``_PER_SERVER_LOCKS`` dicts for
  check-then-mutate during discovery and during eviction on call failure.
- ``_PER_SERVER_LOCKS[name]`` serialises JSON-RPC reads/writes against the
  same stdio pair, so concurrent tool calls to one server cannot interleave.
- TOOL_REGISTRY.register() / unregister() take the registry's own lock (M15).

Fail-closed contract:
- No [mcp.servers.*] config → ``start_discovery`` spawns zero threads; the
  agent runs with the native tool set unchanged.
- Handshake / runtime error → logged as a warning, server NOT added to
  ``_ACTIVE_SERVERS``; agent loop continues with the tools that did register.
- ``mcp_tool_to_loom_tool`` returns None for malformed inputSchema (M4) →
  logged as a warning, tool skipped.
- TOOL_REGISTRY.register() raises ValueError on duplicate name (e.g. native
  tool collision) → logged as a warning, tool skipped.
- Handler call failure (M6) → server evicted from cache, tools unregistered,
  handler returns ``"MCP error: ... (server evicted; restart on next session)"``.

PM-2 will add the permission gate inside ``_make_mcp_handler`` (M5). PM-3
will flatten the ``content`` list returned by ``mcp_client.call_tool`` and
apply 50KB truncation (M6/M7/M12/M16). PM-4 will gate subagent access via
``spec.subagent_access``.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from loguru import logger

from loom.agent.config import HarnessConfig, MCPServerConfig
from loom.agent.mcp_client import (
    MCPServer,
    mcp_tool_to_loom_tool,
    start as mcp_start,
    stop as mcp_stop,
)
from loom.agent.tool_registry import Tool

_LOCK = threading.Lock()
_ACTIVE_SERVERS: dict[str, MCPServer] = {}
_PER_SERVER_LOCKS: dict[str, threading.Lock] = {}
_DISCOVERY_THREADS: list[threading.Thread] = []


def start_discovery(config: HarnessConfig) -> None:
    """SessionStart hook: spawn one daemon thread per configured server.

    Each thread runs ``_discover_server`` which calls ``mcp_client.start``
    (handshake + tools/list) and registers every discovered tool into
    ``TOOL_REGISTRY``. Non-blocking: the agent loop starts immediately, and
    tools appear in the live ``get_tools()`` / ``get_tool_handlers()``
    resolution as each server responds.
    """
    for spec in config.mcp.servers:
        t = threading.Thread(
            target=_discover_server,
            args=(spec,),
            daemon=True,
            name=f"mcp-discovery-{spec.name}",
        )
        t.start()
        _DISCOVERY_THREADS.append(t)


def _discover_server(spec: MCPServerConfig) -> None:
    """Run start() for one server, then register all its tools.

    Catches every exception so a background thread can never crash the main
    process. On any failure, logs a warning and returns; the server is not
    cached, no tools are registered.
    """
    try:
        server = MCPServer(
            name=spec.name,
            command=spec.command,
            args=list(spec.args),
            env=dict(spec.env),
            cwd=spec.cwd,
        )
        mcp_start(server)  # raises MCPError on handshake fail
    except Exception as exc:
        logger.warning("MCP server '%s' discovery failed: %s", spec.name, exc)
        return

    with _LOCK:
        _ACTIVE_SERVERS[spec.name] = server
        _PER_SERVER_LOCKS[spec.name] = threading.Lock()

    # Register every tool the server reported. Each registration is
    # independent: a malformed-schema tool or a duplicate-name tool is
    # logged + skipped, the rest still register.
    from loom.agent import tools as tools_mod  # late import to avoid cycles
    tool_registry = tools_mod.TOOL_REGISTRY

    for mcp_tool in server.tools:
        loom_tool_dict = mcp_tool_to_loom_tool(server, mcp_tool)
        if loom_tool_dict is None:  # M4: malformed inputSchema
            logger.warning(
                "MCP tool '%s' from '%s' has malformed inputSchema, skipping",
                mcp_tool.get("name", "?"),
                spec.name,
            )
            continue
        try:
            handler = _make_mcp_handler(spec.name, mcp_tool["name"])
        except Exception as exc:
            logger.warning(
                "MCP tool '%s' from '%s' handler build failed: %s",
                mcp_tool.get("name", "?"), spec.name, exc,
            )
            continue
        try:
            tool_registry.register(Tool(
                name=loom_tool_dict["name"],
                description=loom_tool_dict["description"],
                input_schema=loom_tool_dict["input_schema"],
                handler=handler,
                is_read_only=False,  # PM-2 may refine per-server
                is_concurrent_safe=False,
                enabled=True,
            ))
        except ValueError:
            logger.warning(
                "MCP tool '%s' from '%s' duplicate name, skipping",
                mcp_tool.get("name", "?"), spec.name,
            )
    # Keep the live TOOLS / TOOL_HANDLERS aliases in sync (M14).
    try:
        tools_mod._resync_from_registry()
    except Exception as exc:
        logger.warning("MCP _resync_from_registry failed: %s", exc)


def _make_mcp_handler(server_name: str, tool_name: str):
    """Return a closure that routes tool calls to ``mcp_client.call_tool``.

    Captures ``server_name`` and ``tool_name`` via default-argument binding
    (NOT via a loop variable — there is no loop here, but the pattern is
    kept explicit for safety if the caller is ever refactored).

    PM-2 will wrap this in a permission gate. PM-3 will replace the
    ``json.dumps(result)`` with flattened ``content`` + 50KB truncation.
    """
    def _handler(**kwargs: Any) -> str:
        with _LOCK:
            server = _ACTIVE_SERVERS.get(server_name)
        if server is None:
            return f"MCP server '{server_name}' not connected"
        per_server_lock = _PER_SERVER_LOCKS.get(server_name)
        if per_server_lock is None:
            return f"MCP server '{server_name}' lock missing (evicted?)"
        with per_server_lock:
            from loom.agent import mcp_client  # late import: avoids cycles
            try:
                result = mcp_client.call_tool(server, tool_name, kwargs)
            except Exception as exc:
                # M6: evict the server so the next call (or a future
                # discovery round) can re-register cleanly. Lock order:
                # release per-server lock first, then acquire _LOCK for
                # eviction. Per-server lock is released on `with` exit.
                logger.warning(
                    "MCP server '%s' call failed, evicting: %s",
                    server_name, exc,
                )
                with _LOCK:
                    _ACTIVE_SERVERS.pop(server_name, None)
                    _PER_SERVER_LOCKS.pop(server_name, None)
                # Unregister the tools so subsequent tool_use blocks fail
                # cleanly with "Unknown tool" rather than hang on a dead
                # process.
                for n in list(tool_registry.names()):
                    if n.startswith(f"mcp__{server_name}__"):
                        try:
                            tool_registry.unregister(n)
                        except Exception:
                            pass
                try:
                    from loom.agent import tools as tools_mod
                    tools_mod._resync_from_registry()
                except Exception:
                    pass
                return f"MCP error: {exc} (server evicted; restart on next session)"
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result)
        except (TypeError, ValueError):
            return str(result)


def get_server_lock(name: str) -> threading.Lock:
    """Return the per-server lock for serialising JSON-RPC against one subprocess."""
    return _PER_SERVER_LOCKS[name]


def shutdown_all() -> None:
    """SessionEnd hook: stop all started servers, unregister their tools.

    Idempotent. Individual ``stop`` failures are logged but do not stop
    the loop — we still want to evict the rest of the cache and tear down
    the per-server locks. Tool unregistration ensures stale MCP tools do
    not linger into a future session that re-runs start_discovery with
    a different tool set.
    """
    from loom.agent import tools as tools_mod  # late import
    tool_registry = tools_mod.TOOL_REGISTRY
    with _LOCK:
        names = list(_ACTIVE_SERVERS.keys())
        for name in names:
            server = _ACTIVE_SERVERS.pop(name, None)
            if server is not None:
                try:
                    mcp_stop(server)
                except Exception:
                    logger.warning("MCP server '%s' shutdown failed", name)
            for tool_name in list(tool_registry.names()):
                if tool_name.startswith(f"mcp__{name}__"):
                    try:
                        tool_registry.unregister(tool_name)
                    except Exception:
                        pass
        _PER_SERVER_LOCKS.clear()
    # Refresh the live aliases after bulk unregistration.
    try:
        tools_mod._resync_from_registry()
    except Exception:
        pass
