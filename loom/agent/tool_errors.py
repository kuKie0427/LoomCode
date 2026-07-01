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

import re
from typing import Any

MAX_REPEATED_FAILURES = 3
LOOKBACK_MESSAGES = 6

# P0-F: 未完成意图检测。LLM 输出末尾声明要做某事（"我来直接创建 CSS："）
# 但 stop_reason=end_turn 且本轮 0 工具调用——说完就停了。
# 末尾意图触发词：以这些词结尾（允许紧跟冒号/句号/感叹号/空白）即视为未完成意图。
_UNFINISHED_INTENT_TAIL_RE = re.compile(
    r"[^\n。！？]*"
    r"(?:我来|我现在|接下来|我将|我马上|让我|我直接|我来直接|我准备|"
    r"我现在就|我马上就|接着|然后|下一步|下一步我|下面我)"
    r"[^。\n！？]*[:：]\s*$"
)

# 防死循环：最多注入 N 次未完成意图 reminder
MAX_UNFINISHED_INTENT_REMINDERS = 2


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


def detect_unfinished_intent(assistant_content: list) -> str | None:
    """Detect "unfinished intent" — the LLM announced an action in text
    (e.g. "我来直接创建 CSS：") but produced no tool_use block in this turn.

    Returns the matched tail text if unfinished intent is detected, else None.

    Conditions:
    1. The assistant message contains text blocks but NO tool_use blocks.
    2. The last non-empty text block ends with an "intent declaration" tail
       matching ``_UNFINISHED_INTENT_TAIL_RE`` (e.g. ends with "我来...：" /
       "接下来...：" / "我将...：" followed by a colon and optional whitespace).
    3. The matched tail is the END of the text — mid-paragraph intent
       declarations (followed by more text) are NOT triggered, because
       those are usually narrative context, not a stop-then-do pattern.

    Block format: ``[{"type": "text", "text": "..."}, {"type": "tool_use", ...}]``
    Supports both dict blocks (new serialization) and objects with .type/.text
    attributes (in-memory dataclasses).
    """
    has_tool_use = False
    last_text: str = ""
    for block in assistant_content:
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
        if btype == "tool_use":
            has_tool_use = True
            break
        if btype == "text":
            txt = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            if txt:
                last_text = txt
    if has_tool_use:
        return None
    if not last_text:
        return None
    # Match against the tail of the last text block
    m = _UNFINISHED_INTENT_TAIL_RE.search(last_text)
    if m is None:
        return None
    return m.group(0).strip()


def build_unfinished_intent_guidance(matched_tail: str, attempt: int) -> str:
    """Build the system-reminder forcing the LLM to execute the announced action.

    ``attempt`` is the 1-based injection counter (capped at
    ``MAX_UNFINISHED_INTENT_REMINDERS``). The reminder escalates in tone
    on the second attempt to make the LLM actually call the tool.
    """
    base = (
        f"<system-reminder>\n"
        f"[unfinished_intent] 你上一轮说了 “{matched_tail}” 但没有调用任何工具就停下了。"
        f"声明要做的事必须在本轮通过工具调用执行——不能说完就停。\n"
        f"立即调用合适的工具完成你声明的动作；如果你已经决定不做，请明确告诉用户为什么。\n"
    )
    if attempt >= 2:
        base += (
            f"\n[final_reminder] 这是第 {attempt} 次提醒。再不调用工具执行，"
            f"主代理将视为放弃此动作并真正停止。\n"
        )
    base += "</system-reminder>"
    return base
