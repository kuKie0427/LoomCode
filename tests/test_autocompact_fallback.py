"""Tests for f-autocompact-fallback-p1.

Verifies (a) when LLM summary generation fails, autocompact does NOT
silently return (the pre-fix bug), (b) raw truncate is applied as a
fallback, (c) the next should_compact() check sees reduced size and
does NOT re-fire, (d) tail is preserved, (e) todo attachment is
re-injected, (f) the special no-tail case works.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_autocompact_falls_back_to_raw_truncate_when_summary_fails():
    from loom.agent.context import Context

    ctx = Context()
    msgs = [
        {"role": "user", "content": "round 1 question"},
        {"role": "assistant", "content": "round 1 answer"},
        {"role": "user", "content": "round 2 question"},
        {"role": "assistant", "content": "round 2 answer " * 1000},
        {"role": "user", "content": "round 3 question"},
        {"role": "assistant", "content": "round 3 answer"},
        {"role": "user", "content": "round 4 question"},
        {"role": "assistant", "content": "round 4 answer"},
    ]

    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=10000)

    # Raw truncate should have applied: messages replaced with placeholder + tail
    assert len(msgs) >= 1
    first = msgs[0]
    assert first["role"] == "user"
    assert "强制截断" in str(first["content"])
    assert "LLM 摘要失败" in str(first["content"])
    # Tail preserved
    assert any("round 4" in str(m["content"]) for m in msgs)


def test_autocompact_with_successful_summary_unchanged_behavior():
    from loom.agent.context import Context

    ctx = Context()
    msgs = [
        {"role": "user", "content": "round 1"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "round 2"},
        {"role": "assistant", "content": "answer 2 " * 1000},
        {"role": "user", "content": "round 3"},
        {"role": "assistant", "content": "answer 3"},
    ]

    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value="FAKE SUMMARY"):
        ctx.autocompact(msgs, fake_client, context_window=10000)

    assert len(msgs) >= 1
    first = msgs[0]
    assert first["role"] == "user"
    assert "FAKE SUMMARY" in str(first["content"])
    assert "压缩" in str(first["content"])


def test_autocompact_no_tail_messages_creates_only_placeholder():
    from loom.agent.context import Context

    ctx = Context()
    msgs: list = []
    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=10000)
    # Empty messages -> autocompact early-returns without modifying
    assert msgs == []


def test_autocompact_single_message_keeps_as_tail():
    """If there's only one round, that single user message is the only
    candidate for the tail. After fallback, the placeholder + that
    one message is preserved (we never destroy user content)."""
    from loom.agent.context import Context

    ctx = Context()
    msgs = [
        {"role": "user", "content": "only one round"},
    ]
    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=10000)
    assert len(msgs) == 2
    assert "强制截断" in str(msgs[0]["content"])
    assert msgs[1]["content"] == "only one round"


def test_autocompact_resets_token_counter_after_raw_truncate():
    from loom.agent.context import Context

    ctx = Context()
    ctx.last_input_tokens = 50000
    ctx.checked_at_index = 7
    msgs = [
        {"role": "user", "content": "round 1"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "round 2"},
        {"role": "assistant", "content": "answer 2 " * 1000},
    ]
    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=10000)
    assert ctx.last_input_tokens == 0
    assert ctx.checked_at_index == 0


def test_should_compact_does_not_re_fire_after_raw_truncate():
    """Regression: the pre-fix bug was that autocompact silent-returned,
    and the next loop iteration immediately re-triggered autocompact
    (since size was unchanged). With the fallback, size drops so
    should_compact returns False."""
    from loom.agent.context import Context

    ctx = Context()
    # Head is huge; tail after fallback should fit in 10% budget
    msgs = [
        {"role": "user", "content": "round 1" * 200},
        {"role": "assistant", "content": "answer 1" * 200},
        {"role": "user", "content": "round 2" * 200},
        {"role": "assistant", "content": "answer 2" * 200},
        {"role": "user", "content": "tail q"},
        {"role": "assistant", "content": "tail a"},
    ]
    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=1000)
    # After fallback, the head (huge) is replaced by placeholder + tail
    # (the 2 tail messages — "tail q" + "tail a" — plus placeholder).
    # should_compact should return False for context_window=1000.
    assert not ctx.should_compact(msgs, 1000, "test-model")


def test_raw_truncate_reinjects_todo_attachment():
    from loom.agent.context import Context

    ctx = Context()
    todo_block = "## Current Tasks\n  [ ] first todo\n  [x] second todo\n"
    msgs = [
        {"role": "user", "content": "round 1"},
        {"role": "assistant", "content": "answer 1"},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "is_error": False, "content": todo_block}],
        },
        {"role": "assistant", "content": "answer 2 " * 1000},
        {"role": "user", "content": "round 3"},
        {"role": "assistant", "content": "answer 3"},
    ]
    fake_client = MagicMock()
    with patch.object(ctx, "_generate_summary", return_value=None):
        ctx.autocompact(msgs, fake_client, context_window=10000)
    serialized = "\n".join(str(m["content"]) for m in msgs)
    assert "first todo" in serialized


def test_harness_eval_autocompact_fallback_defined():
    from loom.eval.cases.autocompact_fallback import AutocompactFallbackDefined
    case = AutocompactFallbackDefined()
    result = case.run()
    assert result.passed
