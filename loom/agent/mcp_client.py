"""Minimal MCP client (stdio transport, JSON-RPC 2.0).

Implements just enough of the Model Context Protocol spec to spawn
one reference server, initialize the protocol handshake, list
available tools, and invoke them.

Per the Phase 3 minimal scope decision: SSE transport deferred,
multi-server support deferred, auth/pagination deferred. The goal
is to register at least one external tool (e.g. filesystem-mcp's
read_file) into loom's TOOL_REGISTRY so the agent can use it.

Spec reference: https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class MCPServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    process: subprocess.Popen | None = None
    tools: list[dict] = field(default_factory=list)
    request_id: int = 0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id


class MCPError(RuntimeError):
    pass


def _read_message(proc: subprocess.Popen, timeout: float = 30.0) -> dict:
    """Read one JSON-RPC message from the server's stdout.

    Messages are framed as `Content-Length: N\r\n\r\n<json body>`.
    """
    deadline = time.monotonic() + timeout
    header = b""
    while time.monotonic() < deadline:
        if proc.stdout is None:
            raise MCPError("server stdout closed")
        chunk = proc.stdout.read(1)
        if not chunk:
            raise MCPError("server stdout EOF while reading headers")
        header += chunk
        if header.endswith(b"\r\n\r\n"):
            break
    else:
        raise MCPError(f"timeout reading message header (got {header!r})")
    lines = header.decode("ascii").split("\r\n")
    content_length = None
    for line in lines:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    if content_length is None:
        raise MCPError(f"no Content-Length in header: {header!r}")
    body = b""
    while len(body) < content_length:
        if proc.stdout is None:
            raise MCPError("server stdout closed while reading body")
        chunk = proc.stdout.read(content_length - len(body))
        if not chunk:
            raise MCPError("server stdout EOF while reading body")
        body += chunk
    return json.loads(body.decode("utf-8"))


def _send_message(proc: subprocess.Popen, message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    if proc.stdin is None:
        raise MCPError("server stdin closed")
    proc.stdin.write(header + body)
    proc.stdin.flush()


def start(server: MCPServer) -> None:
    """Spawn the server process and complete the initialize handshake.

    If server.process is already set (e.g. injected by a test or by
    a future connection-pooling layer), skip Popen and just do the
    handshake. Otherwise spawn via Popen.
    """
    if server.process is None:
        cmd = [server.command] + list(server.args)
        env = {**__import__("os").environ, **server.env}
        server.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
    init_request = {
        "jsonrpc": "2.0", "id": server._next_id(),
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "loom-mcp-client", "version": "0.1.0"},
        },
    }
    _send_message(server.process, init_request)
    response = _read_message(server.process, timeout=30)
    if "error" in response:
        raise MCPError(f"initialize failed: {response['error']}")

    initialized = {
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    }
    _send_message(server.process, initialized)

    list_request = {
        "jsonrpc": "2.0", "id": server._next_id(),
        "method": "tools/list", "params": {},
    }
    _send_message(server.process, list_request)
    list_response = _read_message(server.process, timeout=30)
    if "error" in list_response:
        raise MCPError(f"tools/list failed: {list_response['error']}")
    server.tools = list_response.get("result", {}).get("tools", [])


def stop(server: MCPServer) -> None:
    if server.process is not None:
        server.process.terminate()
        try:
            server.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.process.kill()
        server.process = None


def call_tool(server: MCPServer, tool_name: str, arguments: dict, timeout: float = 30.0) -> Any:
    """Invoke a tool on the server and return its result."""
    if server.process is None:
        raise MCPError(f"server {server.name!r} not started")
    request = {
        "jsonrpc": "2.0", "id": server._next_id(),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    _send_message(server.process, request)
    response = _read_message(server.process, timeout=timeout)
    if "error" in response:
        raise MCPError(f"tool {tool_name!r} failed: {response['error']}")
    return response.get("result", {}).get("content", [])


def mcp_tool_to_loom_tool(server: MCPServer, mcp_tool: dict) -> dict:
    """Convert an MCP tool descriptor to a loom tool schema."""
    return {
        "name": f"mcp_{server.name}_{mcp_tool.get('name', '?')}",
        "description": f"[MCP:{server.name}] {mcp_tool.get('description', '')}",
        "input_schema": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
    }
