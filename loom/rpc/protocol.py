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
(``tui-go/protocol/protocol.go``) mirrors these definitions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

# All event methods (Python -> TUI, fire-and-forget).
# Every entry must have a matching factory method on ``Event``.
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
    def from_jsonl(line: str) -> _Message | Request | Response | Event:
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
    def _from_dict(d: dict[str, Any]) -> Event:
        return Event(method=d["method"], params=d.get("params", {}))

    # ---- Factory methods (one per event type) ----

    @staticmethod
    def assistant_turn_start(agent_name: str = "织轴") -> Event:
        return Event(method="event/assistant_turn_start",
                     params={"agent_name": agent_name})

    @staticmethod
    def assistant_turn_end(tool_calls: int, total_messages: int, duration: float) -> Event:
        return Event(method="event/assistant_turn_end",
                     params={"tool_calls": tool_calls,
                             "total_messages": total_messages,
                             "duration": duration})

    @staticmethod
    def text_delta(text: str) -> Event:
        return Event(method="event/text_delta", params={"text": text})

    @staticmethod
    def thinking_delta(text: str) -> Event:
        return Event(method="event/thinking_delta", params={"text": text})

    @staticmethod
    def tool_use_started(tool_name: str, tool_input: dict, tool_use_id: str) -> Event:
        return Event(method="event/tool_use_started",
                     params={"tool_name": tool_name,
                             "tool_input": tool_input,
                             "tool_use_id": tool_use_id})

    @staticmethod
    def tool_use_completed(tool_use_id: str, output: str, is_error: bool) -> Event:
        return Event(method="event/tool_use_completed",
                     params={"tool_use_id": tool_use_id,
                             "output": output,
                             "is_error": is_error})

    @staticmethod
    def compact_occurred(msgs_before: int, msgs_after: int) -> Event:
        return Event(method="event/compact_occurred",
                     params={"msgs_before": msgs_before, "msgs_after": msgs_after})

    @staticmethod
    def todo_update(todos: list) -> Event:
        return Event(method="event/todo_update", params={"todos": todos})

    @staticmethod
    def subagent_start(subagent_id: str, description: str, agent_name: str = "织针") -> Event:
        return Event(method="event/subagent_start",
                     params={"subagent_id": subagent_id,
                             "description": description,
                             "agent_name": agent_name})

    @staticmethod
    def subagent_end(subagent_id: str, elapsed: float, state: str) -> Event:
        return Event(method="event/subagent_end",
                     params={"subagent_id": subagent_id,
                             "elapsed": elapsed,
                             "state": state})

    @staticmethod
    def show_notification(text: str, severity: str = "info") -> Event:
        return Event(method="event/show_notification",
                     params={"text": text, "severity": severity})

    @staticmethod
    def session_started(session_id: str, model: str) -> Event:
        return Event(method="event/session_started",
                     params={"session_id": session_id, "model": model})

    @staticmethod
    def session_ended(session_id: str) -> Event:
        return Event(method="event/session_ended",
                     params={"session_id": session_id})

    @staticmethod
    def error(message: str, code: int = -32000) -> Event:
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
    def _from_dict(d: dict[str, Any]) -> Request:
        return Request(method=d["method"], id=d.get("id", ""),
                       params=d.get("params", {}))

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:8]

    # ---- Factory methods ----

    @staticmethod
    def send_message(id: str | None = None, text: str = "") -> Request:
        return Request(method="request/send_message",
                       id=id or Request._new_id(),
                       params={"text": text})

    @staticmethod
    def cancel(id: str | None = None) -> Request:
        return Request(method="request/cancel",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def permission_response(id: str | None = None, request_id: str = "",
                            decision: str = "deny") -> Request:
        return Request(method="request/permission_response",
                       id=id or Request._new_id(),
                       params={"request_id": request_id, "decision": decision})

    @staticmethod
    def pick_model(id: str | None = None, model: str = "") -> Request:
        return Request(method="request/pick_model",
                       id=id or Request._new_id(),
                       params={"model": model})

    @staticmethod
    def list_sessions(id: str | None = None) -> Request:
        return Request(method="request/list_sessions",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def load_session(id: str | None = None, session_id: str = "") -> Request:
        return Request(method="request/load_session",
                       id=id or Request._new_id(),
                       params={"session_id": session_id})

    @staticmethod
    def new_session(id: str | None = None) -> Request:
        return Request(method="request/new_session",
                       id=id or Request._new_id(),
                       params={})

    @staticmethod
    def shutdown(id: str | None = None) -> Request:
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

    Note: the JSON field is ``error`` (per JSON-RPC spec) but the dataclass
    attribute is ``error_payload`` to avoid clashing with the
    :meth:`error` factory method.
    """

    id: str = ""
    result: Any = None
    error_payload: dict[str, Any] | None = None
    method: str = ""  # set only for server-initiated requests
    params: dict[str, Any] = field(default_factory=dict)

    def _to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id}
        if self.error_payload is not None:
            d["error"] = self.error_payload
        elif self.method:
            # Server-initiated request (has method + params, no result)
            d["method"] = self.method
            d["params"] = self.params
        else:
            d["result"] = self.result
        return d

    @staticmethod
    def _from_dict(d: dict[str, Any]) -> Response:
        return Response(
            id=d.get("id", ""),
            result=d.get("result"),
            error_payload=d.get("error"),
            method=d.get("method", ""),
            params=d.get("params", {}),
        )

    @staticmethod
    def ok(id: str, result: Any = None) -> Response:
        return Response(id=id, result=result)

    @staticmethod
    def error(id: str, code: int, message: str) -> Response:
        return Response(id=id, error_payload={"code": code, "message": message})

    @staticmethod
    def permission_request(id: str, tool_name: str, tool_input: dict,
                           reason: str) -> Response:
        """Server-initiated: asks TUI to prompt the user for permission."""
        return Response(
            id=id,
            method="request/permission",
            params={"tool_name": tool_name,
                    "tool_input": tool_input,
                    "reason": reason},
        )
