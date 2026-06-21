"""Tool error retry detection.

Scans the recent message history for repeated identical tool_use
blocks (same tool + same input) where every result was an error.
After N consecutive failures (default 3), injects a system-reminder
guiding the agent to stop retrying and reconsider.

This catches the observed failure mode: the agent retries the
exact same failing command N times, burning tokens, instead of
reading the error and changing its approach.
"""

from __future__ import annotations

from typing import Any

MAX_REPEATED_FAILURES = 3
LOOKBACK_MESSAGES = 6


def extract_tool_use_blocks(message: dict) -> list[dict]:
    """Pull all tool_use blocks from an assistant message."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def extract_tool_result_blocks(message: dict) -> list[dict]:
    """Pull all tool_result blocks from a user message."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _tool_call_key(tool_use: dict) -> tuple:
    return (tool_use.get("name", "?"), _stable_input(tool_use.get("input", {})))


def _stable_input(inp: Any) -> str:
    if isinstance(inp, dict):
        return repr(sorted(inp.items()))
    return repr(inp)


def detect_repeated_failures(messages: list, max_failures: int = MAX_REPEATED_FAILURES,
                              lookback: int = LOOKBACK_MESSAGES) -> dict | None:
    """Return a dict describing the repeated failure if detected, else None.

    Walks the most recent N messages, looking for an assistant tool_use
    followed by a user tool_result with is_error=True for the same tool_use_id,
    and counts consecutive same-key failures. When count reaches max_failures,
    returns the offending tool + sample input.

    Algorithm:
    - Walk last `lookback` messages, scanning tool_use (assistant) and
      tool_result (user) blocks in order.
    - For each tool_use, check if its result was an error.
    - If same (tool, input) as the previous tool_use, increment count.
      Otherwise, reset count to 0 and track new key.
    - If any group has count >= max_failures, return it.
    """
    if not messages:
        return None
    window = messages[-lookback:]
    last_key: tuple | None = None
    last_count = 0
    last_was_error = False
    last_detection: dict | None = None
    for msg in window:
        if msg.get("role") == "assistant":
            for tu in extract_tool_use_blocks(msg):
                key = _tool_call_key(tu)
                if key == last_key and last_was_error:
                    pass
                elif key == last_key:
                    last_count = 0
                else:
                    last_key = key
                    last_count = 0
                last_was_error = False
        elif msg.get("role") == "user":
            for tr in extract_tool_result_blocks(msg):
                if tr.get("is_error", False):
                    if last_key is not None:
                        last_count += 1
                        last_was_error = True
                        if last_count >= max_failures:
                            tool_name, input_repr = last_key
                            last_detection = {
                                "tool": tool_name,
                                "input_repr": input_repr,
                                "failure_count": last_count,
                            }
                else:
                    last_count = 0
                    last_was_error = False
    return last_detection


def build_retry_guidance(detection: dict) -> str:
    """Build the system-reminder text for a detected repeated-failure pattern."""
    return (
        f"<system-reminder>\n"
        f"You have called `{detection['tool']}` with the same input {detection['failure_count']} times "
        f"in a row, and every call returned an error. The current approach is not working.\n"
        f"Read the error message carefully, then take ONE of these actions:\n"
        f"  1. Diagnose the root cause from the error (file not found? permission denied? "
        f"syntax error?) and adjust the input.\n"
        f"  2. If the input is fundamentally wrong, use a different tool to gather more "
        f"context (e.g. read_file to inspect, grep to find, glob to discover).\n"
        f"  3. If the task cannot be completed, explain the blocker to the user instead of "
        f"retrying further.\n"
        f"Do not call the same tool with the same input again without changing something.\n"
        f"</system-reminder>"
    )
