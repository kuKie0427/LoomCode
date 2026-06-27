"""Tests for the loom JSON-RPC protocol — shared between Python core and Go TUI."""

from __future__ import annotations

import json

import pytest

from loom.rpc.protocol import (
    EVENT_TYPES,
    REQUEST_TYPES,
    Event,
    Request,
    Response,
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
