"""Integration tests for the RPC server — spawns `python -m loom.cli serve`
as a subprocess and exchanges JSON Lines over its stdin/stdout.

The server runs with ``LOOM_RPC_TEST_STUB=1`` so a stub LLM yields a fixed
text response without calling any real API — this lets the test run
end-to-end without credentials.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time

import pytest


def _read_line(proc: subprocess.Popen, timeout: float = 5.0) -> dict | None:
    """Read one JSON line from proc's stdout. Returns None on EOF."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # select on the underlying fd; proc.stdout is a TextIOWrapper
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if r:
            line = proc.stdout.readline()
            if line == "":
                return None  # EOF
            line = line.strip()
            if not line:
                continue
            return json.loads(line)
    raise TimeoutError(f"no output from server in {timeout}s")


def _write_line(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


@pytest.fixture
def rpc_server(tmp_path, monkeypatch):
    """Spawn `python -m loom.cli serve` in a temp workdir with a stub LLM."""
    env = os.environ.copy()
    env["LOOM_RPC_TEST_STUB"] = "1"
    env["LOOM_RPC_STUB_TEXT"] = "hello from stub"
    # Suppress loguru logging to stderr so it doesn't pollute test output
    env["LOG_LEVEL"] = "WARNING"

    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "loom.cli", "serve", "--workdir", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    try:
        # Wait for the server's ready signal
        first = _read_line(proc, timeout=10.0)
        assert first is not None, "server did not emit ready signal"
        assert first["method"] == "event/session_started", \
            f"expected session_started; got: {first}"
        yield proc
    finally:
        # Clean shutdown: send shutdown if still running, then kill
        if proc.poll() is None:
            try:
                _write_line(proc, {
                    "jsonrpc": "2.0",
                    "method": "request/shutdown",
                    "id": "cleanup",
                    "params": {},
                })
                proc.wait(timeout=3.0)
            except Exception:
                proc.kill()
                proc.wait()


def test_server_emits_session_started_on_launch(rpc_server):
    """The server must emit session_started immediately so the TUI knows
    it's ready to accept user input."""
    # The fixture already consumed the first line; just assert the server
    # is still running.
    assert rpc_server.poll() is None, "server must still be running"


def test_server_echoes_send_message_as_text_delta(rpc_server):
    """When TUI sends request/send_message, the server must:
    1. Reply with Response.ok (same id)
    2. Stream event/assistant_turn_start
    3. Stream event/text_delta (one or more)
    4. Stream event/assistant_turn_end
    """
    req_id = "test-1"
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/send_message",
        "id": req_id,
        "params": {"text": "hello"},
    })

    # Collect events until assistant_turn_end or timeout
    events = []
    deadline = time.monotonic() + 15.0
    got_response = False
    got_end = False
    while time.monotonic() < deadline and not got_end:
        try:
            line = _read_line(rpc_server, timeout=2.0)
        except TimeoutError:
            break
        if line is None:
            break
        events.append(line)
        if line.get("id") == req_id and "result" in line:
            got_response = True
        if line.get("method") == "event/assistant_turn_end":
            got_end = True

    methods = [e.get("method") for e in events]
    assert got_response, f"missing Response for {req_id}; got methods: {methods}"
    assert "event/assistant_turn_start" in methods, \
        f"missing assistant_turn_start; got: {methods}"
    assert "event/text_delta" in methods, \
        f"missing text_delta; got: {methods}"
    assert got_end, f"missing assistant_turn_end; got: {methods}"


def test_server_handles_shutdown(rpc_server):
    """request/shutdown must cause the server to exit cleanly."""
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/shutdown",
        "id": "s-1",
        "params": {},
    })
    # Server should exit within 5 seconds
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if rpc_server.poll() is not None:
            break
        time.sleep(0.1)
    assert rpc_server.poll() is not None, "server did not shut down"
    # Exit code should be 0
    assert rpc_server.returncode == 0, f"server exited with {rpc_server.returncode}"


def test_server_handles_unknown_method(rpc_server):
    """Unknown methods must return a JSON-RPC error response."""
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/nonexistent_method",
        "id": "unk-1",
        "params": {},
    })
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            line = _read_line(rpc_server, timeout=2.0)
        except TimeoutError:
            break
        if line is None:
            break
        if line.get("id") == "unk-1":
            assert "error" in line, f"expected error response; got: {line}"
            assert line["error"]["code"] == -32601
            return
    pytest.fail("did not receive error response for unknown method")


def test_server_handles_malformed_input(rpc_server):
    """Malformed JSON must emit event/error but not crash."""
    rpc_server.stdin.write("not valid json\n")
    rpc_server.stdin.flush()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            line = _read_line(rpc_server, timeout=2.0)
        except TimeoutError:
            break
        if line is None:
            break
        if line.get("method") == "event/error":
            assert "malformed" in line["params"]["message"]
            return
    pytest.fail("did not receive event/error for malformed input")


def test_server_new_session_returns_id(rpc_server):
    """request/new_session must return a fresh session_id."""
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/new_session",
        "id": "ns-1",
        "params": {},
    })
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            line = _read_line(rpc_server, timeout=2.0)
        except TimeoutError:
            break
        if line is None:
            break
        if line.get("id") == "ns-1":
            assert line["result"]["session_id"], "new session must return session_id"
            return
    pytest.fail("did not receive response for new_session")
