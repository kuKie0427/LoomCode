"""Tests for f-mcp-client-p3.

Tests the JSON-RPC framing + initialize handshake + tools/list +
tools/call using a fake subprocess. We never spawn a real MCP
server (would require npx + @modelcontextprotocol/server-* deps);
instead we inject a FakeProcess that round-trips requests through
a responder function.
"""

from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from loom.agent.mcp_client import (
    MCPError,
    MCPServer,
    _send_message,
    call_tool,
    mcp_tool_to_loom_tool,
    start,
    stop,
)


class _FileLikeWriter:
    def __init__(self, fd):
        self._fd = fd

    def write(self, data: bytes) -> int:
        try:
            os.write(self._fd, data)
        except OSError:
            pass
        return len(data)

    def flush(self):
        pass


class _FileLikeReader:
    def __init__(self, fd):
        self._fd = fd

    def read(self, n: int = -1) -> bytes:
        if n == -1:
            return os.read(self._fd, 4096)
        return os.read(self._fd, n)


class FakeProcess:
    def __init__(self, responder):
        self._responder = responder
        self.stdin_r, self.stdin_w = os.pipe()
        self.stdout_r, self.stdout_w = os.pipe()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _read_request(self) -> dict:
        buf = b""
        while not buf.endswith(b"\r\n\r\n"):
            ch = os.read(self.stdin_r, 1)
            if not ch:
                raise EOFError
            buf += ch
        cl = int(buf.decode().split("Content-Length:")[1].split("\r\n")[0].strip())
        body = b""
        while len(body) < cl:
            ch = os.read(self.stdin_r, cl - len(body))
            if not ch:
                raise EOFError
            body += ch
        return json.loads(body.decode())

    def _write_response(self, message: dict) -> None:
        body = json.dumps(message).encode()
        head = f"Content-Length: {len(body)}\r\n\r\n".encode()
        try:
            os.write(self.stdout_w, head + body)
        except OSError:
            pass

    def _loop(self) -> None:
        try:
            while True:
                req = self._read_request()
                resp = self._responder(req)
                if resp is not None:
                    self._write_response(resp)
        except (EOFError, OSError):
            pass

    @property
    def stdin(self):
        return _FileLikeWriter(self.stdin_w)

    @property
    def stdout(self):
        return _FileLikeReader(self.stdout_r)

    @property
    def stderr(self):
        return _FileLikeReader(self.stdout_r)

    def terminate(self):
        for fd in (self.stdin_w, self.stdin_r):
            try:
                os.close(fd)
            except OSError:
                pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminate()


def _make_server_with_fake(responder):
    server = MCPServer(name="test", command="ignored")
    server.process = FakeProcess(responder)
    return server


def test_send_message_writes_content_length_framed():
    captured = []
    def responder(req):
        captured.append(req)
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": True}}
    server = _make_server_with_fake(responder)
    _send_message(server.process, {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    time.sleep(0.1)
    assert len(captured) == 1
    assert captured[0]["method"] == "ping"
    assert captured[0]["id"] == 1


def test_start_completes_handshake_and_lists_tools():
    responses = iter([
        {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "fs"}, "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
            {"name": "list_dir", "description": "List directory", "inputSchema": {"type": "object"}},
        ]}},
    ])
    def responder(_req):
        return next(responses)
    server = _make_server_with_fake(responder)
    start(server)
    assert len(server.tools) == 2
    assert server.tools[0]["name"] == "read_file"
    assert server.tools[1]["name"] == "list_dir"


def test_start_raises_on_initialize_error():
    def responder(_req):
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "bad version"}}
    server = _make_server_with_fake(responder)
    with pytest.raises(MCPError, match="initialize failed"):
        start(server)


def test_call_tool_returns_result():
    def responder(req):
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "hello"}]}}
    server = _make_server_with_fake(responder)
    result = call_tool(server, "read_file", {"path": "foo.txt"})
    assert result == [{"type": "text", "text": "hello"}]


def test_call_tool_raises_on_error_response():
    def responder(req):
        return {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32600, "message": "bad args"}}
    server = _make_server_with_fake(responder)
    with pytest.raises(MCPError, match="bad args"):
        call_tool(server, "read_file", {})


def test_call_tool_raises_when_not_started():
    server = MCPServer(name="fs", command="ignored")
    with pytest.raises(MCPError, match="not started"):
        call_tool(server, "read_file", {})


def test_mcp_tool_to_loom_tool_conversion():
    server = MCPServer(name="fs", command="ignored")
    mcp_tool = {
        "name": "read_file",
        "description": "Read a file from disk",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    out = mcp_tool_to_loom_tool(server, mcp_tool)
    assert out["name"] == "mcp_fs_read_file"
    assert "[MCP:fs]" in out["description"]
    assert "input_schema" in out


def test_mcp_tool_to_loom_tool_handles_missing_fields():
    server = MCPServer(name="x", command="ignored")
    out = mcp_tool_to_loom_tool(server, {})
    assert out["name"].startswith("mcp_x_")
    assert "input_schema" in out


def test_stop_terminates_process():
    server = MCPServer(name="fs", command="ignored")
    proc = MagicMock()
    proc.wait = MagicMock(return_value=0)
    server.process = proc
    stop(server)
    proc.terminate.assert_called_once()
    assert server.process is None


def test_mcp_module_public_api():
    from loom.agent import mcp_client
    for name in ("MCPServer", "MCPError", "start", "stop", "call_tool", "mcp_tool_to_loom_tool"):
        assert hasattr(mcp_client, name), f"missing {name}"
