"""Regression for f-stream-error-handling: the agent_loop streaming path
must surface provider error events to the user instead of silently dropping
them.

Original failure: a thinking model that took >60s caused APITimeoutError in
the Anthropic provider. The exception was NOT in the `except` list, so the
async coroutine crashed, _producer_target logged + put None, the consumer
got zero events, and the TUI showed `thinking · Ns` with an empty thinking
display + no output forever.

Two parts to the fix:
  1) provider emits an `error` StreamEvent on APITimeoutError / unexpected
     exceptions (covered in test_providers.py::TestAnthropicStreamErrorPaths).
  2) agent_loop's streaming branch handles `ev.kind == 'error'` by appending
     a visible TextBlock + firing on_text_delta + recording a trace event +
     ending the turn (no infinite loop on the error).

This file covers (2).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_streaming_path_surfaces_error_event_to_user(tmp_path, monkeypatch):
    import loom.agent.loop as loop_mod
    from loom.agent.config import CheckpointConfig, HarnessConfig, LLMConfig
    from loom.agent.permissions import DEFAULT_POLICY
    from loom.agent.providers.types import ProviderErrorCode, StreamEvent

    monkeypatch.chdir(tmp_path)
    cfg = HarnessConfig(
        policy=DEFAULT_POLICY,
        checkpoint=CheckpointConfig.from_defaults(),
        disabled_tools=frozenset(),
        llm=LLMConfig.from_defaults(),
        max_turns=10,
    )

    fake_llm = MagicMock()
    fake_llm.get_context_window.return_value = 200000
    fake_llm.model = "anthropic/claude-sonnet-4-5"

    def fake_stream(system, messages, tools, max_tokens):
        yield StreamEvent(
            kind="error",
            error_code=ProviderErrorCode.TIMEOUT,
            error_message="stream timed out after 600s",
        )

    recorded: list[dict] = []
    fake_trace = MagicMock()
    fake_trace.record = lambda ev, **kw: recorded.append({"event": ev, **kw})
    monkeypatch.setattr(loop_mod.trace_mod, "current", lambda: fake_trace)
    monkeypatch.setattr(loop_mod.trace_mod, "stop", lambda: None)

    from loom.agent.hooks import Hooks
    hooks = Hooks(loop_mod._active_config.policy, frozenset(), asker=lambda *a, **k: True)
    monkeypatch.setattr(loop_mod, "hooks", hooks)

    loop_mod.apply_config(cfg)

    text_chunks: list[str] = []
    callbacks = {
        "on_text_delta": lambda chunk: text_chunks.append(chunk),
    }

    msgs: list[dict] = [{"role": "user", "content": "hi"}]
    loop_mod.agent_loop(msgs, llm_client=fake_llm, callbacks=callbacks, stream_text=fake_stream)

    assert any(e["event"] == "llm_error" for e in recorded), (
        f"expected llm_error trace event, got {[e['event'] for e in recorded]}"
    )
    err_evs = [e for e in recorded if e["event"] == "llm_error"]
    assert err_evs[0]["code"] == ProviderErrorCode.TIMEOUT
    assert "timed out" in err_evs[0]["message"].lower()

    assert any("LLM error" in c and "timeout" in c.lower() for c in text_chunks), (
        f"on_text_delta should have received an error text chunk, got {text_chunks}"
    )

    assert fake_llm.invoke.call_count == 0, (
        "streaming branch should NOT fall through to llm.invoke after an error"
    )

    last_assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
    assistant_text = ""
    for block in last_assistant["content"]:
        if getattr(block, "type", None) == "text":
            assistant_text += block.text
    assert "LLM error" in assistant_text
    assert "timeout" in assistant_text.lower()


def test_streaming_path_does_not_loop_forever_on_error(tmp_path, monkeypatch):
    """The error path must end the turn (stop_reason='end_turn'), not retry."""
    import loom.agent.loop as loop_mod
    from loom.agent.config import CheckpointConfig, HarnessConfig, LLMConfig
    from loom.agent.permissions import DEFAULT_POLICY
    from loom.agent.providers.types import ProviderErrorCode, StreamEvent

    monkeypatch.chdir(tmp_path)
    cfg = HarnessConfig(
        policy=DEFAULT_POLICY,
        checkpoint=CheckpointConfig.from_defaults(),
        disabled_tools=frozenset(),
        llm=LLMConfig.from_defaults(),
        max_turns=5,
    )

    fake_llm = MagicMock()
    fake_llm.get_context_window.return_value = 200000
    fake_llm.model = "anthropic/claude-sonnet-4-5"

    call_count = {"n": 0}

    def fake_stream(system, messages, tools, max_tokens):
        call_count["n"] += 1
        yield StreamEvent(
            kind="error",
            error_code=ProviderErrorCode.UNKNOWN,
            error_message="boom",
        )

    fake_trace = MagicMock()
    fake_trace.record = lambda ev, **kw: None
    monkeypatch.setattr(loop_mod.trace_mod, "current", lambda: fake_trace)
    monkeypatch.setattr(loop_mod.trace_mod, "stop", lambda: None)

    from loom.agent.hooks import Hooks
    hooks = Hooks(loop_mod._active_config.policy, frozenset(), asker=lambda *a, **k: True)
    monkeypatch.setattr(loop_mod, "hooks", hooks)

    loop_mod.apply_config(cfg)

    msgs: list[dict] = [{"role": "user", "content": "hi"}]
    loop_mod.agent_loop(msgs, llm_client=fake_llm, callbacks={}, stream_text=fake_stream)

    assert call_count["n"] == 1, (
        f"streaming branch should end the turn after error, called {call_count['n']} times"
    )
