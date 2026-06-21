"""Tests for f-max-turns-guard-p0.

Verifies (a) config parsing of `[agent] max_turns` from harness.toml,
(b) the agent loop exits cleanly when max_turns is reached, (c) the
limit-reached system reminder is injected into messages, (d) the
loop_limit_reached trace event fires, (e) default max_turns=100.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from loom.agent.config import (
    DEFAULT_MAX_TURNS,
    HarnessConfig,
    load_config,
)


def test_default_max_turns_is_100():
    cfg = HarnessConfig.from_defaults()
    assert cfg.max_turns == DEFAULT_MAX_TURNS
    assert cfg.max_turns == 100


def test_load_config_default_max_turns_when_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.max_turns == 100


def test_load_config_parses_max_turns_from_toml(tmp_path):
    (tmp_path / "harness.toml").write_text("[agent]\nmax_turns = 42\n")
    cfg = load_config(tmp_path)
    assert cfg.max_turns == 42


def test_load_config_rejects_zero_max_turns(tmp_path):
    (tmp_path / "harness.toml").write_text("[agent]\nmax_turns = 0\n")
    with pytest.raises(Exception, match="positive integer"):
        load_config(tmp_path)


def test_load_config_rejects_negative_max_turns(tmp_path):
    (tmp_path / "harness.toml").write_text("[agent]\nmax_turns = -5\n")
    with pytest.raises(Exception, match="positive integer"):
        load_config(tmp_path)


def test_load_config_rejects_string_max_turns(tmp_path):
    (tmp_path / "harness.toml").write_text('[agent]\nmax_turns = "fifty"\n')
    with pytest.raises(Exception, match="positive integer"):
        load_config(tmp_path)


def test_load_config_missing_agent_section_uses_default(tmp_path):
    (tmp_path / "harness.toml").write_text('[permissions]\ndeny_patterns = []\n')
    cfg = load_config(tmp_path)
    assert cfg.max_turns == 100


def _make_stop_reason_response(stop_reason: str = "end_turn", text: str = "done"):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)
    resp.content = [MagicMock(type="text", text=text)]
    return resp


class TestAgentLoopExitsAtMaxTurns:
    """The agent_loop() must stop after `max_turns` iterations even if
    the LLM keeps emitting tool_use blocks."""

    def test_loop_exits_after_max_turns_tool_use_forever(self, tmp_path, monkeypatch):
        import loom.agent.loop as loop_mod
        from loom.agent.config import CheckpointConfig, HarnessConfig, LLMConfig
        from loom.agent.permissions import DEFAULT_POLICY

        wd = tmp_path
        monkeypatch.chdir(wd)

        cfg = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            llm=LLMConfig.from_defaults(),
            max_turns=3,
        )

        fake_llm = MagicMock()
        fake_llm.get_context_window.return_value = 200000
        fake_llm.model = "test-model"

        recorded_events: list[dict] = []
        fake_trace = MagicMock()
        fake_trace.record = lambda ev, **kw: recorded_events.append({"event": ev, **kw})
        fake_trace.stop = lambda: None
        monkeypatch.setattr(loop_mod.trace_mod, "current", lambda: fake_trace)
        monkeypatch.setattr(loop_mod.trace_mod, "stop", lambda: None)

        tool_response = MagicMock()
        tool_response.stop_reason = "tool_use"
        tool_response.usage = MagicMock(input_tokens=5, output_tokens=3)
        tool_response.content = [MagicMock(type="tool_use", id="t1", name="bash", input={"command": "echo hi"})]
        fake_llm.client.messages.create.return_value = tool_response

        from loom.agent.hooks import Hooks
        hooks = Hooks(loop_mod._active_config.policy, frozenset(), asker=lambda *a, **k: True)
        monkeypatch.setattr(loop_mod, "hooks", hooks)

        from loom.agent import tools as tools_mod
        monkeypatch.setattr(tools_mod, "run_bash", lambda cmd: "hi")

        loop_mod.apply_config(cfg)
        from loom.agent.checkpoint import exists
        if exists(wd):
            from loom.agent.checkpoint import load
            saved = load(wd) or {}
            msgs = saved.get("messages", [])
        else:
            msgs = []
        if not msgs:
            msgs = [{"role": "user", "content": "loop forever please"}]

        loop_mod.agent_loop(msgs, llm_client=fake_llm, callbacks={}, stream_text=None)

        # We should have seen 3 turn iterations then the limit-reached event
        loop_events = [e for e in recorded_events if e["event"] == "loop_limit_reached"]
        assert len(loop_events) == 1
        assert loop_events[0]["max_turns"] == 3
        assert loop_events[0]["turn"] == 3
        assert fake_llm.client.messages.create.call_count == 3

        # The last user message in the history should be the limit-reached reminder
        last_user = [m for m in msgs if m.get("role") == "user"][-1]
        assert "maximum turn limit" in str(last_user["content"])
        assert "3" in str(last_user["content"])

    def test_loop_exits_naturally_under_max_turns(self, tmp_path, monkeypatch):
        """When the LLM responds with end_turn before max_turns, loop exits via
        the natural path, NOT via loop_limit_reached."""
        import loom.agent.loop as loop_mod
        from loom.agent.config import CheckpointConfig, HarnessConfig, LLMConfig
        from loom.agent.permissions import DEFAULT_POLICY

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
        fake_llm.model = "test-model"
        fake_llm.client.messages.create.return_value = _make_stop_reason_response("end_turn", "all done")

        recorded_events: list[dict] = []
        fake_trace = MagicMock()
        fake_trace.record = lambda ev, **kw: recorded_events.append({"event": ev, **kw})
        monkeypatch.setattr(loop_mod.trace_mod, "current", lambda: fake_trace)
        monkeypatch.setattr(loop_mod.trace_mod, "stop", lambda: None)

        from loom.agent.hooks import Hooks
        hooks = Hooks(loop_mod._active_config.policy, frozenset(), asker=lambda *a, **k: True)
        monkeypatch.setattr(loop_mod, "hooks", hooks)

        loop_mod.apply_config(cfg)
        msgs = [{"role": "user", "content": "hi"}]
        loop_mod.agent_loop(msgs, llm_client=fake_llm, callbacks={}, stream_text=None)

        limit_events = [e for e in recorded_events if e["event"] == "loop_limit_reached"]
        assert limit_events == []
        # session_end should fire on natural exit
        assert any(e["event"] == "session_end" for e in recorded_events)
        assert fake_llm.client.messages.create.call_count == 1


class TestMaxTurnsHarnessEval:
    """Harness eval cases for max-turns guard (no LLM cost)."""

    def test_max_turns_default_in_config(self):
        from loom.eval.cases.max_turns_guard import MaxTurnsGuardDefault
        case = MaxTurnsGuardDefault()
        result = case.run()
        assert result.passed
        assert "100" in result.detail

    def test_max_turns_loop_limit_reached_constant_exists(self):
        from loom.eval.cases.max_turns_guard import MaxTurnsGuardTraceEventDefined
        case = MaxTurnsGuardTraceEventDefined()
        result = case.run()
        assert result.passed
