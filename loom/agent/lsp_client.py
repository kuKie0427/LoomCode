"""Minimal LSP client for f-lsp-integration-p3.

Scope (deliberately small):
- JSON-RPC over stdin/stdout (Content-Length framed, like MCP)
- start(): spawn process, send initialize, wait for response, send initialized
- goto_definition(file, line, col): returns list of Location dicts
- shutdown() + exit(): clean process termination

NOT in scope (deferred): find_references / rename_symbol / hover /
completion / multi-server / workspace edits / did_change tracking.

Why not depend on pylsp? pylsp requires installing the python-lsp-server
package + its many plugins. We don't need the server — we need the
client. The client speaks JSON-RPC to whatever LSP-compatible server
the user wants (pylsp, typescript-language-server, gopls, etc.).
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_logger = logging.getLogger(__name__)


class _ProcessLike(Protocol):
    stdin: Any
    stdout: Any
    stderr: Any
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


@dataclass
class LSPServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    process: _ProcessLike | None = None
    request_id: int = 0
    capabilities: dict = field(default_factory=dict)


class LSPError(RuntimeError):
    """Raised when the LSP server returns an error response."""


def _next_id(server: LSPServer) -> int:
    server.request_id += 1
    return server.request_id


def _send(proc: _ProcessLike, message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header + body)
    try:
        proc.stdin.flush()
    except (AttributeError, OSError):
        pass


def _read_headers(proc: _ProcessLike) -> dict[str, str]:
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        if not line:
            raise EOFError("LSP server closed stream before headers")
        line = line.decode("ascii", errors="replace").strip()
        if not line:
            return headers
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()


def _read_message(proc: _ProcessLike) -> dict:
    headers = _read_headers(proc)
    cl = int(headers.get("content-length", "0"))
    if cl <= 0:
        raise ValueError(f"missing or invalid Content-Length: {headers}")
    body = b""
    while len(body) < cl:
        chunk = proc.stdout.read(cl - len(body))
        if not chunk:
            raise EOFError("LSP server closed stream mid-message")
        body += chunk
    return json.loads(body.decode("utf-8"))


def _read_response(proc: _ProcessLike, expected_id: int, timeout: float = 30.0) -> dict:
    """Read messages until we get a response with id == expected_id.

    Notifications and requests from the server (no id) are discarded.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = _read_message(proc)
        except (EOFError, ValueError):
            raise
        if "id" in msg and msg["id"] == expected_id:
            return msg
    raise TimeoutError(f"LSP response timeout (id={expected_id})")


def start(server: LSPServer) -> None:
    """Spawn LSP server subprocess + complete initialize handshake."""
    if server.process is None:
        cmd = [server.command, *server.args]
        server.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=server.cwd,
        )
    assert server.process is not None
    proc = server.process

    init_id = _next_id(server)
    _send(proc, {
        "jsonrpc": "2.0",
        "id": init_id,
        "method": "initialize",
        "params": {
            "processId": None,
            "rootUri": f"file://{server.cwd or '.'}",
            "capabilities": {
                "textDocument": {"definition": {"dynamicRegistration": False}},
            },
        },
    })
    resp = _read_response(proc, init_id)
    if "error" in resp:
        raise LSPError(f"initialize failed: {resp['error']}")
    server.capabilities = resp.get("result", {}).get("capabilities", {})

    _send(proc, {
        "jsonrpc": "2.0",
        "method": "initialized",
        "params": {},
    })


def goto_definition(
    server: LSPServer,
    file_path: str | Path,
    line: int,
    character: int,
    timeout: float = 30.0,
) -> list[dict]:
    """Send textDocument/definition and return list of Location dicts.

    Returns [] if the server responds with `null` (no definition).
    Raises LSPError on protocol error.
    """
    if server.process is None:
        raise LSPError("LSP server not started — call start() first")
    proc = server.process
    req_id = _next_id(server)
    _send(proc, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "textDocument/definition",
        "params": {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
        },
    })
    resp = _read_response(proc, req_id, timeout=timeout)
    if "error" in resp:
        raise LSPError(f"definition failed: {resp['error']}")
    result = resp.get("result")
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


def shutdown(server: LSPServer) -> None:
    """Send shutdown + exit notifications, then terminate process.

    Fire-and-forget by design: we don't wait for the shutdown response
    because (a) some servers don't send one, (b) reading with a timeout
    requires non-blocking IO that this minimal client doesn't implement,
    and (c) the LSP spec says shutdown is best-effort. We send both
    notifications, then force-terminate.
    """
    if server.process is None:
        return
    proc = server.process
    try:
        shutdown_id = _next_id(server)
        _send(proc, {"jsonrpc": "2.0", "id": shutdown_id, "method": "shutdown", "params": None})
    except (OSError, BrokenPipeError):
        pass
    try:
        _send(proc, {"jsonrpc": "2.0", "method": "exit", "params": None})
    except (OSError, BrokenPipeError):
        pass
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
    server.process = None