"""Eval cases for the per-LLM-call thinking callback (f-tui-thinking-per-llm-call).

Locks in the new `on_assistant_message_start` callback contract added to
agent_loop: it must fire BEFORE EACH LLM call inside the while loop (one per
reasoning round, including the post-tool-use continuation), while the pre-
existing `on_message_start` keeps its once-per-session semantic. The TUI's
`run_agent_turn` must wire both callbacks to `AssistantTurnStart` so the
thinking spinner + fresh ThinkingDisplay appear on every round, not just the
first.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from anthropic.types import TextBlock, ToolUseBlock

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: DEFAULT_CALLBACKS contains on_assistant_message_start ─────────────

class AgentLoopAssistantMessageStartInDefaults(EvalCase):
    name = "agent-loom-assistant-message-start-in-defaults"
    description = (
        "DEFAULT_CALLBACKS in agent_loop includes on_assistant_message_start "
        "alongside the existing on_message_start"
    )

    def run(self) -> EvalResult:
        from loom.agent.loop import DEFAULT_CALLBACKS

        if "on_assistant_message_start" not in DEFAULT_CALLBACKS:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_assistant_message_start missing from DEFAULT_CALLBACKS "
                    f"keys={list(DEFAULT_CALLBACKS)}"
                ),
            )
        if "on_message_start" not in DEFAULT_CALLBACKS:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_message_start missing from DEFAULT_CALLBACKS "
                    f"(regression: existing once-per-session callback gone) "
                    f"keys={list(DEFAULT_CALLBACKS)}"
                ),
            )
        merged = {**DEFAULT_CALLBACKS, "on_assistant_message_start": lambda: None}
        if merged.get("on_assistant_message_start") is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="caller-supplied on_assistant_message_start not honored",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "on_assistant_message_start present in DEFAULT_CALLBACKS and "
                "overridable by caller"
            ),
        )


# ── Case 2: on_assistant_message_start fires once per LLM call ────────────────

class AgentLoopAssistantMessageStartFiresPerLLMCall(EvalCase):
    name = "agent-loom-assistant-message-start-fires-per-llm-call"
    description = (
        "In a 2-LLM-call scenario (tool_use then end_turn), "
        "on_assistant_message_start fires exactly 2 times and "
        "on_message_start fires exactly 1 time"
    )

    def run(self) -> EvalResult:
        responses = iter([
            MagicMock(
                content=[
                    ToolUseBlock(
                        type="tool_use", name="bash",
                        input={"command": "ls"}, id="tu_99",
                    ),
                ],
                stop_reason="tool_use",
                usage=MagicMock(input_tokens=100, output_tokens=50),
            ),
            MagicMock(
                content=[TextBlock(type="text", text="Done")],
                stop_reason="end_turn",
                usage=MagicMock(input_tokens=100, output_tokens=50),
            ),
        ])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = lambda **kw: next(responses)

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        on_message_start = MagicMock()
        on_assistant_message_start = MagicMock()

        with patch("loom.agent.loop.configure_logging"), \
             patch("loom.agent.loop.trace_mod"), \
             patch("loom.agent.loop.checkpoint"), \
             patch("loom.agent.loop.hooks"), \
             patch("loom.agent.loop.context") as mock_ctx, \
             patch("loom.agent.loop._run_tool_turn",
                   return_value=[
                       {"type": "tool_result", "tool_use_id": "tu_99",
                        "content": "file1.py\nfile2.py", "is_error": False},
                   ]):
            mock_ctx.should_compact.return_value = False
            from loom.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "list files"}],
                llm_client=mock_llm,
                callbacks={
                    "on_message_start": on_message_start,
                    "on_assistant_message_start": on_assistant_message_start,
                },
            )

        if mock_client.messages.create.call_count != 2:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"LLM called {mock_client.messages.create.call_count} "
                    f"times, expected 2"
                ),
            )
        if on_message_start.call_count != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_message_start called {on_message_start.call_count} "
                    f"times, expected 1 (once-per-session)"
                ),
            )
        if on_assistant_message_start.call_count != 2:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_assistant_message_start called "
                    f"{on_assistant_message_start.call_count} times, expected "
                    f"2 (one per LLM call)"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "2 LLM calls → on_message_start=1, "
                "on_assistant_message_start=2 (per-LLM-call)"
            ),
        )


# ── Case 3: on_message_start still once per invocation (regression guard) ────

class AgentLoopMessageStartStillOncePerInvocation(EvalCase):
    name = "agent-loom-message-start-still-once-per-invocation"
    description = (
        "Single-LLM-call scenario: on_message_start fires exactly 1 time AND "
        "on_assistant_message_start fires exactly 1 time (regression guard for "
        "the once-per-session semantic of on_message_start)"
    )

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="Hi")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        on_message_start = MagicMock()
        on_assistant_message_start = MagicMock()

        with patch("loom.agent.loop.configure_logging"), \
             patch("loom.agent.loop.trace_mod"), \
             patch("loom.agent.loop.checkpoint"), \
             patch("loom.agent.loop.hooks"), \
             patch("loom.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            from loom.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hi"}],
                llm_client=mock_llm,
                callbacks={
                    "on_message_start": on_message_start,
                    "on_assistant_message_start": on_assistant_message_start,
                },
            )

        if on_message_start.call_count != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_message_start called {on_message_start.call_count} "
                    f"times, expected 1 (regression: once-per-session broken)"
                ),
            )
        if on_assistant_message_start.call_count != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"on_assistant_message_start called "
                    f"{on_assistant_message_start.call_count} times, expected "
                    f"1 (single LLM call)"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "1 LLM call → on_message_start=1, on_assistant_message_start=1 "
                "(both once)"
            ),
        )


# ── Case 4: TUI wires both callbacks in run_agent_turn ────────────────────────

class AgentTUIAppWiresAssistantMessageStart(EvalCase):
    name = "agent-tui-app-wires-assistant-message-start"
    description = (
        "AgentTUIApp.run_agent_turn wires both on_message_start AND "
        "on_assistant_message_start to AssistantTurnStart so the spinner "
        "resets on every LLM call"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        if not hasattr(AgentTUIApp, "run_agent_turn"):
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp has no run_agent_turn method",
            )

        source = inspect.getsource(AgentTUIApp.run_agent_turn)

        if "on_message_start" not in source:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp.run_agent_turn does not wire on_message_start "
                    "(regression: once-per-session wiring removed)"
                ),
            )
        if "on_assistant_message_start" not in source:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp.run_agent_turn does not wire "
                    "on_assistant_message_start (thinking spinner will not "
                    "reset on subsequent LLM calls)"
                ),
            )
        if "AssistantTurnStart" not in source:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp.run_agent_turn does not post AssistantTurnStart "
                    "from either callback"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp.run_agent_turn wires both on_message_start AND "
                "on_assistant_message_start to AssistantTurnStart"
            ),
        )
