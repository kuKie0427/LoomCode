"""Unit tests for Context in context.py."""

from loop.agent.context import Context


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
    """microcompact preserves tool_result content for tail rounds while clearing pre-cutoff rounds."""
    ctx = Context()
    messages = _build_rounds(7)

    ctx.microcompact("PreToolUse", messages)

    # Pre-cutoff: round 0's tool_result (index 2) should be cleared
    assert (
        messages[2]["content"][0]["content"] == "[Old tool result content cleared]"
    )

    # Tail rounds (starting at index 4): tool_result content preserved unchanged
    for round_start_idx in range(4, len(messages), 4):
        tr_msg_idx = round_start_idx + 2  # tool_result user message
        if tr_msg_idx < len(messages):
            expected = f"Result of round {round_start_idx // 4 + 1}"
            assert messages[tr_msg_idx]["content"][0]["content"] == expected


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


def test_update_records_token_state(mocker):
    """update() correctly sets last_input_tokens and checked_at_index."""
    ctx = Context()
    mock_response = mocker.Mock()
    mock_response.usage.input_tokens = 5000

    ctx.update(10, mock_response)

    assert ctx.last_input_tokens == 5000
    assert ctx.checked_at_index == 10


def test_current_tokens_integrates_with_update():
    """update() → current_tokens() end-to-end: only new messages count in delta."""
    ctx = Context()
    ctx.last_input_tokens = 1000
    ctx.checked_at_index = 2
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "New message"},
    ]

    # current_tokens = 1000 + estimate_tokens(messages[2:])
    # messages[2:] = [{"role": "user", "content": "New message"}]
    # estimate_tokens = len("New message") // 4 = 11 // 4 = 2
    assert ctx.current_tokens(messages) == 1002


def test_should_compact_with_real_workflow(mocker):
    """update() → should_compact() real chain, without manually setting last_input_tokens."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
    ]

    # Positive case: large input_tokens triggers compaction
    mock_response = mocker.Mock()
    mock_response.usage.input_tokens = 10000
    ctx.update(len(messages), mock_response)
    assert ctx.should_compact(messages, context_window=100) is True

    # Negative case: small input_tokens does not trigger compaction
    ctx2 = Context()
    mock_response2 = mocker.Mock()
    mock_response2.usage.input_tokens = 10
    ctx2.update(len(messages), mock_response2)
    assert ctx2.should_compact(messages, context_window=100) is False


def test_find_tail_cutoff_budget_larger_than_total():
    """_find_tail_cutoff returns 0 when budget exceeds total tokens."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Round 1"},        # 7 chars → 1 token
        {"role": "assistant", "content": "Response 1"},  # 10 chars → 2 tokens
        {"role": "user", "content": "Round 2"},        # 7 chars → 1 token
        {"role": "assistant", "content": "Response 2"},  # 10 chars → 2 tokens
    ]
    # Total tokens ≈ 1+2+1+2 = 6
    # Budget=100 exceeds total, loop exhausts → return 0
    assert ctx._find_tail_cutoff(messages, budget=100) == 0


def test_find_tail_cutoff_partial():
    """_find_tail_cutoff returns the index where accumulated tokens first reach budget."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Round 1"},        # 7 chars → 1 token
        {"role": "assistant", "content": "Response 1"},  # 10 chars → 2 tokens
        {"role": "user", "content": "Round 2"},        # 7 chars → 1 token
        {"role": "assistant", "content": "Response 2"},  # 10 chars → 2 tokens
    ]
    # Budget=2: scanning from end, msg[3]="Response 2" → 2 tokens ≥ 2 → return 3
    assert ctx._find_tail_cutoff(messages, budget=2) == 3


def test_find_tail_cutoff_zero_budget():
    """_find_tail_cutoff returns the last message index when budget is 0."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "B"},
        {"role": "user", "content": "C"},
        {"role": "assistant", "content": "D"},
        {"role": "user", "content": "E"},
        {"role": "assistant", "content": "F"},
    ]
    # Budget=0: first iteration (i=5) always triggers since 0 >= 0
    assert ctx._find_tail_cutoff(messages, budget=0) == 5


def test_generate_summary_returns_summary_text(mocker):
    """_generate_summary returns extracted text when API succeeds."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    ]

    mock_text_block = mocker.Mock()
    mock_text_block.type = "text"
    mock_text_block.text = "[Compressed summary of conversation]"
    mock_response = mocker.Mock()
    mock_response.content = [mock_text_block]
    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_response

    result = ctx._generate_summary(messages, mock_client, "claude-3-haiku-20240307")
    assert result == "[Compressed summary of conversation]"


def test_generate_summary_handles_api_error(mocker):
    """_generate_summary returns None when API call raises an exception."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    ]

    mock_client = mocker.Mock()
    mock_client.messages.create.side_effect = Exception("API error")

    result = ctx._generate_summary(messages, mock_client, "claude-3-haiku-20240307")
    assert result is None


def test_inject_todo_attachment_inserts_at_index_1():
    """_inject_todo_attachment inserts the todo message at index 1."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Another question"},
    ]

    ctx._inject_todo_attachment(messages, "## Current Tasks\n- Task 1\n- Task 2")

    assert len(messages) == 4
    assert messages[1]["role"] == "user"
    assert "<system-reminder>" in messages[1]["content"]
    assert "## Current Tasks" in messages[1]["content"]


def test_autocompact_full_flow_compresses_head(mocker):
    """autocompact compresses head messages into a summary and preserves tail messages."""
    ctx = Context()
    messages = _build_rounds(10)

    mock_text_block = mocker.Mock()
    mock_text_block.type = "text"
    mock_text_block.text = "[Compressed summary]"
    mock_response = mocker.Mock()
    mock_response.content = [mock_text_block]
    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_response

    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert len(messages) == 13
    assert messages[0]["role"] == "user"
    assert "<system-reminder>" in messages[0]["content"]
    assert "[Compressed summary]" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Round 8"


def test_autocompact_not_enough_rounds_returns_early(mocker):
    """autocompact returns immediately when there is 1 or fewer rounds."""
    ctx = Context()
    messages = _build_rounds(1)

    mock_client = mocker.Mock()
    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert len(messages) == 4
    assert messages[0]["content"] == "Round 1"
    assert messages[1]["content"][0]["type"] == "tool_use"


def test_autocompact_llm_failure_skips_compaction(mocker):
    """autocompact skips compaction when _generate_summary raises an exception."""
    ctx = Context()
    messages = _build_rounds(5)

    mock_client = mocker.Mock()
    mock_client.messages.create.side_effect = Exception("API Error")
    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert len(messages) == 20
    assert messages[0]["content"] == "Round 1"


def test_autocompact_injects_todo_when_present(mocker):
    """autocompact injects todo attachment at index 1 when a todo exists in head."""
    ctx = Context()
    messages = _build_rounds(10)
    messages[2]["content"][0]["content"] = "## Current Tasks\n- Refactor module A\n- Write tests"

    mock_text_block = mocker.Mock()
    mock_text_block.type = "text"
    mock_text_block.text = "[Compressed summary]"
    mock_response = mocker.Mock()
    mock_response.content = [mock_text_block]
    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_response

    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert len(messages) == 14
    assert messages[1]["role"] == "user"
    assert "<system-reminder>" in messages[1]["content"]
    assert "## Current Tasks" in messages[1]["content"]
    assert "Refactor module A" in messages[1]["content"]


def test_autocompact_no_todo_no_injection(mocker):
    """autocompact does NOT inject a todo when none exists in head."""
    ctx = Context()
    messages = _build_rounds(10)

    mock_text_block = mocker.Mock()
    mock_text_block.type = "text"
    mock_text_block.text = "[Compressed summary]"
    mock_response = mocker.Mock()
    mock_response.content = [mock_text_block]
    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_response

    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert len(messages) == 13
    all_content = " ".join(str(m["content"]) for m in messages)
    assert "## Current Tasks" not in all_content


def test_autocompact_resets_token_state(mocker):
    """autocompact resets last_input_tokens and checked_at_index after compression."""
    ctx = Context()
    messages = _build_rounds(4)
    ctx.last_input_tokens = 9999
    ctx.checked_at_index = 50

    mock_text_block = mocker.Mock()
    mock_text_block.type = "text"
    mock_text_block.text = "[Compressed summary]"
    mock_response = mocker.Mock()
    mock_response.content = [mock_text_block]
    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_response

    ctx.autocompact(messages, mock_client, "claude-3-haiku-20240307", context_window=200)

    assert ctx.last_input_tokens == 0
    assert ctx.checked_at_index == 0
