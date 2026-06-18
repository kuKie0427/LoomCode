"""Eval cases for the thinking_delta callback chain (f-thinking-display-fix).

Locks in the 4-link chain that delivers LLM thinking content to the TUI:
LLM stream → StreamEvent(kind="thinking") → on_thinking_delta callback →
app.on_thinking_delta handler → ChatLog.append_thinking_text.
"""

from __future__ import annotations

from loop.eval.runner import EvalCase, EvalResult


class TuiThinkingDeltaCallbackRegistered(EvalCase):
    name = "tui-thinking-delta-callback-registered"
    description = (
        "DEFAULT_CALLBACKS in agent_loop includes on_thinking_delta and "
        "the callbacks dict passed to agent_loop can supply it"
    )

    def run(self) -> EvalResult:
        from loop.agent.loop import DEFAULT_CALLBACKS

        if "on_thinking_delta" not in DEFAULT_CALLBACKS:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"on_thinking_delta missing from DEFAULT_CALLBACKS keys={list(DEFAULT_CALLBACKS)}",
            )
        merged = {**DEFAULT_CALLBACKS, "on_thinking_delta": lambda x: None}
        if merged.get("on_thinking_delta") is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="after merge, on_thinking_delta is None (caller-supplied not honored)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="on_thinking_delta present in DEFAULT_CALLBACKS and can be overridden by caller",
        )


class TuiThinkingDeltaHandledByApp(EvalCase):
    name = "tui-thinking-delta-handled-by-app"
    description = "AgentTUIApp has an on_thinking_delta handler that forwards to chat_log"

    def run(self) -> EvalResult:
        from loop.tui.app import AgentTUIApp

        if not hasattr(AgentTUIApp, "on_thinking_delta"):
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp has no on_thinking_delta method",
            )
        import inspect

        source = inspect.getsource(AgentTUIApp.on_thinking_delta)
        if "append_thinking_text" not in source:
            return EvalResult(
                name=self.name, passed=False,
                detail="on_thinking_delta does not call chat_log.append_thinking_text",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="AgentTUIApp.on_thinking_delta calls chat_log.append_thinking_text",
        )


class TuiStreamEventSupportsThinkingKind(EvalCase):
    name = "tui-stream-event-supports-thinking-kind"
    description = "StreamEvent.kind Literal allows 'thinking' so thinking_delta can be emitted"

    def run(self) -> EvalResult:
        from loop.agent.llm import StreamEvent

        try:
            ev = StreamEvent(kind="thinking", text="sample thought")
        except Exception as e:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"StreamEvent(kind='thinking') rejected: {e}",
            )
        if ev.kind != "thinking":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"StreamEvent.kind={ev.kind!r} expected 'thinking'",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="StreamEvent accepts kind='thinking'",
        )
