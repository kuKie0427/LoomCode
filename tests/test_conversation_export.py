"""Tests for f-conversation-export-p2.

Verifies to_markdown and to_json render messages correctly, redact
removes PII, write_export creates parent dirs.
"""

from __future__ import annotations

import json

from loom.agent.cost import SessionCostAccumulator, TokenUsage, compute_cost
from loom.agent.export import (
    ExportMetadata,
    redact_pii,
    to_json,
    to_markdown,
    write_export,
)


def _make_messages() -> list:
    return [
        {"role": "user", "content": "Hello agent."},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "I should greet back."},
            {"type": "text", "text": "Hi! How can I help?"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "file1.txt\nfile2.txt"}
        ]},
        {"role": "assistant", "content": "There are 2 files."},
    ]


def _make_meta() -> ExportMetadata:
    sess = SessionCostAccumulator()
    sess.add(TokenUsage(input_tokens=1000, output_tokens=500), compute_cost("claude-sonnet-4-5", TokenUsage(1000, 500)))
    return ExportMetadata(
        model="claude-sonnet-4-5",
        session_id="2026-06-22T10:00:00",
        workdir="/tmp/test",
        tool_call_count=1,
        started_at="2026-06-22T10:00:00",
        ended_at="2026-06-22T10:05:00",
        session_cost=sess,
    )


def test_to_markdown_contains_metadata_header():
    out = to_markdown(_make_messages(), _make_meta())
    assert "# Agent Session Transcript" in out
    assert "claude-sonnet-4-5" in out
    assert "/tmp/test" in out
    assert "Tool calls**: 1" in out


def test_to_markdown_contains_cost_summary():
    out = to_markdown(_make_messages(), _make_meta())
    assert "Total cost" in out
    assert "Total tokens" in out


def test_to_markdown_collapses_thinking_blocks():
    out = to_markdown(_make_messages(), _make_meta())
    assert "details" in out
    assert "I should greet back" in out


def test_to_markdown_includes_tool_call():
    out = to_markdown(_make_messages(), _make_meta())
    assert "bash" in out
    assert "command" in out


def test_to_markdown_includes_tool_result():
    out = to_markdown(_make_messages(), _make_meta())
    assert "file1.txt" in out
    assert "file2.txt" in out


def test_to_markdown_handles_string_and_list_content():
    out = to_markdown(_make_messages(), _make_meta())
    assert "Hello agent" in out
    assert "Hi! How can I help?" in out
    assert "There are 2 files" in out


def test_to_markdown_truncates_long_tool_results():
    long_result = "x" * 5000
    messages = [{
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": long_result}
        ]
    }]
    out = to_markdown(messages, _make_meta())
    assert "more chars" in out
    assert len(out) < len(long_result)


def test_to_markdown_handles_string_user_content():
    messages = [{"role": "user", "content": "Just a string question."}]
    out = to_markdown(messages, _make_meta())
    assert "Just a string question" in out


def test_to_json_contains_metadata():
    out = to_json(_make_messages(), _make_meta())
    parsed = json.loads(out)
    assert parsed["metadata"]["model"] == "claude-sonnet-4-5"
    assert parsed["metadata"]["tool_call_count"] == 1


def test_to_json_messages_round_trip():
    msgs = _make_messages()
    out = to_json(msgs, _make_meta())
    parsed = json.loads(out)
    assert len(parsed["messages"]) == 4
    assert parsed["messages"][0]["content"] == "Hello agent."
    assert parsed["messages"][1]["content"][0]["type"] == "thinking"


def test_to_json_includes_cost_when_present():
    out = to_json(_make_messages(), _make_meta())
    parsed = json.loads(out)
    assert "session_cost" in parsed["metadata"]
    assert parsed["metadata"]["session_cost"]["turns"] == 1


def test_to_json_omits_cost_when_absent():
    meta = _make_meta()
    meta.session_cost = None
    out = to_json(_make_messages(), meta)
    parsed = json.loads(out)
    assert "session_cost" not in parsed["metadata"]


def test_redact_pii_replaces_api_key():
    text = "Use key sk-abcdefghijklmnopqrstuvwxyz1234567890 for auth"
    out = redact_pii(text)
    assert "[REDACTED_API_KEY]" in out
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out


def test_redact_pii_replaces_env_var():
    text = "ANTHROPIC_API_KEY=mysecretvalue123 should not be exposed"
    out = redact_pii(text)
    assert "[REDACTED]" in out


def test_redact_pii_replaces_email():
    text = "Contact user@example.com for support"
    out = redact_pii(text)
    assert "[REDACTED_EMAIL]" in out


def test_redact_pii_preserves_non_pii_text():
    text = "Just a normal sentence with no secrets."
    out = redact_pii(text)
    assert out == text


def test_write_export_creates_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "out.md"
    path = write_export("hello", target)
    assert path.exists()
    assert path.read_text() == "hello"


def test_write_export_applies_redaction(tmp_path):
    target = tmp_path / "out.md"
    write_export("key sk-abcdefghijklmnopqrstuvwxyz1234567890", target, redact=True)
    assert "[REDACTED_API_KEY]" in target.read_text()


def test_write_export_no_redaction_by_default(tmp_path):
    target = tmp_path / "out.md"
    write_export("key sk-abcdefghijklmnopqrstuvwxyz1234567890", target)
    assert "sk-abcdef" in target.read_text()
