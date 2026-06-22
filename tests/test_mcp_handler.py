"""Tests for f-mcp-handler-safety (Phase PM-3, M6/M7/M12/M16).

Covers the four PM-3 output-shape mitigations in
``mcp_manager._make_mcp_handler`` + the stderr-collection path in
``mcp_client.start``:

  - ``_flatten_mcp_content`` (M12): text, multiple text, image,
    resource, unknown type, string passthrough, empty list.
  - 50KB truncation with footer (M7) + ``mcp_output_truncated``
    trace event.
  - Crash recovery (M6): ``logger.warning`` visible, server evicted
    from ``_ACTIVE_SERVERS`` + ``_PER_SERVER_LOCKS``, all
    ``mcp__<server>__*`` tools unregistered from ``TOOL_REGISTRY``.
  - ``mcp_request`` trace event emitted before the call (R5).
  - Stderr tail collected on handshake failure (M16).

The handler tests inject a synthetic ``MCPServer`` into
``_ACTIVE_SERVERS`` and patch ``mcp_client.call_tool`` so no real
subprocess is spawned. The stderr test mocks ``subprocess.Popen`` and
drives a fake ``stderr`` file descriptor.
"""

from __future__ import annotations

import io
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from loom.agent import mcp_client
from loom.agent import mcp_manager as mm
from loom.agent.mcp_client import MCPError, MCPServer
from loom.agent.tools import TOOL_REGISTRY

# ── Shared fixture: clean manager state between tests ──────────────────────


@pytest.fixture(autouse=True)
def _isolate_manager_state():
    """Wipe mcp_manager module-level state + remove any leaked MCP tools."""
    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
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
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass


def _seed_server(server_name: str = "fs") -> MCPServer:
    """Install a fake server + lock in the manager cache and return it."""
    server = MCPServer(name=server_name, command="ignored")
    mm._ACTIVE_SERVERS[server_name] = server
    mm._PER_SERVER_LOCKS[server_name] = __import__("threading").Lock()
    return server


# ── 1. _flatten_mcp_content — M12 ──────────────────────────────────────────


def test_flatten_mcp_content_text_only() -> None:
    """A single text block returns its text verbatim."""
    out = mm._flatten_mcp_content([{"type": "text", "text": "hello"}])
    assert out == "hello"


def test_flatten_mcp_content_multiple_text() -> None:
    """Multiple text blocks joined with a newline, in order."""
    out = mm._flatten_mcp_content([
        {"type": "text", "text": "alpha"},
        {"type": "text", "text": "beta"},
    ])
    assert out == "alpha\nbeta"


def test_flatten_mcp_content_image_placeholder() -> None:
    """An image block becomes '[MCP: <mime> image content omitted]'."""
    out = mm._flatten_mcp_content([
        {"type": "image", "data": "BASE64DATA==", "mimeType": "image/png"},
    ])
    assert out == "[MCP: image/png image content omitted]"


def test_flatten_mcp_content_resource_placeholder() -> None:
    """A resource block becomes '[MCP: resource <uri>]'."""
    out = mm._flatten_mcp_content([
        {
            "type": "resource",
            "resource": {"uri": "file:///etc/hosts", "text": "127.0.0.1 ..."},
        },
    ])
    assert out == "[MCP: resource file:///etc/hosts]"


def test_flatten_mcp_content_unknown_type() -> None:
    """Unknown block types render as '[MCP: unknown content type '<type>']'."""
    out = mm._flatten_mcp_content([{"type": "audio", "data": "..."}])
    assert out == "[MCP: unknown content type 'audio']"


def test_flatten_mcp_content_string_passthrough() -> None:
    """If content is already a string, return it verbatim."""
    out = mm._flatten_mcp_content("already a string")
    assert out == "already a string"


def test_flatten_mcp_content_empty_list() -> None:
    """An empty content list returns the empty string."""
    out = mm._flatten_mcp_content([])
    assert out == ""


def test_flatten_mcp_content_mixed_text_and_image() -> None:
    """Mixed text + image blocks: text emitted, image becomes placeholder."""
    out = mm._flatten_mcp_content([
        {"type": "text", "text": "before"},
        {"type": "image", "mimeType": "image/jpeg", "data": "..."},
        {"type": "text", "text": "after"},
    ])
    assert out == "before\n[MCP: image/jpeg image content omitted]\nafter"


def test_flatten_mcp_content_resource_missing_uri() -> None:
    """A resource block with no uri falls back to '?'."""
    out = mm._flatten_mcp_content([{"type": "resource", "resource": {}}])
    assert out == "[MCP: resource ?]"


# ── 2. Handler truncation — M7 ─────────────────────────────────────────────


def test_mcp_handler_truncates_at_50kb() -> None:
    """A 60KB text result is truncated to 50KB + a footer."""
    _seed_server("big")
    big_text = "x" * 60000
    with patch.object(
        mcp_client, "call_tool", return_value=[{"type": "text", "text": big_text}],
    ):
        handler = mm._make_mcp_handler("big", "huge")
        out = handler()
    # The body must be exactly 50000 chars before the footer.
    body, sep, footer = out.rpartition("\n... (truncated, ")
    assert sep == "\n... (truncated, "
    assert len(body) == mm.MAX_MCP_OUTPUT_CHARS == 50000
    assert footer.startswith("10000 more characters)")


def test_mcp_handler_truncation_emits_trace_event() -> None:
    """Truncation writes an mcp_output_truncated event to the trace."""
    from loom.agent import trace as trace_mod

    class _FakeTrace:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def record(self, event: str, **fields: object) -> None:
            self.events.append((event, fields))

    fake = _FakeTrace()
    _seed_server("big")
    big_text = "x" * 60000
    with patch.object(trace_mod, "current", return_value=fake), \
         patch.object(
             mcp_client, "call_tool",
             return_value=[{"type": "text", "text": big_text}],
         ):
        handler = mm._make_mcp_handler("big", "huge")
        handler()
    event_names = [e for e, _ in fake.events]
    assert "mcp_request" in event_names, "R5: pre-lock trace event missing"
    assert "mcp_output_truncated" in event_names, "M7: truncation trace event missing"
    trunc = next(f for e, f in fake.events if e == "mcp_output_truncated")
    assert trunc["server"] == "big"
    assert trunc["tool"] == "huge"
    assert trunc["capped_len"] == 50000


def test_mcp_handler_no_truncation_for_small_output() -> None:
    """A small output is returned verbatim — no truncation, no footer."""
    _seed_server("small")
    with patch.object(
        mcp_client, "call_tool",
        return_value=[{"type": "text", "text": "short"}],
    ):
        handler = mm._make_mcp_handler("small", "ok")
        out = handler()
    assert out == "short"


# ── 3. Handler crash — M6 ──────────────────────────────────────────────────


def test_mcp_handler_crash_evicts_and_unregisters() -> None:
    """call_tool raises → server evicted, tools unregistered, warning logged."""
    _seed_server("crashy")
    # Pre-register two tools for this server so we can confirm unregistration.
    from loom.agent.tool_registry import Tool
    for tname in ("mcp__crashy__a", "mcp__crashy__b"):
        TOOL_REGISTRY.register(Tool(
            name=tname,
            description="t",
            input_schema={"type": "object", "properties": {}},
            handler=lambda **kw: "x",
            is_read_only=False,
            is_concurrent_safe=False,
            enabled=True,
        ))
    assert "mcp__crashy__a" in TOOL_REGISTRY.names()
    assert "mcp__crashy__b" in TOOL_REGISTRY.names()

    with patch.object(
        mcp_client, "call_tool",
        side_effect=RuntimeError("boom — server crashed"),
    ), patch.object(mm.logger, "warning") as warning_spy:
        handler = mm._make_mcp_handler("crashy", "broken")
        out = handler()

    # M6 invariant: returns a string error (not raise), names eviction.
    assert "MCP error" in out
    assert "evicted" in out
    assert "boom — server crashed" in out
    # Cache state: server gone, lock gone.
    assert "crashy" not in mm._ACTIVE_SERVERS
    assert "crashy" not in mm._PER_SERVER_LOCKS
    # Tools unregistered.
    assert "mcp__crashy__a" not in TOOL_REGISTRY.names()
    assert "mcp__crashy__b" not in TOOL_REGISTRY.names()
    # Warning emitted visibly (spy on the bound logger.warning method).
    assert warning_spy.called, "M6 visible warning NOT emitted via logger.warning"
    call_args = warning_spy.call_args.args
    # loguru uses lazy %-format: args[0] is the template, args[1:] are values.
    rendered = call_args[0] % call_args[1:]
    assert "crashy" in rendered, (
        f"M6 warning missing server name; got: {rendered!r}"
    )
    assert "evicted" in rendered, (
        f"M6 warning missing 'evicted'; got: {rendered!r}"
    )


def test_mcp_handler_records_request_trace() -> None:
    """R5: mcp_request event emitted BEFORE call_tool is invoked."""
    from loom.agent import trace as trace_mod

    class _FakeTrace:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def record(self, event: str, **fields: object) -> None:
            self.events.append((event, fields))

    fake = _FakeTrace()
    _seed_server("trace")
    order: list[str] = []

    def _call(server, tool_name, arguments):
        order.append("call_tool")
        return [{"type": "text", "text": "ok"}]

    with patch.object(trace_mod, "current", return_value=fake), \
         patch.object(mcp_client, "call_tool", side_effect=_call):
        # Patch tr.record to also track the call order.
        original_record = fake.record
        def _record_with_order(event: str, **fields: object) -> None:
            order.append(f"trace:{event}")
            original_record(event, **fields)
        with patch.object(fake, "record", side_effect=_record_with_order):
            handler = mm._make_mcp_handler("trace", "ok")
            handler()

    # The trace event must come BEFORE the call_tool invocation.
    trace_indices = [i for i, x in enumerate(order) if x.startswith("trace:")]
    call_indices = [i for i, x in enumerate(order) if x == "call_tool"]
    assert trace_indices, "no trace events recorded"
    assert call_indices, "call_tool never invoked"
    assert min(trace_indices) < min(call_indices), (
        f"R5 violation: mcp_request must be recorded before call_tool; "
        f"order={order}"
    )


def test_mcp_handler_returns_error_when_server_not_connected() -> None:
    """If server is not in _ACTIVE_SERVERS, return a 'not connected' string."""
    handler = mm._make_mcp_handler("ghost", "nope")
    out = handler()
    assert "not connected" in out


def test_mcp_handler_returns_error_when_lock_missing() -> None:
    """If server is present but lock is gone (race with eviction), safe string."""
    server = MCPServer(name="nolock", command="ignored")
    mm._ACTIVE_SERVERS["nolock"] = server
    # Deliberately do NOT add a per-server lock.
    handler = mm._make_mcp_handler("nolock", "nope")
    out = handler()
    assert "lock missing" in out


# ── 4. Stderr collection — M16 ────────────────────────────────────────────


def test_mcp_start_collects_stderr_on_failure() -> None:
    """On handshake failure, MCPError message includes the stderr tail."""
    # Handshake will fail because the fake process never responds.
    # We pipe pre-written bytes into stderr before start() reads it.
    r, w = os.pipe()
    os.write(w, b"ERROR: missing GITHUB_TOKEN env var\nat startup\n")
    os.close(w)

    fake_proc = MagicMock()
    fake_proc.stdin = io.BytesIO()
    fake_proc.stdout = io.BytesIO()  # no JSON-RPC response → handshake fails
    fake_proc.stderr = os.fdopen(r, "rb", buffering=0)
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=1)
    fake_proc.kill = MagicMock()

    server = MCPServer(name="ghost", command="ignored")
    server.process = fake_proc

    with pytest.raises(MCPError) as excinfo:
        mcp_client.start(server)

    msg = str(excinfo.value)
    assert "server stderr tail" in msg
    assert "GITHUB_TOKEN" in msg


def test_mcp_start_raises_friendly_error_on_missing_command() -> None:
    """Popen FileNotFoundError → MCPError naming the missing command."""
    server = MCPServer(name="nope", command="/definitely/does/not/exist/xyz")

    with patch.object(
        subprocess, "Popen",
        side_effect=FileNotFoundError(2, "No such file or directory", "/definitely/does/not/exist/xyz"),
    ):
        with pytest.raises(MCPError, match=r"not found"):
            mcp_client.start(server)


def test_mcp_start_works_when_stderr_unavailable() -> None:
    """A handshake failure with no stderr still raises MCPError (no crash)."""
    fake_proc = MagicMock()
    fake_proc.stdin = io.BytesIO()
    fake_proc.stdout = io.BytesIO()
    fake_proc.stderr = None  # no stderr — diagnostic must not crash
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=1)
    fake_proc.kill = MagicMock()

    server = MCPServer(name="nostderr", command="ignored")
    server.process = fake_proc

    with pytest.raises(MCPError):
        mcp_client.start(server)