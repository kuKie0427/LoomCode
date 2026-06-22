"""Tests for f-lsp-integration-p3 (loom.agent.lsp_client)."""

from __future__ import annotations

import json
import os
import threading

import pytest

from loom.agent.lsp_client import (
    LSPError,
    LSPServer,
    goto_definition,
    shutdown,
    start,
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

    def readline(self) -> bytes:
        buf = b""
        while True:
            ch = os.read(self._fd, 1)
            if not ch:
                return buf
            buf += ch
            if ch == b"\n":
                return buf

    def read(self, n: int = -1) -> bytes:
        if n == -1:
            return os.read(self._fd, 4096)
        return os.read(self._fd, n)


class FakeLSPProcess:
    """Mirrors MCP's FakeProcess: read request, dispatch to responder, write response."""

    def __init__(self, responder):
        self._responder = responder
        self.stdin_r, self.stdin_w = os.pipe()
        self.stdout_r, self.stdout_w = os.pipe()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _read_request(self) -> dict:
        headers = {}
        while True:
            line = b""
            while not line.endswith(b"\r\n"):
                ch = os.read(self.stdin_r, 1)
                if not ch:
                    raise EOFError
                line += ch
            decoded = line.decode("ascii").strip()
            if not decoded:
                break
            k, _, v = decoded.partition(":")
            headers[k.strip().lower()] = v.strip()
        cl = int(headers.get("content-length", "0"))
        body = b""
        while len(body) < cl:
            ch = os.read(self.stdin_r, cl - len(body))
            if not ch:
                raise EOFError
            body += ch
        return json.loads(body.decode("utf-8"))

    def _write(self, message: dict) -> None:
        body = json.dumps(message).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        try:
            os.write(self.stdout_w, header + body)
        except OSError:
            pass

    def _loop(self) -> None:
        try:
            while True:
                req = self._read_request()
                if "id" in req:
                    resp = self._responder(req)
                    if resp is not None:
                        self._write(resp)
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
    server = LSPServer(name="pylsp", command="ignored")
    server.process = FakeLSPProcess(responder)
    return server


def _make_pylsp_responder():
    """Realistic pylsp-style responder: initialize returns capabilities, definition returns Location."""
    def responder(req):
        method = req.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {"definitionProvider": True}}}
        if method == "textDocument/definition":
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "uri": "file:///tmp/foo.py",
                    "range": {"start": {"line": 10, "character": 4}, "end": {"line": 10, "character": 15}},
                },
            }
        return None
    return responder


def test_start_completes_initialize_handshake():
    server = _make_server_with_fake(_make_pylsp_responder())
    start(server)
    assert server.capabilities.get("definitionProvider") is True


def test_start_raises_on_initialize_error():
    def responder(req):
        if req.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -1, "message": "bad"}}
        return None
    server = _make_server_with_fake(responder)
    with pytest.raises(LSPError, match="initialize failed"):
        start(server)


def test_goto_definition_returns_single_location():
    server = _make_server_with_fake(_make_pylsp_responder())
    start(server)
    locs = goto_definition(server, "/tmp/foo.py", line=5, character=12)
    assert len(locs) == 1
    assert locs[0]["uri"] == "file:///tmp/foo.py"
    assert locs[0]["range"]["start"]["line"] == 10


def test_goto_definition_returns_empty_when_no_definition():
    def responder(req):
        if req.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {}}}
        if req.get("method") == "textDocument/definition":
            return {"jsonrpc": "2.0", "id": req["id"], "result": None}
        return None
    server = _make_server_with_fake(responder)
    start(server)
    locs = goto_definition(server, "/tmp/foo.py", line=0, character=0)
    assert locs == []


def test_goto_definition_raises_on_lsp_error():
    def responder(req):
        if req.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {}}}
        if req.get("method") == "textDocument/definition":
            return {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32600, "message": "no doc"}}
        return None
    server = _make_server_with_fake(responder)
    start(server)
    with pytest.raises(LSPError, match="no doc"):
        goto_definition(server, "/tmp/foo.py", line=0, character=0)


def test_goto_definition_without_start_raises():
    server = LSPServer(name="pylsp", command="ignored")
    with pytest.raises(LSPError, match="not started"):
        goto_definition(server, "/tmp/foo.py", line=0, character=0)


def test_shutdown_clears_process():
    server = _make_server_with_fake(_make_pylsp_responder())
    start(server)
    shutdown(server)
    assert server.process is None


def test_shutdown_is_idempotent():
    server = LSPServer(name="pylsp", command="ignored")
    shutdown(server)
    shutdown(server)


def test_lsp_module_public_api():
    from loom.agent import lsp_client
    for name in ("LSPServer", "LSPError", "start", "shutdown", "goto_definition"):
        assert hasattr(lsp_client, name), f"missing {name}"


def test_real_definition_via_fake_matches_lsp_spec_format():
    """The result we parse must match LSP's Location schema: uri + range + start/end."""
    server = _make_server_with_fake(_make_pylsp_responder())
    start(server)
    locs = goto_definition(server, "/tmp/foo.py", line=1, character=0)
    assert "uri" in locs[0]
    assert "range" in locs[0]
    assert "start" in locs[0]["range"]
    assert "end" in locs[0]["range"]
    assert "line" in locs[0]["range"]["start"]
    assert "character" in locs[0]["range"]["start"]