"""Unit tests for Context in context.py."""

from context import Context, KEEP_RECENT, COMPACTABLE_TOOLS


def test_estimate_tokens_approx_chars_div_4():
    """estimate_tokens returns approximately total_chars // 4 for mixed messages."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'm fine, thanks!"},
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "bash",
                    "input": {"cmd": "ls"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "file1.txt"}
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
        },
    ]

    # Only str content and text blocks contribute to token count
    total_chars = (
        len("Hello, how are you?")  # user str content
        + len("I'm fine, thanks!")  # assistant text block
        + len("Done.")  # assistant text block
    )
    # total_chars = 19 + 17 + 5 = 41, 41 // 4 = 10

    tokens = ctx.estimate_tokens(messages)
    assert abs(tokens - total_chars // 4) <= 2


def test_should_compact_below_threshold():
    """should_compact returns False when tokens are below 85% of context_window."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
    ]
    # current_tokens = 0 + (len("Hi") + len("Hello")) // 4 = 0 + 8 // 4 = 2
    # 2 < 100 * 0.85 = 85
    assert ctx.should_compact(messages, context_window=100) is False


def test_should_compact_above_threshold():
    """should_compact returns True when tokens exceed 85% of context_window."""
    ctx = Context()
    ctx.last_input_tokens = 1000  # shortcut: skip constructing huge messages
    messages = [
        {"role": "user", "content": "Hi"},
    ]
    # current_tokens = 1000 + (2 // 4) = 1000
    # 1000 >= 100 * 0.85 = 85
    assert ctx.should_compact(messages, context_window=100) is True


def test_find_rounds_returns_user_str_indices():
    """_find_rounds returns indices of user messages with str content."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Round 1"},
        {"role": "assistant", "content": "Response 1"},
        {"role": "user", "content": "Round 2"},
        {"role": "assistant", "content": "Response 2"},
        {"role": "user", "content": "Round 3"},
        {"role": "assistant", "content": "Response 3"},
    ]
    assert ctx._find_rounds(messages) == [0, 2, 4]


def test_find_rounds_no_matches():
    """_find_rounds returns [] when no user+str messages exist."""
    ctx = Context()
    messages = [
        {"role": "assistant", "content": "Response 1"},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "result"}
            ],
        },
        {"role": "assistant", "content": "Response 2"},
    ]
    assert ctx._find_rounds(messages) == []


def test_align_to_round_start_exact_match():
    """_align_to_round_start returns the cutoff when it matches a round start."""
    ctx = Context()
    assert ctx._align_to_round_start([0, 3, 6], 3) == 3


def test_align_to_round_start_between_rounds():
    """_align_to_round_start aligns to the nearest preceding round start."""
    ctx = Context()
    assert ctx._align_to_round_start([0, 3, 6], 4) == 3


def test_extract_last_todo_finds_tool_result():
    """_extract_last_todo returns tool_result content starting with '## Current Tasks'."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Round 1"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "OK"}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "## Current Tasks\n- Task 1\n- Task 2",
                    "is_error": False,
                }
            ],
        },
    ]
    result = ctx._extract_last_todo(messages)
    assert result == "## Current Tasks\n- Task 1\n- Task 2"


def test_extract_last_todo_no_todo():
    """_extract_last_todo returns None when no matching tool_result exists."""
    ctx = Context()

    # Case 1: content does not start with "## Current Tasks"
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "Some other content",
                    "is_error": False,
                }
            ],
        },
    ]
    assert ctx._extract_last_todo(messages) is None

    # Case 2: is_error=True
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "## Current Tasks\n- Task 1",
                    "is_error": True,
                }
            ],
        },
    ]
    assert ctx._extract_last_todo(messages) is None

    # Case 3: no tool_result at all (plain str messages)
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    assert ctx._extract_last_todo(messages) is None


def _build_rounds(num_rounds: int, tool_name: str = "bash") -> list:
    """Build a list of messages with num_rounds conversation rounds.

    Each round follows the pattern:
      user(str) → assistant([tool_use, text]) → user([tool_result]) → assistant([text])
    """
    messages: list = []
    for i in range(num_rounds):
        tool_id = str(i)
        messages.append({"role": "user", "content": f"Round {i + 1}"})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": {"cmd": "ls"},
                    },
                    {"type": "text", "text": f"Running {tool_name}..."},
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": f"Result of round {i + 1}",
                        "is_error": False,
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"Done round {i + 1}"}],
            }
        )
    return messages


def test_microcompact_preserves_tail():
    """microcompact preserves at least KEEP_RECENT user+str messages in the tail."""
    ctx = Context()
    messages = _build_rounds(7)  # 7 rounds → 7 user+str markers, > KEEP_RECENT=6

    # Sanity check: we start with 7 user+str messages
    user_str_before = sum(
        1 for msg in messages if msg["role"] == "user" and isinstance(msg["content"], str)
    )
    assert user_str_before == 7

    ctx.microcompact("PreToolUse", messages)

    # After microcompact, all user+str messages remain (microcompact only modifies
    # tool_result content, it doesn't delete messages). Count should be >= KEEP_RECENT.
    user_str_after = sum(
        1 for msg in messages if msg["role"] == "user" and isinstance(msg["content"], str)
    )
    assert user_str_after >= KEEP_RECENT


def test_microcompact_strips_compactable_tools():
    """microcompact clears tool_result content for compactable tools before the cutoff."""
    ctx = Context()
    messages = _build_rounds(7)  # all 7 rounds use "bash" (compactable)

    # Verify original content before compaction
    assert messages[2]["content"][0]["content"] == "Result of round 1"

    ctx.microcompact("PreToolUse", messages)

    # First round is before cutoff → tool_result content should be cleared
    assert (
        messages[2]["content"][0]["content"] == "[Old tool result content cleared]"
    )

    # Rounds at/after cutoff should NOT have their tool_result content cleared
    for round_start_idx in range(4, len(messages), 4):
        tr_msg_idx = round_start_idx + 2  # tool_result user message
        if tr_msg_idx < len(messages):
            tr_content = messages[tr_msg_idx]["content"][0]["content"]
            assert tr_content != "[Old tool result content cleared]"
