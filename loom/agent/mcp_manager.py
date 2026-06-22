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

PM-2 added the 3-state permission gate inside ``_make_mcp_handler`` (M5).
PM-3 added four output-shape mitigations: flatten the ``content`` list
returned by ``mcp_client.call_tool`` into a string (M12), truncate the
result at 50KB with a footer + trace event (M7), emit a visible
``logger.warning`` and evict the server on call failure (M6), and record
an ``mcp_request`` trace event before the per-server lock (R5). PM-4
will gate subagent access via ``spec.subagent_access``.
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

    PM-2 wraps this in the 3-state permission gate. PM-3 applies four
    output-shape mitigations before returning the result:

    - **R5**: emits an ``mcp_request`` trace event BEFORE acquiring the
      per-server lock, so trace records the intent even if the lock
      contention blocks for a long time.
    - **M6**: catches every exception from ``call_tool``, emits a visible
      ``logger.warning``, evicts the server from ``_ACTIVE_SERVERS`` /
      ``_PER_SERVER_LOCKS``, and unregisters every ``mcp__<server>__*``
      tool from ``TOOL_REGISTRY`` so the agent does not hang on a dead
      process. Returns a string error instead of raising — the agent
      loop catches tool errors as text, not exceptions.
    - **M12**: flattens the ``content`` list (text/image/resource blocks)
      returned by ``mcp_client.call_tool`` into a single string via
      ``_flatten_mcp_content``. Image and resource blocks become
      bracketed placeholders so a 200KB image does not blow up context.
    - **M7**: caps the flattened text at ``MAX_MCP_OUTPUT_CHARS`` (50KB)
      and appends a footer with the overflow count + emits an
      ``mcp_output_truncated`` trace event.
    """
    from loom.agent import trace as trace_mod  # late import: avoids cycles

    def _handler(**kwargs: Any) -> str:
        # R5: trace BEFORE the per-server lock — if a sibling call holds
        # the lock and the request times out, we still see the intent.
        tr = trace_mod.current()
        if tr is not None:
            try:
                tr.record("mcp_request", server=server_name, tool=tool_name)
            except Exception:
                pass

        with _LOCK:
            server = _ACTIVE_SERVERS.get(server_name)
        if server is None:
            return f"MCP server '{server_name}' not connected"
        per_server_lock = _PER_SERVER_LOCKS.get(server_name)
        if per_server_lock is None:
            return f"MCP server '{server_name}' lock missing (evicted?)"
        with per_server_lock:
            from loom.agent import mcp_client  # late import: avoids cycles
            from loom.agent import tools as tools_mod  # late import: registry
            try:
                content = mcp_client.call_tool(server, tool_name, kwargs)
            except Exception as exc:
                # M6: visible crash recovery — log warning, evict from
                # cache, unregister all mcp__<server>__* tools so the
                # next tool_use block fails fast with "Unknown tool"
                # instead of hanging on a dead stdio pipe.
                logger.warning(
                    "MCP server '%s' call failed (tool='%s'), evicted. "
                    "Tools from this server unavailable until restart. Error: %s",
                    server_name, tool_name, exc,
                )
                with _LOCK:
                    _ACTIVE_SERVERS.pop(server_name, None)
                    _PER_SERVER_LOCKS.pop(server_name, None)
                    for n in list(tools_mod.TOOL_REGISTRY.names()):
                        if n.startswith(f"mcp__{server_name}__"):
                            try:
                                tools_mod.TOOL_REGISTRY.unregister(n)
                            except Exception:
                                pass
                try:
                    tools_mod._resync_from_registry()
                except Exception:
                    pass
                return f"MCP error: {exc} (server evicted; restart on next session)"

        # M12: flatten content list to a string.
        text = _flatten_mcp_content(content)

        # M7: cap oversized output so a runaway tool cannot blow up context.
        if len(text) > MAX_MCP_OUTPUT_CHARS:
            truncated = text[:MAX_MCP_OUTPUT_CHARS]
            overflow = len(text) - MAX_MCP_OUTPUT_CHARS
            text = truncated + f"\n... (truncated, {overflow} more characters)"
            if tr is not None:
                try:
                    tr.record(
                        "mcp_output_truncated",
                        server=server_name,
                        tool=tool_name,
                        original_len=len(text),
                        capped_len=MAX_MCP_OUTPUT_CHARS,
                    )
                except Exception:
                    pass

        return text

    return _handler


# M7: maximum chars we will return from a single MCP tool call. Beyond
# this, we truncate with a footer so a misbehaving server (e.g. dumping a
# 200MB log file) cannot blow up the agent's context window.
MAX_MCP_OUTPUT_CHARS = 50000


def _flatten_mcp_content(content: Any) -> str:
    """Convert an MCP tools/call ``content`` payload to a single string.

    Per the MCP spec, ``tools/call`` returns ``{content: [...]}`` where the
    list may contain text blocks (``{type: "text", text: "..."}``), image
    blocks (``{type: "image", data: "...", mimeType: "..."}``), and
    resource blocks (``{type: "resource", resource: {...}}``).

    Text blocks are joined with newlines. Image and resource blocks are
    replaced with bracketed placeholders so a 200KB inline image does not
    blow up the agent's context. Unknown block types get a placeholder
    naming their type. String content is returned as-is (some servers
    short-circuit and return a plain string instead of a list).

    M12 fix — pre-PM-3 we did ``json.dumps(content)`` which returned a
    Python-list literal that the LLM could not parse reliably.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        try:
            return json.dumps(content)
        except (TypeError, ValueError):
            return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "image":
            mime = block.get("mimeType", "image/unknown")
            parts.append(f"[MCP: {mime} image content omitted]")
        elif btype == "resource":
            res = block.get("resource", {})
            uri = res.get("uri", "?") if isinstance(res, dict) else "?"
            parts.append(f"[MCP: resource {uri}]")
        else:
            parts.append(f"[MCP: unknown content type '{btype}']")
    return "\n".join(parts)


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
