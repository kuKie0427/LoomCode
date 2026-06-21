"""Tests for f-microcompact-token-counter-p1.

Verifies that microcompact updates last_input_tokens to reflect the
bytes it cleared, so should_compact doesn't fire prematurely after
a microcompact pass.
"""

from __future__ import annotations


def _round(i: int, text: str = "x") -> dict:
    return {"role": "user", "content": text}


def _assistant_tool_use(tool_id: str, name: str, inp: dict) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}],
    }


def _tool_result(tool_use_id: str, content: str, name: str = "bash") -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": False, "content": content}
        ],
    }


def test_microcompact_does_nothing_under_threshold():
    from loom.agent.context import KEEP_RECENT, Context
    ctx = Context()
    messages = []
    for i in range(KEEP_RECENT):
        messages.append(_round(i, f"round {i}"))
    ctx.last_input_tokens = 1000
    ctx.microcompact("PreToolUse", messages)
    assert ctx.last_input_tokens == 1000


def test_microcompact_clears_old_bash_outputs():
    from loom.agent.context import Context
    ctx = Context()
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "bash", {"command": f"echo {i}"}))
        messages.append(_tool_result(f"t{i}", "big bash output " * 100))
    ctx.last_input_tokens = 100
    ctx.microcompact("PreToolUse", messages)
    for m in messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for block in m["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                round_num = int(tool_use_id.lstrip("t"))
                if round_num < 8 - 6:
                    assert block["content"] == "[Old tool result content cleared]", \
                        f"round {round_num} tool_result not cleared: {block['content'][:60]}"


def test_microcompact_updates_last_input_tokens():
    from loom.agent.context import Context
    ctx = Context()
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "bash", {"command": f"echo {i}"}))
        messages.append(_tool_result(f"t{i}", "x" * 1000))
    ctx.last_input_tokens = 5000
    ctx.microcompact("PreToolUse", messages)
    assert ctx.last_input_tokens < 5000
    assert ctx.last_input_tokens >= 0


def test_microcompact_does_not_reduce_counter_below_zero():
    from loom.agent.context import Context
    ctx = Context()
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "bash", {"command": f"echo {i}"}))
        messages.append(_tool_result(f"t{i}", "x" * 1000))
    ctx.last_input_tokens = 10
    ctx.microcompact("PreToolUse", messages)
    assert ctx.last_input_tokens == 0


def test_microcompact_cleared_byte_count_matches_reduction():
    from loom.agent.context import Context
    ctx = Context()
    placeholder_len = len("[Old tool result content cleared]")
    output_len = 500
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "bash", {"command": f"echo {i}"}))
        messages.append(_tool_result(f"t{i}", "y" * output_len))
    # Start high enough to capture the full reduction without clamping to 0
    ctx.last_input_tokens = 1000
    ctx.microcompact("PreToolUse", messages)
    rounds_to_clear = 8 - 6
    expected_saved_bytes = (output_len - placeholder_len) * rounds_to_clear
    expected_saved_tokens = expected_saved_bytes // 4
    assert ctx.last_input_tokens == 1000 - expected_saved_tokens


def test_microcompact_skips_non_compactable_tools():
    from loom.agent.context import Context
    ctx = Context()
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "read_file", {"path": "x.py"}))
        messages.append(_tool_result(f"t{i}", "z" * 1000))
    ctx.last_input_tokens = 0
    ctx.microcompact("PreToolUse", messages)
    assert ctx.last_input_tokens == 0
    for m in messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for block in m["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                assert block["content"] != "[Old tool result content cleared]"
                assert block["content"] == "z" * 1000


def test_microcompact_invalidate_token_cache():
    """The _token_cache must be invalidated so the next should_compact
    uses fresh counts, not stale pre-microcompact values."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import Context

    ctx = Context()
    messages = []
    for i in range(8):
        messages.append(_round(i, f"round {i}"))
        messages.append(_assistant_tool_use(f"t{i}", "bash", {"command": f"echo {i}"}))
        messages.append(_tool_result(f"t{i}", "x" * 1000))
    key = id(messages)
    ctx_mod._token_cache[key] = 99999
    try:
        ctx.microcompact("PreToolUse", messages)
        assert key not in ctx_mod._token_cache or ctx_mod._token_cache.get(key) != 99999
    finally:
        ctx_mod._token_cache.pop(key, None)


def test_harness_eval_microcompact_counter_defined():
    from loom.eval.cases.microcompact_counter import MicrocompactCounterDefined
    case = MicrocompactCounterDefined()
    result = case.run()
    assert result.passed
