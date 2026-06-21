"""Tests for f-tool-error-semantics-p2.

Verifies the tool error detection module:
- detect_repeated_failures() correctly identifies N consecutive same-tool errors
- Returns None for non-repeating errors or no errors
- build_retry_guidance() produces a useful system-reminder
- Tool result extraction handles edge cases (string content, missing blocks)
"""

from __future__ import annotations

from loom.agent.tool_errors import (
    build_retry_guidance,
    detect_repeated_failures,
    extract_tool_result_blocks,
    extract_tool_use_blocks,
)


def _tool_use(name: str, input: dict, tu_id: str = "t1") -> dict:
    return {"type": "tool_use", "id": tu_id, "name": name, "input": input}


def _tool_result(tu_id: str, is_error: bool, content: str = "fail") -> dict:
    return {"type": "tool_result", "tool_use_id": tu_id, "is_error": is_error, "content": content}


def _msg(role: str, blocks: list) -> dict:
    return {"role": role, "content": blocks}


def test_extract_tool_use_blocks_from_assistant():
    msg = _msg("assistant", [_tool_use("bash", {"command": "ls"}), {"type": "text", "text": "ok"}])
    blocks = extract_tool_use_blocks(msg)
    assert len(blocks) == 1
    assert blocks[0]["name"] == "bash"


def test_extract_tool_use_blocks_handles_string_content():
    msg = {"role": "assistant", "content": "just text"}
    assert extract_tool_use_blocks(msg) == []


def test_extract_tool_result_blocks():
    msg = _msg("user", [_tool_result("t1", False, "ok"), _tool_result("t2", True, "fail")])
    blocks = extract_tool_result_blocks(msg)
    assert len(blocks) == 2


def test_detect_returns_none_on_empty_history():
    assert detect_repeated_failures([]) is None


def test_detect_returns_none_when_no_errors():
    messages = [
        _msg("user", "do something"),
        _msg("assistant", [_tool_use("bash", {"command": "ls"})]),
        _msg("user", [_tool_result("t1", False, "ok")]),
    ]
    assert detect_repeated_failures(messages) is None


def test_detect_returns_none_with_only_two_failures():
    messages = [
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t1", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t2", True)]),
    ]
    assert detect_repeated_failures(messages) is None


def test_detect_returns_dict_with_three_consecutive_failures():
    messages = [
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t1", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t2", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t3", True)]),
    ]
    detection = detect_repeated_failures(messages)
    assert detection is not None
    assert detection["tool"] == "bash"
    assert detection["failure_count"] == 3


def test_detect_custom_max_failures_threshold():
    messages = [
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t1", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t2", True)]),
    ]
    assert detect_repeated_failures(messages, max_failures=2) is not None
    assert detect_repeated_failures(messages, max_failures=3) is None


def test_detect_different_inputs_not_counted_as_repeat():
    messages = [
        _msg("assistant", [_tool_use("bash", {"command": "rm a"})]),
        _msg("user", [_tool_result("t1", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm b"})]),
        _msg("user", [_tool_result("t2", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm c"})]),
        _msg("user", [_tool_result("t3", True)]),
    ]
    assert detect_repeated_failures(messages) is None


def test_detect_lookback_respects_window():
    """If the repeated failure is outside the lookback window, it's not detected."""
    messages = []
    for i in range(5):
        messages.append(_msg("assistant", [_tool_use("bash", {"command": "rm x"})]))
        messages.append(_msg("user", [_tool_result(f"t{i}", True)]))
    messages.append(_msg("assistant", [_tool_use("read_file", {"path": "/x"})]))
    messages.append(_msg("user", [_tool_result("ok", False, "content")]))
    detection = detect_repeated_failures(messages, lookback=4)
    assert detection is None
    detection = detect_repeated_failures(messages, lookback=12)
    assert detection is not None


def test_detect_distinguishes_error_from_success_in_run():
    """A run of [error, success, error] should NOT count as 2 consecutive."""
    messages = [
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t1", True)]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t2", False, "ok")]),
        _msg("assistant", [_tool_use("bash", {"command": "rm x"})]),
        _msg("user", [_tool_result("t3", True)]),
    ]
    detection = detect_repeated_failures(messages)
    assert detection is None


def test_build_retry_guidance_mentions_tool_and_count():
    guidance = build_retry_guidance({"tool": "bash", "input_repr": "x", "failure_count": 3})
    assert "bash" in guidance
    assert "3 times" in guidance
    assert "<system-reminder>" in guidance
    assert "Do not call the same tool" in guidance


def test_build_retry_guidance_suggests_alternatives():
    guidance = build_retry_guidance({"tool": "edit_file", "input_repr": "x", "failure_count": 4})
    assert "read_file" in guidance
    assert "grep" in guidance
    assert "glob" in guidance
