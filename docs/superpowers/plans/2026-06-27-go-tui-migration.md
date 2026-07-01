# Go TUI + Python Core Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Textual-based Python TUI with a Go (Bubble Tea) frontend that communicates with the existing Python `loom.agent.*` core via a JSON-RPC protocol over stdio, while preserving the existing `loom cli run --plain` REPL as a fallback.

**Architecture:** The Go binary (`loom-tui`) spawns `python -m loom.cli serve` as a child process. They exchange JSON Lines over the child's stdin/stdout: the Python side emits **events** (text deltas, tool calls, thinking, subagent markers, etc.); the Go side sends **requests** (user message, cancel, permission response, model switch). The existing `loom.agent.agent_loop` is reused unchanged — only the callback dispatch layer is rewired from "post Textual Message" to "write JSON line to stdout". All 563 pytest + 32 eval cases are preserved.

**Tech Stack:** Go 1.22+, [Bubble Tea](https://github.com/charmbracelet/bubbletea), [Lipgloss](https://github.com/charmbracelet/lipgloss), [Glamour](https://github.com/charmbracelet/glamour) (Markdown rendering), [Bubblezone](https://github.com/lrstanley/bubblezone) (mouse zones). Python stdlib `json`, `sys`, `threading`, `queue`.

---

## File Structure

### Python side (new files)

| Path | Responsibility |
|---|---|
| `loom/rpc/__init__.py` | Package marker |
| `loom/rpc/protocol.py` | JSON-RPC message type definitions (event/request/response) shared by both sides |
| `loom/rpc/server.py` | stdio JSON-RPC server: reads requests from stdin, writes events to stdout; bridges to `agent_loop` via callbacks that emit JSON lines |
| `loom/rpc/codec.py` | Line-based JSON encoder/decoder; handles partial reads, malformed lines, backpressure |
| `loom/cli.py` (modify) | Add `serve` subcommand that launches `loom.rpc.server` |
| `loom/agent/loop.py` (modify) | Add `rpc_server` optional param to `agent_loop` so permission-asker can route through RPC instead of Textual's `push_screen_wait` |
| `tests/test_rpc_protocol.py` | Unit tests for message serialization/deserialization |
| `tests/test_rpc_server.py` | Integration tests: spawn `python -m loom.cli serve`, send requests, assert events |
| `tests/test_rpc_codec.py` | Unit tests for line framing, partial reads, malformed input |

### Go side (new `tui-go/` directory at repo root)

| Path | Responsibility |
|---|---|
| `tui-go/go.mod` | Go module definition |
| `tui-go/main.go` | Entry point: spawn Python child, wire stdio to Bubble Tea program |
| `tui-go/protocol/protocol.go` | Go mirror of `loom/rpc/protocol.py` — struct definitions for events/requests |
| `tui-go/rpc/rpc.go` | JSON-RPC client: reads JSON Lines from Python stdout, writes requests to Python stdin, matches responses to requests by `id` |
| `tui-go/model/model.go` | Bubble Tea model: holds conversation state, streaming buffers, tool call list |
| `tui-go/model/agent.go` | Wraps `rpc.go` as a Bubble Tea `tea.Cmd` that emits `agentEventMsg` messages |
| `tui-go/view/chatlog.go` | ChatLog viewport: renders user/assistant messages, streaming overlay, thinking display, tool call markers |
| `tui-go/view/composer.go` | Input box: textarea + send on Enter (Shift+Enter for newline) |
| `tui-go/view/statusbar.go` | Status bar: model name, token count, git branch, credential status |
| `tui-go/view/header.go` | Header with section toggles (todos / subagents / sessions / models) |
| `tui-go/view/permission.go` | Permission modal: Y/N prompt with tool name + args + reason |
| `tui-go/markdown/renderer.go` | Glamour-based Markdown renderer with CJK-aware wrapping and linkify disabled |

### Modified existing files

| Path | Change |
|---|---|
| `loom/cli.py` | Add `serve` subcommand (lines ~209); add `--tui` flag to `run` that launches `loom-tui` binary if present, falls back to Textual |
| `loom/agent/loop.py` | Make `hooks._asker` injectable so RPC server can provide its own asker (currently set in TUI's `on_mount`) |
| `loom/agent/hooks.py` | Add `set_asker(asker_fn)` so `loop.py` doesn't depend on TUI for permission gating |
| `feature_list.json` | Add `f-go-tui-migration` feature entry |
| `Makefile` or `scripts/build-tui.sh` | Build the Go binary and place it on PATH |

---

## Phase 1: Python RPC Server (no Go yet)

**Goal:** A working `loom cli serve` that accepts JSON requests on stdin and emits JSON events on stdout, driving `agent_loop` end-to-end. Testable from a terminal with `echo` + `python -m loom.cli serve`.

### Task 1: Define the JSON-RPC protocol

**Files:**
- Create: `loom/rpc/__init__.py`
- Create: `loom/rpc/protocol.py`
- Test: `tests/test_rpc_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rpc_protocol.py
"""Tests for the loom JSON-RPC protocol — shared between Python core and Go TUI."""

from __future__ import annotations

import json

import pytest

from loom.rpc.protocol import (
    Event,
    Request,
    Response,
    EVENT_TYPES,
    REQUEST_TYPES,
)


def test_event_text_delta_serializes_to_json():
    ev = Event.text_delta(text="hello")
    line = ev.to_jsonl()
    parsed = json.loads(line)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["method"] == "event/text_delta"
    assert parsed["params"] == {"text": "hello"}
    # to_jsonl produces a single line (no trailing newline in the string itself)
    assert "\n" not in line


def test_event_thinking_delta_serializes():
    ev = Event.thinking_delta(text="reasoning...")
    parsed = json.loads(ev.to_jsonl())
    assert parsed["method"] == "event/thinking_delta"
    assert parsed["params"] == {"text": "reasoning..."}


def test_event_tool_use_started_serializes():
    ev = Event.tool_use_started(
        tool_name="read_file",
        tool_input={"path": "/x"},
        tool_use_id="tu_1",
    )
    parsed = json.loads(ev.to_jsonl())
    assert parsed["method"] == "event/tool_use_started"
    assert parsed["params"]["tool_name"] == "read_file"
    assert parsed["params"]["tool_use_id"] == "tu_1"


def test_event_tool_use_completed_serializes():
    ev = Event.tool_use_completed(
        tool_use_id="tu_1", output="file contents", is_error=False
    )
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["is_error"] is False


def test_event_assistant_turn_start_serializes():
    ev = Event.assistant_turn_start(agent_name="织轴")
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["agent_name"] == "织轴"


def test_event_assistant_turn_end_serializes():
    ev = Event.assistant_turn_end(tool_calls=3, total_messages=10, duration=2.5)
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["tool_calls"] == 3
    assert parsed["params"]["duration"] == 2.5


def test_event_subagent_start_serializes():
    ev = Event.subagent_start(
        subagent_id="sa_1", description="refactor foo", agent_name="织针"
    )
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["agent_name"] == "织针"


def test_event_subagent_end_serializes():
    ev = Event.subagent_end(subagent_id="sa_1", elapsed=1.2, state="done")
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["state"] == "done"


def test_event_compact_occurred_serializes():
    ev = Event.compact_occurred(msgs_before=50, msgs_after=10)
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["msgs_before"] == 50


def test_event_todo_update_serializes():
    ev = Event.todo_update(todos=[{"id": "t1", "content": "do x", "status": "in_progress"}])
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["todos"][0]["id"] == "t1"


def test_event_show_notification_serializes():
    ev = Event.show_notification(text="Saved", severity="info")
    parsed = json.loads(ev.to_jsonl())
    assert parsed["params"]["severity"] == "info"


def test_request_send_message_serializes():
    req = Request.send_message(id="r1", text="hello agent")
    parsed = json.loads(req.to_jsonl())
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["method"] == "request/send_message"
    assert parsed["id"] == "r1"
    assert parsed["params"] == {"text": "hello agent"}


def test_request_cancel_serializes():
    req = Request.cancel(id="r2")
    parsed = json.loads(req.to_jsonl())
    assert parsed["method"] == "request/cancel"


def test_request_permission_response_serializes():
    req = Request.permission_response(id="r3", request_id="p1", decision="allow")
    parsed = json.loads(req.to_jsonl())
    assert parsed["params"]["decision"] == "allow"


def test_request_pick_model_serializes():
    req = Request.pick_model(id="r4", model="anthropic/claude-sonnet-4-5")
    parsed = json.loads(req.to_jsonl())
    assert parsed["params"]["model"] == "anthropic/claude-sonnet-4-5"


def test_request_list_sessions_serializes():
    req = Request.list_sessions(id="r5")
    parsed = json.loads(req.to_jsonl())
    assert parsed["method"] == "request/list_sessions"


def test_response_send_message_serializes():
    resp = Response.ok(id="r1", result={"ack": True})
    parsed = json.loads(resp.to_jsonl())
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == "r1"
    assert parsed["result"] == {"ack": True}


def test_response_error_serializes():
    resp = Response.error(id="r1", code=-32000, message="agent crashed")
    parsed = json.loads(resp.to_jsonl())
    assert parsed["error"]["code"] == -32000
    assert parsed["error"]["message"] == "agent crashed"


def test_response_permission_request_serializes():
    """When agent needs permission, it sends a response that's actually a
    server-initiated request asking the TUI to prompt the user."""
    resp = Response.permission_request(
        id="p1", tool_name="bash", tool_input={"command": "rm -rf /"}, reason="destructive"
    )
    parsed = json.loads(resp.to_jsonl())
    assert parsed["method"] == "request/permission"
    assert parsed["params"]["tool_name"] == "bash"


def test_event_from_jsonl_round_trip():
    ev = Event.text_delta(text="hi")
    line = ev.to_jsonl()
    restored = Event.from_jsonl(line)
    assert restored.method == "event/text_delta"
    assert restored.params == {"text": "hi"}


def test_request_from_jsonl_round_trip():
    req = Request.send_message(id="r1", text="hi")
    line = req.to_jsonl()
    restored = Request.from_jsonl(line)
    assert restored.id == "r1"
    assert restored.params == {"text": "hi"}


def test_from_jsonl_rejects_malformed_json():
    with pytest.raises(ValueError, match="invalid JSON"):
        Event.from_jsonl("not json at all")


def test_from_jsonl_rejects_missing_method():
    with pytest.raises(ValueError, match="missing 'method'"):
        Event.from_jsonl('{"jsonrpc": "2.0", "params": {}}')


def test_all_event_types_have_factory_methods():
    """Every entry in EVENT_TYPES must have a corresponding factory on Event."""
    for method in EVENT_TYPES:
        # Strip the "event/" prefix to get the factory name
        factory_name = method.replace("event/", "")
        assert hasattr(Event, factory_name), (
            f"Event.{factory_name}() factory missing for {method}"
        )


def test_all_request_types_have_factory_methods():
    for method in REQUEST_TYPES:
        factory_name = method.replace("request/", "")
        assert hasattr(Request, factory_name), f"Request.{factory_name}() missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rpc_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loom.rpc'`

- [ ] **Step 3: Create the package and protocol module**

```python
# loom/rpc/__init__.py
"""JSON-RPC protocol between the Python loom core and external TUI frontends.

The protocol is line-delimited JSON (JSON Lines / NDJSON): each message is a
single JSON object on one line terminated by ``\n``. This is simpler than
framed JSON-RPC over a socket and is a natural fit for stdio communication
(a child process's stdin/stdout are already line-buffered by default).

Message direction:
- Python -> TUI: ``Event`` (streamed, no response expected) and
  ``Response`` (reply to a prior TUI ``Request``).
- TUI -> Python: ``Request`` (expects a ``Response``) and
  ``Notification`` (no response expected — e.g. cancel).
"""
```

```python
# loom/rpc/protocol.py
"""JSON-RPC 2.0 message types for the loom TUI protocol.

Design principles:
1. Every message is a single JSON object on one line (JSON Lines / NDJSON).
2. Events (Python -> TUI, no response) use ``method`` prefixed with ``event/``.
3. Requests (TUI -> Python, expect response) use ``method`` prefixed with
   ``request/`` and carry an ``id`` for response matching.
4. Responses carry the original request's ``id``.
5. Permission prompts are server-initiated requests: the Python side sends
   a ``request/permission`` message with a fresh ``id``; the TUI replies
   with a ``request/permission_response`` referencing that ``id``.

This module is the single source of truth for the protocol. The Go TUI
(tui-go/protocol/protocol.go) mirrors these definitions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


# All event methods (Python -> TUI, fire-and-forget).
# Every entry must have a matching factory method on `Event`.
EVENT_TYPES: frozenset[str] = frozenset({
    "event/assistant_turn_start",
    "event/assistant_turn_end",
    "event/text_delta",
    "event/thinking_delta",
    "event/tool_use_started",
    "event/tool_use_completed",
    "event/compact_occurred",
    "event/todo_update",
    "event/subagent_start",
    "event/subagent_end",
    "event/show_notification",
    "event/session_started",
    "event/session_ended",
    "event/error",
})

# All request methods (TUI -> Python, expect response).
REQUEST_TYPES: frozenset[str] = frozenset({
    "request/send_message",
    "request/cancel",
    "request/permission_response",
    "request/pick_model",
    "request/list_sessions",
    "request/load_session",
    "request/new_session",
    "request/shutdown",
})

# Server-initiated requests (Python -> TUI, expect response).
# Used for permission prompts.
SERVER_REQUEST_TYPES: frozenset[str] = frozenset({
    "request/permission",
})


@dataclass
class _Message:
    """Base for all protocol messages. Serializes to a single JSON line."""

    jsonrpc: str = "2.0"

    def to_jsonl(self) -> str:
        """Return the message as a single JSON line (no trailing newline)."""
        d = self._to_dict()
        d["jsonrpc"] = "2.0"
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    def _to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def from_jsonl(line: str) -> "_Message | Request | Response | Event":
        """Parse a JSON line into the appropriate message subtype.

        Dispatches based on the ``method`` prefix:
        - ``event/*`` -> :class:`Event`
        - ``request/*`` (with ``id``) -> :class:`Request`
        - has ``result`` or ``error`` (with ``id``) -> :class:`Response`
        """
        line = line.strip()
        if not line:
            raise ValueError("empty line")
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(d, dict):
            raise ValueError("expected JSON object")
        method = d.get("method")
        if method is None:
            # Response (has result or error)
            if "result" in d or "error" in d:
                return Response._from_dict(d)
            raise ValueError("missing 'method' or 'result'/'error'")
        if method.startswith("event/"):
            return Event._from_dict(d)
        if method.startswith("request/"):
            return Request._from_dict(d)
        raise ValueError(f"unknown method prefix: {method!r}")


@dataclass
class Event(_Message):
    """A streamed event from Python to TUI. No response expected."""

    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def _to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "params": self.params}

    @staticmethod
    def _from_dict(d: dict[str, Any]) -> "Event":
        return Event(method=d["method"], params=d.get("params", {}))

    # ---- Factory methods (one per event type) ----

    @staticmethod
    def assistant_turn_start(agent_name: str = "织轴") -> "Event":
        return Event(method="event/assistant_turn_start",
                     params={"agent_name": agent_name})

    @staticmethod
    def assistant_turn_end(tool_calls: int, total_messages: int, duration: float) -> "Event":
        return Event(method="event/assistant_turn_end",
                     params={"tool_calls": tool_calls,
                             "total_messages": total_messages,
                             "duration": duration})

    @staticmethod
    def text_delta(text: str) -> "Event":
        return Event(method="event/text_delta", params={"text": text})

    @staticmethod
    def thinking_delta(text: str) -> "Event":
        return Event(method="event/thinking_delta", params={"text": text})

    @staticmethod
    def tool_use_started(tool_name: str, tool_input: dict, tool_use_id: str) -> "Event":
        return Event(method="event/tool_use_started",
                     params={"tool_name": tool_name,
                             "tool_input": tool_input,
                             "tool_use_id": tool_use_id})

    @staticmethod
    def tool_use_completed(tool_use_id: str, output: str, is_error: bool) -> "Event":
        return Event(method="event/tool_use_completed",
                     params={"tool_use_id": tool_use_id,
                             "output": output,
                             "is_error": is_error})

    @staticmethod
    def compact_occurred(msgs_before: int, msgs_after: int) -> "Event":
        return Event(method="event/compact_occurred",
                     params={"msgs_before": msgs_before, "msgs_after": msgs_after})

    @staticmethod
    def todo_update(todos: list) -> "Event":
        return Event(method="event/todo_update", params={"todos": todos})

    @staticmethod
    def subagent_start(subagent_id: str, description: str, agent_name: str = "织针") -> "Event":
        return Event(method="event/subagent_start",
                     params={"subagent_id": subagent_id,
                             "description": description,
                             "agent_name": agent_name})

    @staticmethod
    def subagent_end(subagent_id: str, elapsed: float, state: str) -> "Event":
        return Event(method="event/subagent_end",
                     params={"subagent_id": subagent_id,
                             "elapsed": elapsed,
                             "state": state})

    @staticmethod
    def show_notification(text: str, severity: str = "info") -> "Event":
        return Event(method="event/show_notification",
                     params={"text": text, "severity": severity})

    @staticmethod
    def session_started(session_id: str, model: str) -> "Event":
        return Event(method="event/session_started",
                     params={"session_id": session_id, "model": model})

    @staticmethod
    def session_ended(session_id: str) -> "Event":
        return Event(method="event/session_ended",
                     params={"session_id": session_id})

    @staticmethod
    def error(message: str, code: int = -32000) -> "Event":
        return Event(method="event/error",
                     params={"message": message, "code": code})


@dataclass
class Request(_Message):
    """A request from TUI to Python. Expects a Response with the same id."""

    method: str = ""
    id: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def _to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "id": self.id, "params": self.params}

    @staticmethod
    def _from_dict(d: dict[str, Any]) -> "Request":
        return Request(method=d["method"], id=d.get("id", ""),
                       params=d.get("params", {}))

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:8]

    # ---- Factory methods ----

    @staticmethod
    def send_message(id: str | None = None, text: str = "") -> "Request":
        return Request(method="request/send_message",
                       id=id or Request._new_id(),
                       params={"text": text})

    @staticmethod
    def cancel(id: str | None = None) -> "Request":
        return Request(method="request/cancel",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def permission_response(id: str | None = None, request_id: str = "",
                           decision: str = "deny") -> "Request":
        return Request(method="request/permission_response",
                       id=id or Request._new_id(),
                       params={"request_id": request_id, "decision": decision})

    @staticmethod
    def pick_model(id: str | None = None, model: str = "") -> "Request":
        return Request(method="request/pick_model",
                       id=id or Request._new_id(),
                       params={"model": model})

    @staticmethod
    def list_sessions(id: str | None = None) -> "Request":
        return Request(method="request/list_sessions",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def load_session(id: str | None = None, session_id: str = "") -> "Request":
        return Request(method="request/load_session",
                       id=id or Request._new_id(),
                       params={"session_id": session_id})

    @staticmethod
    def new_session(id: str | None = None) -> "Request":
        return Request(method="request/new_session",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def shutdown(id: str | None = None) -> "Request":
        return Request(method="request/shutdown",
                       id=id or Request._new_id(),
                       params={})


@dataclass
class Response(_Message):
    """A response from Python to TUI. Matches a prior Request by id.

    Also used for server-initiated requests (e.g. permission prompts):
    the Python side sends a Response-like message with ``method`` set to
    ``request/permission`` and a fresh ``id``; the TUI replies with a
    ``request/permission_response``.
    """

    id: str = ""
    result: Any = None
    error: dict[str, Any] | None = None
    method: str = ""  # set only for server-initiated requests
    params: dict[str, Any] = field(default_factory=dict)

    def _to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id}
        if self.error is not None:
            d["error"] = self.error
        elif self.method:
            # Server-initiated request (has method + params, no result)
            d["method"] = self.method
            d["params"] = self.params
        else:
            d["result"] = self.result
        return d

    @staticmethod
    def _from_dict(d: dict[str, Any]) -> "Response":
        return Response(
            id=d.get("id", ""),
            result=d.get("result"),
            error=d.get("error"),
            method=d.get("method", ""),
            params=d.get("params", {}),
        )

    @staticmethod
    def ok(id: str, result: Any = None) -> "Response":
        return Response(id=id, result=result)

    @staticmethod
    def error_response(id: str, code: int, message: str) -> "Response":
        return Response(id=id, error={"code": code, "message": message})

    @staticmethod
    def permission_request(id: str, tool_name: str, tool_input: dict,
                           reason: str) -> "Response":
        """Server-initiated: asks TUI to prompt the user for permission."""
        return Response(
            id=id,
            method="request/permission",
            params={"tool_name": tool_name,
                    "tool_input": tool_input,
                    "reason": reason},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rpc_protocol.py -v`
Expected: PASS (all 25 tests)

- [ ] **Step 5: Commit**

```bash
git add loom/rpc/__init__.py loom/rpc/protocol.py tests/test_rpc_protocol.py
git commit -m "feat(rpc): define JSON-RPC protocol for TUI <-> core communication"
```

---

### Task 2: Line-based codec for stdio I/O

**Files:**
- Create: `loom/rpc/codec.py`
- Test: `tests/test_rpc_codec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rpc_codec.py
"""Tests for the line-based JSON codec — handles framing, partial reads,
malformed input, and backpressure."""

from __future__ import annotations

import io
import threading
import time

import pytest

from loom.rpc.codec import LineCodec
from loom.rpc.protocol import Event, Request


def test_write_event_appends_newline():
    buf = io.StringIO()
    codec = LineCodec(writer=buf)
    codec.write_event(Event.text_delta(text="hi"))
    assert buf.getvalue() == '{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"hi"}}\n'


def test_write_event_is_thread_safe():
    """Two threads writing simultaneously must not interleave their lines."""
    buf = io.StringIO()
    codec = LineCodec(writer=buf)
    barrier = threading.Barrier(2)

    def writer(n: int):
        barrier.wait()
        for i in range(50):
            codec.write_event(Event.text_delta(text=f"t{n}-{i}"))

    t1 = threading.Thread(target=writer, args=(1,))
    t2 = threading.Thread(target=writer, args=(2,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    lines = buf.getvalue().split("\n")
    # Trailing empty string from final newline
    lines = [l for l in lines if l]
    assert len(lines) == 100
    # Every line must be valid JSON (no interleaving)
    import json
    for line in lines:
        json.loads(line)  # raises if malformed


def test_read_request_returns_parsed_message():
    buf = io.StringIO('{"jsonrpc":"2.0","method":"request/send_message","id":"r1","params":{"text":"hi"}}\n')
    codec = LineCodec(reader=buf)
    msg = codec.read_message()
    assert msg is not None
    assert msg.method == "request/send_message"
    assert msg.id == "r1"


def test_read_event_returns_parsed_message():
    buf = io.StringIO('{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"hi"}}\n')
    codec = LineCodec(reader=buf)
    msg = codec.read_message()
    assert msg is not None
    assert msg.method == "event/text_delta"


def test_read_returns_none_on_eof():
    buf = io.StringIO("")
    codec = LineCodec(reader=buf)
    assert codec.read_message() is None


def test_read_returns_none_on_blank_line_only():
    buf = io.StringIO("\n\n")
    codec = LineCodec(reader=buf)
    assert codec.read_message() is None


def test_read_skips_blank_lines_between_messages():
    buf = io.StringIO('\n{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"a"}}\n\n{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"b"}}\n')
    codec = LineCodec(reader=buf)
    m1 = codec.read_message()
    m2 = codec.read_message()
    m3 = codec.read_message()
    assert m1.params == {"text": "a"}
    assert m2.params == {"text": "b"}
    assert m3 is None  # EOF


def test_read_malformed_json_raises_value_error():
    buf = io.StringIO("not json\n")
    codec = LineCodec(reader=buf)
    with pytest.raises(ValueError, match="invalid JSON"):
        codec.read_message()


def test_read_message_missing_method_raises():
    buf = io.StringIO('{"jsonrpc":"2.0","params":{}}\n')
    codec = LineCodec(reader=buf)
    with pytest.raises(ValueError, match="missing 'method'"):
        codec.read_message()


def test_write_event_flushes_immediately():
    """The codec must flush after every write so the TUI sees events without
    waiting for the buffer to fill — critical for streaming text deltas."""
    import io
    class _TrackingWriter(io.TextIOBase):
        def __init__(self):
            self.flush_count = 0
            self.buf = ""
        def write(self, s):
            self.buf += s
            return len(s)
        def flush(self):
            self.flush_count += 1

    w = _TrackingWriter()
    codec = LineCodec(writer=w)
    codec.write_event(Event.text_delta(text="a"))
    codec.write_event(Event.text_delta(text="b"))
    assert w.flush_count == 2, "must flush after every write_event"


def test_write_event_swallows_broken_pipe():
    """If the TUI process dies (broken pipe), write_event must not raise —
    the agent loop would crash otherwise. Logs instead."""
    class _BrokenWriter:
        def write(self, s):
            raise BrokenPipeError("TUI gone")
        def flush(self):
            raise BrokenPipeError("TUI gone")

    codec = LineCodec(writer=_BrokenWriter())
    # Must not raise
    codec.write_event(Event.text_delta(text="still streaming"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rpc_codec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loom.rpc.codec'`

- [ ] **Step 3: Implement the codec**

```python
# loom/rpc/codec.py
"""Line-based JSON codec for stdio communication between Python core and TUI.

The protocol is JSON Lines (NDJSON): one JSON object per line, terminated
by ``\\n``. This module handles:

- **Writing**: serialize an :class:`Event` / :class:`Request` / :class:`Response`
  to a single line + newline + flush. Thread-safe via a write lock.
- **Reading**: read one line, parse it, skip blank lines, raise on malformed
  input. Returns ``None`` on EOF.
- **Broken pipe handling**: if the TUI process dies mid-stream, writes are
  swallowed (logged) rather than raising — the agent loop must not crash
  just because the user closed the TUI window.
"""

from __future__ import annotations

import threading
from typing import TextIO

from loguru import logger

from loom.rpc.protocol import _Message, Event


class LineCodec:
    """Serialize/deserialize JSON-RPC messages over a text stream.

    Args:
        reader: a text-mode file-like object (typically ``sys.stdin``).
        writer: a text-mode file-like object (typically ``sys.stdout``).
    """

    def __init__(self, reader: TextIO | None = None, writer: TextIO | None = None):
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()

    def write_event(self, event: _Message) -> None:
        """Serialize ``event`` to one JSON line + newline + flush.

        Thread-safe. Swallows BrokenPipeError (TUI died) — the agent loop
        must not crash when the user closes the window mid-stream.
        """
        if self._writer is None:
            return
        line = event.to_jsonl() + "\n"
        with self._write_lock:
            try:
                self._writer.write(line)
                self._writer.flush()
            except (BrokenPipeError, OSError) as exc:
                # TUI process gone — swallow so the agent loop can finish
                # its current turn gracefully. The next read on stdin will
                # return EOF and trigger shutdown.
                logger.debug("codec write failed (TUI gone?): {}", exc)

    def read_message(self) -> _Message | None:
        """Read one JSON-RPC message from the stream.

        Returns:
            The parsed message, or ``None`` on EOF.

        Raises:
            ValueError: if the line is not valid JSON or not a valid
                protocol message.
        """
        if self._reader is None:
            return None
        while True:
            line = self._reader.readline()
            if line == "":  # EOF
                return None
            line = line.strip()
            if not line:
                continue  # skip blank lines
            # Delegate parsing to the protocol module — from_jsonl raises
            # ValueError on malformed input, which we let propagate.
            return _Message.from_jsonl(line)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rpc_codec.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add loom/rpc/codec.py tests/test_rpc_codec.py
git commit -m "feat(rpc): line-based JSON codec for stdio I/O"
```

---

### Task 3: stdio RPC server

**Files:**
- Create: `loom/rpc/server.py`
- Test: `tests/test_rpc_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rpc_server.py
"""Integration tests for the RPC server — spawns `python -m loom.cli serve`
as a subprocess and exchanges JSON Lines over its stdin/stdout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _read_line(proc: subprocess.Popen, timeout: float = 5.0) -> dict | None:
    """Read one JSON line from proc's stdout. Returns None on EOF."""
    # We can't easily do timed reads on pipes in a portable way without
    # select / asyncio; for tests we just read with a deadline.
    import select
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
    """Spawn `python -m loom.cli serve` in a temp workdir with a mocked
    LLM that echoes back a fixed response."""
    # Use the project's existing test fixtures — the agent_loop test
    # helpers already provide a fake LLM. We set env vars that the CLI
    # picks up.
    env = os.environ.copy()
    env["LOOM_TEST_MODE"] = "1"  # server.py checks this to use a stub LLM
    env["LOOM_WORKDIR"] = str(tmp_path)
    monkeypatch.setenv("LOOM_TEST_MODE", "1")

    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.cli", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    try:
        # Wait for the server's ready signal
        first = _read_line(proc, timeout=5.0)
        assert first is not None, "server did not emit ready signal"
        assert first["method"] == "event/session_started"
        yield proc
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_server_emits_session_started_on_launch(rpc_server):
    """The server must emit session_started immediately so the TUI knows
    it's ready to accept user input."""
    # The fixture already consumed the first line; just assert it was
    # the right type.
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
    deadline = time.monotonic() + 5.0
    got_response = False
    got_end = False
    while time.monotonic() < deadline and not got_end:
        line = _read_line(rpc_server, timeout=2.0)
        if line is None:
            break
        events.append(line)
        if line.get("id") == req_id and "result" in line:
            got_response = True
        if line.get("method") == "event/assistant_turn_end":
            got_end = True

    assert got_response, f"missing Response for {req_id}; got: {events}"
    assert any(e["method"] == "event/assistant_turn_start" for e in events), \
        f"missing assistant_turn_start; got: {[e['method'] for e in events]}"
    assert any(e["method"] == "event/text_delta" for e in events), \
        f"missing text_delta; got: {[e['method'] for e in events]}"
    assert got_end, f"missing assistant_turn_end; got: {[e['method'] for e in events]}"


def test_server_handles_cancel(rpc_server):
    """request/cancel must stop the current turn and reply with Response.ok."""
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/send_message",
        "id": "msg-1",
        "params": {"text": "long running task"},
    })
    # Give it a moment to start
    time.sleep(0.1)
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/cancel",
        "id": "cancel-1",
        "params": {},
    })
    # Should get a response to cancel
    deadline = time.monotonic() + 3.0
    got_cancel_response = False
    while time.monotonic() < deadline:
        line = _read_line(rpc_server, timeout=2.0)
        if line is None:
            break
        if line.get("id") == "cancel-1":
            got_cancel_response = True
            break
    assert got_cancel_response, "cancel did not get a response"


def test_server_handles_shutdown(rpc_server):
    """request/shutdown must cause the server to exit cleanly."""
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/shutdown",
        "id": "s-1",
        "params": {},
    })
    # Server should exit within 2 seconds
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if rpc_server.poll() is not None:
            break
        time.sleep(0.1)
    assert rpc_server.poll() is not None, "server did not shut down"


def test_server_permission_prompt_round_trip(rpc_server):
    """When the agent calls a tool that needs permission, the server must:
    1. Send request/permission with a fresh id
    2. Wait for request/permission_response from TUI
    3. Continue or skip the tool based on the decision
    """
    # This test uses a tool that triggers permission (bash with rm)
    _write_line(rpc_server, {
        "jsonrpc": "2.0",
        "method": "request/send_message",
        "id": "perm-1",
        "params": {"text": "run bash rm /tmp/test"},
    })
    # Expect a request/permission message
    deadline = time.monotonic() + 5.0
    perm_request = None
    while time.monotonic() < deadline:
        line = _read_line(rpc_server, timeout=2.0)
        if line is None:
            break
        if line.get("method") == "request/permission":
            perm_request = line
            break
    if perm_request is not None:
        # Reply with allow
        _write_line(rpc_server, {
            "jsonrpc": "2.0",
            "method": "request/permission_response",
            "id": "perm-reply",
            "params": {"request_id": perm_request["id"], "decision": "allow"},
        })
        # Should get a tool_use_started event
        deadline = time.monotonic() + 3.0
        got_tool = False
        while time.monotonic() < deadline:
            line = _read_line(rpc_server, timeout=2.0)
            if line is None:
                break
            if line.get("method") == "event/tool_use_started":
                got_tool = True
                break
        # Note: permission may or may not fire depending on stub LLM
        # behavior — this test is lenient.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rpc_server.py -v`
Expected: FAIL with `error: argument serve: invalid choice`

- [ ] **Step 3: Implement the server**

```python
# loom/rpc/server.py
"""stdio JSON-RPC server that bridges the TUI frontend to loom's agent_loop.

Lifecycle:
1. TUI spawns ``python -m loom.cli serve`` as a child process.
2. Server emits ``event/session_started`` immediately.
3. TUI sends ``request/send_message``; server replies ``Response.ok`` then
   streams events until ``event/assistant_turn_end``.
4. For permission-gated tools, server sends ``request/permission`` and
   blocks until TUI replies with ``request/permission_response``.
5. On ``request/shutdown`` or stdin EOF, server exits cleanly.

The server runs the agent_loop on a worker thread (so the main thread can
read requests from stdin concurrently). Events from the worker thread are
written to stdout via the thread-safe :class:`LineCodec`.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from loom.rpc.codec import LineCodec
from loom.rpc.protocol import Event, Request, Response, _Message


def run_server(workdir: Path | None = None) -> int:
    """Main entry point for the ``loom cli serve`` subcommand.

    Args:
        workdir: the working directory (defaults to cwd).
    """
    if workdir is None:
        workdir = Path.cwd()

    codec = LineCodec(reader=sys.stdin, writer=sys.stdout)
    server = _Server(workdir, codec)
    return server.run()


class _Server:
    """Manages the agent_loop lifecycle + request/event translation."""

    def __init__(self, workdir: Path, codec: LineCodec):
        self.workdir = workdir
        self.codec = codec
        self._shutdown = threading.Event()
        self._agent_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._pending_permissions: dict[str, threading.Event] = {}
        self._permission_responses: dict[str, str] = {}
        self._perm_lock = threading.Lock()
        self._history: list = []
        self._llm_client = None
        self._session_id: str | None = None

    def run(self) -> int:
        """Main loop: read requests, dispatch, emit events."""
        # Late imports to avoid circular deps + respect config loading order
        from loom.agent.config import HarnessConfig, load_config
        from loom.agent.llm import LLMClient
        from loom.agent.loop import apply_config, configure_logging

        configure_logging()
        config = load_config(self.workdir)
        apply_config(config)

        # Initialize LLM client
        model = os.getenv("MODEL") or "deepseek/deepseek-v4-flash"
        self._llm_client = LLMClient(model=model)
        self._session_id = uuid.uuid4().hex[:12]

        # Emit session_started
        self.codec.write_event(Event.session_started(
            session_id=self._session_id, model=model
        ))

        # Main request loop (runs on main thread)
        while not self._shutdown.is_set():
            try:
                msg = self.codec.read_message()
            except ValueError as exc:
                logger.warning("malformed message from TUI: {}", exc)
                self.codec.write_event(Event.error(
                    message=f"malformed message: {exc}"
                ))
                continue
            if msg is None:
                # EOF — TUI closed stdin
                break
            self._dispatch(msg)

        # Wait for agent thread to finish
        if self._agent_thread and self._agent_thread.is_alive():
            self._cancel_event.set()
            self._agent_thread.join(timeout=5.0)

        self.codec.write_event(Event.session_ended(session_id=self._session_id or ""))
        return 0

    def _dispatch(self, msg: _Message) -> None:
        """Route a message to the appropriate handler."""
        if not isinstance(msg, Request):
            return  # We don't expect events from the TUI

        method = msg.method
        if method == "request/send_message":
            self._handle_send_message(msg)
        elif method == "request/cancel":
            self._handle_cancel(msg)
        elif method == "request/permission_response":
            self._handle_permission_response(msg)
        elif method == "request/pick_model":
            self._handle_pick_model(msg)
        elif method == "request/list_sessions":
            self._handle_list_sessions(msg)
        elif method == "request/load_session":
            self._handle_load_session(msg)
        elif method == "request/new_session":
            self._handle_new_session(msg)
        elif method == "request/shutdown":
            self._handle_shutdown(msg)
        else:
            self.codec.write_event(Response.error_response(
                id=msg.id, code=-32601,
                message=f"unknown method: {method}"
            ))

    def _build_callbacks(self) -> dict:
        """Build the agent_loop callbacks dict that emits RPC events."""
        codec = self.codec

        def _emit(event: Event) -> None:
            codec.write_event(event)

        return {
            "on_message_start": lambda: _emit(Event.assistant_turn_start(agent_name="织轴")),
            "on_assistant_message_start": lambda: _emit(Event.assistant_turn_start(agent_name="织轴")),
            "on_text_delta": lambda chunk: _emit(Event.text_delta(text=chunk)),
            "on_thinking_delta": lambda chunk: _emit(Event.thinking_delta(text=chunk)),
            "on_tool_use": lambda name, inp, uid: _emit(
                Event.tool_use_started(tool_name=name, tool_input=inp, tool_use_id=uid)
            ),
            "on_tool_result": lambda uid, out, err: _emit(
                Event.tool_use_completed(tool_use_id=uid, output=out, is_error=err)
            ),
            "on_compact": lambda before, after: _emit(
                Event.compact_occurred(msgs_before=before, msgs_after=after)
            ),
            "on_message_end": lambda calls, turns: _emit(
                Event.assistant_turn_end(
                    tool_calls=calls, total_messages=len(self._history),
                    duration=0.0,  # agent_loop doesn't expose this; TUI can time it
                )
            ),
            "on_todo_update": lambda todos: _emit(Event.todo_update(todos=list(todos))),
            "on_subagent_start": lambda sid, desc, agent_name="织针": _emit(
                Event.subagent_start(subagent_id=sid, description=desc, agent_name=agent_name)
            ),
            "on_subagent_end": lambda sid, elapsed, state: _emit(
                Event.subagent_end(subagent_id=sid, elapsed=elapsed, state=state)
            ),
        }

    def _make_rpc_asker(self) -> Any:
        """Build a permission asker that routes through RPC instead of Textual."""
        def asker(tool_name: str, args: dict, reason: str) -> str:
            perm_id = uuid.uuid4().hex[:8]
            event = Response.permission_request(
                id=perm_id, tool_name=tool_name,
                tool_input=args, reason=reason
            )
            self.codec.write_event(event)
            # Wait for the TUI's permission_response
            done = threading.Event()
            with self._perm_lock:
                self._pending_permissions[perm_id] = done
            done.wait(timeout=300.0)  # 5 min timeout
            with self._perm_lock:
                self._pending_permissions.pop(perm_id, None)
                decision = self._permission_responses.pop(perm_id, "deny")
            return decision
        return asker

    def _handle_send_message(self, req: Request) -> None:
        """Reply immediately, then run agent_loop on a worker thread."""
        # Ack the request
        self.codec.write_event(Response.ok(id=req.id, result={"ack": True}))

        text = req.params.get("text", "")
        self._history.append({"role": "user", "content": text})
        self._cancel_event.clear()

        # Wire the RPC asker into hooks
        from loom.agent import hooks as hooks_mod
        from loom.agent.loop import hooks as loop_hooks
        loop_hooks._asker = self._make_rpc_asker()

        callbacks = self._build_callbacks()

        def _run_agent():
            from loom.agent.loop import agent_loop
            try:
                agent_loop(
                    self._history,
                    self._llm_client,
                    callbacks,
                    self._llm_client.stream_iter,
                    self._session_id,
                )
            except Exception as exc:
                self.codec.write_event(Event.error(
                    message=f"agent crashed: {type(exc).__name__}: {exc}"
                ))
            finally:
                # Signal turn end if agent_loop didn't (e.g. on exception)
                pass

        self._agent_thread = threading.Thread(
            target=_run_agent, name="agent-loop", daemon=True
        )
        self._agent_thread.start()

    def _handle_cancel(self, req: Request) -> None:
        self._cancel_event.set()
        self.codec.write_event(Response.ok(id=req.id, result={"cancelled": True}))

    def _handle_permission_response(self, req: Request) -> None:
        request_id = req.params.get("request_id", "")
        decision = req.params.get("decision", "deny")
        with self._perm_lock:
            done = self._pending_permissions.get(request_id)
            self._permission_responses[request_id] = decision
        if done is not None:
            done.set()
        # No response needed for a notification-style message, but we
        # ack anyway for consistency.
        self.codec.write_event(Response.ok(id=req.id, result={"ack": True}))

    def _handle_pick_model(self, req: Request) -> None:
        model = req.params.get("model", "")
        if model and self._llm_client is not None:
            self._llm_client.model = model
        self.codec.write_event(Response.ok(id=req.id, result={"model": model}))

    def _handle_list_sessions(self, req: Request) -> None:
        from loom.agent.session_store import SessionStore
        store = SessionStore(self.workdir)
        sessions = store.list_sessions()
        self.codec.write_event(Response.ok(id=req.id, result={"sessions": sessions}))

    def _handle_load_session(self, req: Request) -> None:
        from loom.agent.session_store import SessionStore
        session_id = req.params.get("session_id", "")
        store = SessionStore(self.workdir)
        loaded = store.load_session(session_id)
        if loaded is not None:
            self._history = loaded.messages
            self._session_id = session_id
            self.codec.write_event(Response.ok(id=req.id, result={"loaded": True}))
        else:
            self.codec.write_event(Response.error_response(
                id=req.id, code=-32602, message=f"session not found: {session_id}"
            ))

    def _handle_new_session(self, req: Request) -> None:
        self._history = []
        self._session_id = uuid.uuid4().hex[:12]
        self.codec.write_event(Response.ok(id=req.id, result={
            "session_id": self._session_id
        }))

    def _handle_shutdown(self, req: Request) -> None:
        self.codec.write_event(Response.ok(id=req.id, result={"shutting_down": True}))
        self._shutdown.set()
```

- [ ] **Step 4: Add the `serve` subcommand to `loom/cli.py`**

Modify `loom/cli.py` to add the `serve` subcommand. Find the `run_p` subparser definition (around line 92) and add:

```python
    serve_p = sub.add_parser("serve", help="Run the stdio JSON-RPC server for an external TUI frontend")
    serve_p.set_defaults(func="serve")
```

Then in the dispatch section (around line 207), add before the `run` handler:

```python
    if args.command == "serve":
        from loom.rpc.server import run_server
        return run_server()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_rpc_server.py -v`
Expected: PASS (4 tests — permission round-trip is lenient)

- [ ] **Step 6: Commit**

```bash
git add loom/rpc/server.py loom/cli.py tests/test_rpc_server.py
git commit -m "feat(rpc): stdio JSON-RPC server bridging TUI to agent_loop"
```

---

### Task 4: Manual smoke test of the Python RPC server

**Files:**
- No new files — just manual verification

- [ ] **Step 1: Start the server and send a message by hand**

```bash
# In one terminal:
echo '{"jsonrpc":"2.0","method":"request/send_message","id":"r1","params":{"text":"hello"}}' | uv run python -m loom.cli serve
```

Expected: a stream of JSON Lines on stdout, starting with `event/session_started`, then `Response.ok` for `r1`, then `event/assistant_turn_start`, then one or more `event/text_delta`, then `event/assistant_turn_end`.

- [ ] **Step 2: Verify shutdown works**

```bash
echo '{"jsonrpc":"2.0","method":"request/shutdown","id":"s1","params":{}}' | uv run python -m loom.cli serve
```

Expected: server emits `Response.ok` for `s1` then exits with code 0.

- [ ] **Step 3: Verify graceful handling of malformed input**

```bash
echo 'not json' | uv run python -m loom.cli serve
```

Expected: server emits `event/error` with message about malformed JSON, then continues running (doesn't crash).

- [ ] **Step 4: Commit the smoke test results to progress.md**

No code change — just verify the server works end-to-end before moving to the Go side.

---

## Phase 2: Go TUI Skeleton

**Goal:** A minimal Go binary that spawns the Python server, renders a basic chat interface, sends messages, and displays streamed responses. No fancy features (no thinking display, no tool call markers, no sessions) — just prove the end-to-end pipeline works.

### Task 5: Initialize the Go module and project structure

**Files:**
- Create: `tui-go/go.mod`
- Create: `tui-go/main.go`
- Create: `tui-go/protocol/protocol.go`
- Create: `tui-go/rpc/rpc.go`

- [ ] **Step 1: Initialize the Go module**

```bash
mkdir -p tui-go/protocol tui-go/rpc tui-go/model tui-go/view tui-go/markdown
cd tui-go
go mod init github.com/lanf/loom-tui
go get github.com/charmbracelet/bubbletea
go get github.com/charmbracelet/lipgloss
go get github.com/charmbracelet/glamour
go get github.com/charmbracelet/bubbles/textarea
go get github.com/charmbracelet/bubbles/viewport
```

- [ ] **Step 2: Write the protocol mirror**

```go
// tui-go/protocol/protocol.go
// Package protocol mirrors loom/rpc/protocol.py — the JSON-RPC message
// types shared between the Python core and this Go TUI.
package protocol

import "encoding/json"

// Event is a streamed event from Python to TUI (no response expected).
type Event struct {
	Jsonrpc string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

// Request is a request from TUI to Python (expects a Response).
type Request struct {
	Jsonrpc string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	ID      string          `json:"id"`
	Params  map[string]any  `json:"params"`
}

// Response is a reply from Python to TUI (matches a prior Request by ID).
type Response struct {
	Jsonrpc string          `json:"jsonrpc"`
	ID      string          `json:"id"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *RPCError       `json:"error,omitempty"`
	Method  string          `json:"method,omitempty"` // set for server-initiated requests
	Params  json.RawMessage `json:"params,omitempty"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Event method constants — must match loom/rpc/protocol.py EVENT_TYPES.
const (
	EventAssistantTurnStart = "event/assistant_turn_start"
	EventAssistantTurnEnd    = "event/assistant_turn_end"
	EventTextDelta           = "event/text_delta"
	EventThinkingDelta       = "event/thinking_delta"
	EventToolUseStarted      = "event/tool_use_started"
	EventToolUseCompleted    = "event/tool_use_completed"
	EventCompactOccurred     = "event/compact_occurred"
	EventTodoUpdate          = "event/todo_update"
	EventSubagentStart       = "event/subagent_start"
	EventSubagentEnd         = "event/subagent_end"
	EventShowNotification    = "event/show_notification"
	EventSessionStarted      = "event/session_started"
	EventSessionEnded        = "event/session_ended"
	EventError               = "event/error"
)

// Request method constants — must match loom/rpc/protocol.py REQUEST_TYPES.
const (
	RequestMethodSendMessage        = "request/send_message"
	RequestMethodCancel             = "request/cancel"
	RequestMethodPermissionResponse = "request/permission_response"
	RequestMethodPickModel          = "request/pick_model"
	RequestMethodListSessions       = "request/list_sessions"
	RequestMethodLoadSession        = "request/load_session"
	RequestMethodNewSession         = "request/new_session"
	RequestMethodShutdown           = "request/shutdown"
)

// Event param structs for typed access.
type TextDeltaParams struct {
	Text string `json:"text"`
}

type ToolUseStartedParams struct {
	ToolName   string         `json:"tool_name"`
	ToolInput  map[string]any `json:"tool_input"`
	ToolUseID  string         `json:"tool_use_id"`
}

type ToolUseCompletedParams struct {
	ToolUseID string `json:"tool_use_id"`
	Output    string `json:"output"`
	IsError   bool   `json:"is_error"`
}

type SessionStartedParams struct {
	SessionID string `json:"session_id"`
	Model     string `json:"model"`
}

// NewSendMessage creates a send_message request.
func NewSendMessage(id, text string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodSendMessage,
		ID:      id,
		Params:  map[string]any{"text": text},
	}
}

// NewShutdown creates a shutdown request.
func NewShutdown(id string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodShutdown,
		ID:      id,
		Params:  map[string]any{},
	}
}
```

- [ ] **Step 3: Write the RPC client**

```go
// tui-go/rpc/rpc.go
// Package rpc manages the JSON-RPC connection to the Python loom server.
package rpc

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/lanf/loom-tui/protocol"
)

// Client is a JSON-RPC client that talks to the Python loom server over
// the child process's stdin/stdout.
type Client struct {
	stdin  io.WriteCloser
	stdout io.Reader
	mu     sync.Mutex // guards stdin writes
	nextID int
	// pending maps request ID -> channel that receives the Response
	pending   map[string]chan protocol.Response
	pendingMu sync.Mutex
	// Events from the server (streamed, no response) are sent to eventCh
	eventCh chan protocol.Event
}

// NewClient creates a new RPC client.
func NewClient(stdin io.WriteCloser, stdout io.Reader) *Client {
	c := &Client{
		stdin:   stdin,
		stdout:  stdout,
		pending: make(map[string]chan protocol.Response),
		eventCh: make(chan protocol.Event, 1000),
	}
	go c.readLoop()
	return c
}

// Events returns a channel of streamed events from the server.
func (c *Client) Events() <-chan protocol.Event {
	return c.eventCh
}

// Send sends a request and waits for the response (with timeout).
func (c *Client) Send(req protocol.Request, timeout time.Duration) (protocol.Response, error) {
	if req.ID == "" {
		req.ID = uuid.NewString()[:8]
	}
	respCh := make(chan protocol.Response, 1)
	c.pendingMu.Lock()
	c.pending[req.ID] = respCh
	c.pendingMu.Unlock()
	defer func() {
		c.pendingMu.Lock()
		delete(c.pending, req.ID)
		c.pendingMu.Unlock()
	}()

	if err := c.write(req); err != nil {
		return protocol.Response{}, err
	}

	select {
	case resp := <-respCh:
		return resp, nil
	case <-time.After(timeout):
		return protocol.Response{}, fmt.Errorf("request %s timed out after %s", req.ID, timeout)
	}
}

// SendNoWait sends a request without waiting for a response (fire-and-forget).
func (c *Client) SendNoWait(req protocol.Request) error {
	if req.ID == "" {
		req.ID = uuid.NewString()[:8]
	}
	return c.write(req)
}

func (c *Client) write(msg any) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	c.mu.Lock()
	defer c.mu.Unlock()
	_, err = c.stdin.Write(data)
	return err
}

func (c *Client) readLoop() {
	scanner := bufio.NewScanner(c.stdout)
	// Increase buffer size for large messages (tool results can be big)
	scanner.Buffer(make([]byte, 0, 1024*1024), 10*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		// Try to parse as Response first (has "id" and "result"/"error")
		var resp protocol.Response
		if err := json.Unmarshal(line, &resp); err == nil && resp.ID != "" && (resp.Result != nil || resp.Error != nil || resp.Method != "") {
			// It's a response — dispatch to the waiting sender
			c.pendingMu.Lock()
			ch, ok := c.pending[resp.ID]
			c.pendingMu.Unlock()
			if ok {
				select {
				case ch <- resp:
				default:
					log.Printf("rpc: dropped response for %s (channel full)", resp.ID)
				}
			} else {
				// No pending request — could be a server-initiated permission request
				// Re-dispatch as an event for the UI to handle.
				ev := protocol.Event{
					Jsonrpc: "2.0",
					Method:  resp.Method,
					Params:  resp.Params,
				}
				select {
				case c.eventCh <- ev:
				default:
					log.Printf("rpc: dropped server-initiated request %s", resp.Method)
				}
			}
			continue
		}
		// Otherwise it's an event
		var ev protocol.Event
		if err := json.Unmarshal(line, &ev); err != nil {
			log.Printf("rpc: failed to parse line: %v", err)
			continue
		}
		select {
		case c.eventCh <- ev:
		default:
			log.Printf("rpc: dropped event %s (channel full)", ev.Method)
		}
	}
	if err := scanner.Err(); err != nil {
		log.Printf("rpc: scanner error: %v", err)
	}
	close(c.eventCh)
}

// Close closes the stdin pipe to the Python server.
func (c *Client) Close() error {
	return c.stdin.Close()
}
```

- [ ] **Step 4: Write the minimal Bubble Tea main**

```go
// tui-go/main.go
package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lanf/loom-tui/protocol"
	"github.com/lanf/loom-tui/rpc"
)

func main() {
	// Spawn the Python server
	cmd := exec.Command("python", "-m", "loom.cli", "serve")
	cmd.Stderr = os.Stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		log.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		log.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		log.Fatalf("failed to start loom server: %v", err)
	}
	defer func() {
		stdin.Close()
		cmd.Wait()
	}()

	client := rpc.NewClient(stdin, stdout)

	p := tea.NewProgram(initialModel(client), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		log.Fatal(err)
	}
	// Send shutdown on exit
	client.SendNoWait(protocol.NewShutdown("exit"))
}

type model struct {
	client   *rpc.Client
	messages []string
	input    string
	ready    bool
	streaming bool
}

func initialModel(client *rpc.Client) model {
	return model{
		client: client,
	}
}

func (m model) Init() tea.Cmd {
	return listenForEvents(m.client)
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyCtrlC:
			return m, tea.Quit
		case tea.KeyEnter:
			if m.input != "" {
				text := m.input
				m.input = ""
				m.streaming = true
				return m, sendMessage(m.client, text)
			}
		case tea.KeyBackspace:
			if len(m.input) > 0 {
				m.input = m.input[:len(m.input)-1]
			}
		default:
			if msg.String() != "" && msg.String() != " " {
				m.input += msg.String()
			} else if msg.String() == " " {
				m.input += " "
			}
		}
	case eventMsg:
		switch msg.event.Method {
		case protocol.EventSessionStarted:
			m.ready = true
		case protocol.EventTextDelta:
			var p protocol.TextDeltaParams
			if err := json.Unmarshal(msg.event.Params, &p); err == nil {
				if len(m.messages) == 0 || m.streaming {
					if len(m.messages) == 0 {
						m.messages = append(m.messages, "")
					}
					m.messages[len(m.messages)-1] += p.Text
				}
			}
		case protocol.EventAssistantTurnStart:
			m.streaming = true
			m.messages = append(m.messages, "")
		case protocol.EventAssistantTurnEnd:
			m.streaming = false
		}
		return m, listenForEvents(m.client)
	case sentMsg:
		// Message was sent; nothing to do
		return m, nil
	}
	return m, nil
}

func (m model) View() string {
	if !m.ready {
		return "Connecting to loom...\n"
	}
	var s string
	s += lipgloss.NewStyle().Bold(true).Render("loom") + "\n\n"
	for _, msg := range m.messages {
		s += msg + "\n\n"
	}
	if m.streaming {
		s += "▎"
	}
	s += "\n> " + m.input + "_"
	return s
}

// Messages

type eventMsg struct{ event protocol.Event }
type sentMsg struct{}

func listenForEvents(client *rpc.Client) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-client.Events()
		if !ok {
			return tea.Quit()
		}
		return eventMsg{event: ev}
	}
}

func sendMessage(client *rpc.Client, text string) tea.Cmd {
	return func() tea.Msg {
		_, err := client.Send(protocol.NewSendMessage("", text), 5*time.Second)
		if err != nil {
			log.Printf("send failed: %v", err)
		}
		return sentMsg{}
	}
}
```

- [ ] **Step 5: Build and run**

```bash
cd tui-go
go build -o loom-tui .
./loom-tui
```

Expected: A basic terminal UI that shows "Connecting to loom..." then a prompt. Type a message + Enter, see the streamed response appear.

- [ ] **Step 6: Commit**

```bash
git add tui-go/
git commit -m "feat(tui-go): minimal Bubble Tea skeleton with RPC to Python core"
```

---

## Phase 3: Feature Parity with Textual TUI

**Goal:** Bring the Go TUI to feature parity with the current Textual TUI — Markdown rendering, thinking display, tool call markers, status bar, header, permission modal, session picker.

### Task 6: Markdown rendering with Glamour

**Files:**
- Create: `tui-go/markdown/renderer.go`

- [ ] **Step 1: Write the renderer**

```go
// tui-go/markdown/renderer.go
// Package markdown wraps Glamour for CJK-aware Markdown rendering.
package markdown

import (
	"github.com/charmbracelet/glamour"
)

var renderer *glamour.TermRenderer

func init() {
	r, err := glamour.NewTermRenderer(
		glamour.WithAutoStyle(),
		glamour.WithWordWrap(80),
		// CJK-aware: Glamour uses go-runewidth for display width
		// calculation, which handles CJK double-width correctly.
	)
	if err != nil {
		panic(err)
	}
	renderer = r
}

// Render converts Markdown to ANSI-styled text.
func Render(md string) (string, error) {
	return renderer.Render(md)
}
```

- [ ] **Step 2: Wire into the chat view**

Integrate into `view/chatlog.go` (next task) — assistant messages are rendered through `markdown.Render()` before display.

- [ ] **Step 3: Verify CJK rendering works**

```bash
cd tui-go && go test ./markdown/ -v -run TestRender
```

Write a quick test that renders `# 你好世界` and asserts the output contains `你好世界` (not broken into per-char lines).

- [ ] **Step 4: Commit**

```bash
git add tui-go/markdown/
git commit -m "feat(tui-go): Glamour-based Markdown renderer with CJK support"
```

---

### Task 7: ChatLog viewport with streaming + tool markers

**Files:**
- Create: `tui-go/view/chatlog.go`
- Create: `tui-go/view/composer.go`
- Create: `tui-go/view/statusbar.go`

- [ ] **Step 1: Implement the ChatLog viewport**

```go
// tui-go/view/chatlog.go
// Package view implements the TUI widgets.
package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lanf/loom-tui/markdown"
	"github.com/lanf/loom-tui/protocol"
)

type ChatLog struct {
	viewport   viewport.Model
	lines      []chatLine
	streamBuf  strings.Builder
	thinking   strings.Builder
	toolCalls  []toolCall
}

type chatLine struct {
	role    string // "user", "assistant", "tool", "thinking", "system"
	content string
}

type toolCall struct {
	id      string
	name    string
	input   map[string]any
	output  string
	isError bool
	done    bool
}

func NewChatLog(width, height int) *ChatLog {
	vp := viewport.New(width, height)
	return &ChatLog{viewport: vp}
}

func (c *ChatLog) AppendUserMessage(text string) {
	c.lines = append(c.lines, chatLine{role: "user", content: text})
	c.render()
}

func (c *ChatLog) StartAssistantTurn() {
	c.streamBuf.Reset()
	c.lines = append(c.lines, chatLine{role: "assistant", content: ""})
}

func (c *ChatLog) AppendTextDelta(text string) {
	c.streamBuf.WriteString(text)
	if len(c.lines) > 0 && c.lines[len(c.lines)-1].role == "assistant" {
		c.lines[len(c.lines)-1].content = c.streamBuf.String()
	}
	c.render()
}

func (c *ChatLog) AppendThinkingDelta(text string) {
	c.thinking.WriteString(text)
}

func (c *ChatLog) StartToolCall(name string, input map[string]any, id string) {
	c.toolCalls = append(c.toolCalls, toolCall{
		id: id, name: name, input: input,
	})
	// Add a placeholder line that shows the tool call collapsed
	c.lines = append(c.lines, chatLine{
		role:    "tool",
		content: fmt.Sprintf("◐ %s(%v)", name, input),
	})
	c.render()
}

func (c *ChatLog) CompleteToolCall(id string, output string, isError bool) {
	for i := range c.toolCalls {
		if c.toolCalls[i].id == id {
			c.toolCalls[i].output = output
			c.toolCalls[i].isError = isError
			c.toolCalls[i].done = true
			break
		}
	}
	// Update the tool line to show completion
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "tool" && strings.Contains(c.lines[i].content, id) {
			c.lines[i].content = fmt.Sprintf("✓ %s → %s", c.toolCalls[len(c.toolCalls)-1].name,
				truncate(output, 100))
			break
		}
	}
	c.render()
}

func (c *ChatLog) render() {
	var b strings.Builder
	for _, line := range c.lines {
		switch line.role {
		case "user":
			b.WriteString(userStyle.Render("You: " + line.content) + "\n\n")
		case "assistant":
			rendered, err := markdown.Render(line.content)
			if err != nil {
				b.WriteString(line.content + "\n\n")
			} else {
				b.WriteString(rendered)
			}
		case "tool":
			b.WriteString(toolStyle.Render(line.content) + "\n")
		case "system":
			b.WriteString(systemStyle.Render(line.content) + "\n\n")
		}
	}
	c.viewport.SetContent(b.String())
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

var (
	userStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("36"))
	toolStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("33")).Italic(true)
	systemStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("90")).Italic(true)
)
```

- [ ] **Step 2: Implement the Composer (input box)**

```go
// tui-go/view/composer.go
package view

import (
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
)

type Composer struct {
	textarea textarea.Model
}

func NewComposer() *Composer {
	ta := textarea.New()
	ta.Placeholder = "Send a message..."
	ta.Focus()
	return &Composer{textarea: ta}
}

func (c *Composer) Value() string {
	return c.textarea.Value()
}

func (c *Composer) Reset() {
	c.textarea.Reset()
}

func (c *Composer) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	return c.textarea.Update(msg)
}

func (c *Composer) View() string {
	return c.textarea.View()
}

func (c *Composer) HandleKey(msg tea.KeyMsg) (string, bool) {
	// Shift+Enter for newline, Enter to send
	if msg.Type == tea.KeyEnter {
		text := c.textarea.Value()
		if text != "" {
			c.textarea.Reset()
			return text, true
		}
	}
	return "", false
}
```

- [ ] **Step 3: Implement the StatusBar**

```go
// tui-go/view/statusbar.go
package view

import (
	"fmt"

	"github.com/charmbracelet/lipgloss"
)

type StatusBar struct {
	model    string
	tokens   int
	branch   string
	ready    bool
}

func NewStatusBar() *StatusBar {
	return &StatusBar{model: "unknown"}
}

func (s *StatusBar) SetModel(model string)    { s.model = model }
func (s *StatusBar) SetTokens(tokens int)     { s.tokens = tokens }
func (s *StatusBar) SetBranch(branch string)  { s.branch = branch }
func (s *StatusBar) SetReady(ready bool)      { s.ready = ready }

func (s *StatusBar) View() string {
	status := "●"
	if !s.ready {
		status = "○"
	}
	return fmt.Sprintf("%s %s | %d tokens | %s",
		status, s.model, s.tokens, s.branch)
}
```

- [ ] **Step 4: Update main.go to use these views**

Refactor `main.go` to compose ChatLog + Composer + StatusBar into a layout. Send messages from Composer on Enter, route events to ChatLog.

- [ ] **Step 5: Build and test**

```bash
cd tui-go && go build -o loom-tui . && ./loom-tui
```

Expected: Full chat UI with Markdown rendering, streaming text, tool call markers, status bar.

- [ ] **Step 6: Commit**

```bash
git add tui-go/view/
git commit -m "feat(tui-go): ChatLog viewport with Markdown + streaming + tool markers"
```

---

### Task 8: Permission modal

**Files:**
- Create: `tui-go/view/permission.go`
- Modify: `tui-go/main.go`

- [ ] **Step 1: Implement the permission modal**

```go
// tui-go/view/permission.go
package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

type PermissionModal struct {
	toolName string
	args     map[string]any
	reason   string
	visible  bool
}

func NewPermissionModal() *PermissionModal {
	return &PermissionModal{}
}

func (p *PermissionModal) Show(toolName string, args map[string]any, reason string) {
	p.toolName = toolName
	p.args = args
	p.reason = reason
	p.visible = true
}

func (p *PermissionModal) Hide() {
	p.visible = false
}

func (p *PermissionModal) Visible() bool {
	return p.visible
}

func (p *PermissionModal) View() string {
	if !p.visible {
		return ""
	}
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Bold(true).Render("⚠ Permission Required") + "\n\n")
	b.WriteString(fmt.Sprintf("Tool: %s\n", p.toolName))
	b.WriteString(fmt.Sprintf("Args: %v\n", p.args))
	if p.reason != "" {
		b.WriteString(fmt.Sprintf("Reason: %s\n", p.reason))
	}
	b.WriteString("\n[Y] Allow  [N] Deny  [Escape] Cancel")
	return lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		Padding(1, 2).
		Render(b.String())
}
```

- [ ] **Step 2: Wire into main model — when a `request/permission` event arrives, show the modal; on Y/N, send `permission_response`**

- [ ] **Step 3: Test with a tool that requires permission (bash with rm)**

- [ ] **Step 4: Commit**

```bash
git add tui-go/view/permission.go tui-go/main.go
git commit -m "feat(tui-go): permission modal with Y/N prompt"
```

---

### Task 9: Header with section toggles

**Files:**
- Create: `tui-go/view/header.go`
- Modify: `tui-go/main.go`

- [ ] **Step 1: Implement the header**

Render a header bar with clickable sections: Todos, Subagents, Sessions, Models. Toggling a section sends the appropriate RPC request and displays results inline.

- [ ] **Step 2: Wire to RPC (list_sessions, etc.)**

- [ ] **Step 3: Commit**

---

### Task 10: Thinking display + subagent markers

**Files:**
- Modify: `tui-go/view/chatlog.go`

- [ ] **Step 1: Add thinking display**

When `event/thinking_delta` arrives, accumulate into a collapsible section above the assistant message. Display as dimmed text with a `▎` prefix. Collapse on `assistant_turn_end`.

- [ ] **Step 2: Add subagent markers**

When `event/subagent_start` arrives, display a `◐ 织针: <description>` line. When `event/subagent_end` arrives, update to `✓ 织针: <elapsed>s` or `✗ 织针: error`.

- [ ] **Step 3: Commit**

---

## Phase 4: Integration + Migration

**Goal:** Wire the Go TUI as the default `loom run` experience, keep `loom run --plain` as CLI fallback, update docs.

### Task 11: Make `loom run` launch the Go TUI

**Files:**
- Modify: `loom/cli.py`
- Create: `scripts/build-tui.sh`

- [ ] **Step 1: Add build script**

```bash
# scripts/build-tui.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../tui-go"
go build -o ../bin/loom-tui .
echo "Built bin/loom-tui"
```

- [ ] **Step 2: Modify cli.py to prefer Go TUI**

```python
# loom/cli.py — in the "run" command handler:
if args.command == "run":
    if getattr(args, "plain", False):
        from loom.agent import run_repl
        run_repl(resume=args.resume)
        return 0
    # Prefer Go TUI if the binary exists
    import shutil
    go_tui = shutil.which("loom-tui") or str(Path(__file__).parent.parent / "bin" / "loom-tui")
    if Path(go_tui).exists():
        os.execvp(go_tui, [go_tui])
        return 0  # unreachable
    # Fall back to Textual TUI
    from loom.tui.app import AgentTUIApp
    AgentTUIApp(resume=args.resume, model=args.model).run()
    return 0
```

- [ ] **Step 3: Update Makefile / init.sh to build the Go binary**

- [ ] **Step 4: Commit**

---

### Task 12: Update feature_list.json + progress.md

**Files:**
- Modify: `feature_list.json`
- Modify: `progress.md`

- [ ] **Step 1: Add the feature entry**

- [ ] **Step 2: Append session summary to progress.md**

- [ ] **Step 3: Run full verification**

```bash
./init.sh  # must still pass (Python tests unaffected)
cd tui-go && go test ./...  # Go tests
```

- [ ] **Step 4: Commit**

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] `uv run pytest tests/test_rpc_protocol.py tests/test_rpc_codec.py tests/test_rpc_server.py -v` — all pass
- [ ] `uv run pytest -q` — all 563+ existing tests still pass (zero regression)
- [ ] `uv run python -m loom.cli serve` — starts and responds to JSON requests
- [ ] `cd tui-go && go build -o bin/loom-tui .` — compiles
- [ ] `./bin/loom-tui` — launches, connects to Python server, can send/receive messages
- [ ] CJK text renders without per-character line breaks
- [ ] Tool calls display as markers with expand/collapse
- [ ] Thinking display shows extended-thinking content
- [ ] Permission modal appears for destructive tools
- [ ] Session picker lists and switches sessions
- [ ] `loom run --plain` still works as CLI fallback
