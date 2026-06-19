"""Eval cases verifying the PreCompact hook event."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

from loom.agent.hooks import HOOKS, Hooks
from loom.eval.runner import EvalCase, EvalResult


def _save_hooks() -> dict:
    return copy.deepcopy(HOOKS)


def _restore_hooks(saved: dict) -> None:
    HOOKS.clear()
    HOOKS.update(saved)


class PreCompactEventKeyInHooksDict(EvalCase):
    name = "pre-compact-event-key-in-hooks-dict"
    description = "HOOKS dict contains PreCompact key between PostToolUse and AgentStop"

    def run(self) -> EvalResult:
        keys = list(HOOKS.keys())
        if "PreCompact" not in HOOKS:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="HOOKS dict missing PreCompact key",
            )
        try:
            post_idx = keys.index("PostToolUse")
            pre_idx = keys.index("PreCompact")
            stop_idx = keys.index("AgentStop")
        except ValueError as e:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"Missing expected key in HOOKS: {e}",
            )
        if not (post_idx < pre_idx < stop_idx):
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"PreCompact at index {pre_idx} not between "
                    f"PostToolUse ({post_idx}) and AgentStop ({stop_idx})"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"PreCompact at index {pre_idx} between PostToolUse ({post_idx}) and AgentStop ({stop_idx})",
        )


class PreCompactTriggerRunsCallbacks(EvalCase):
    name = "pre-compact-trigger-runs-callbacks"
    description = "trigger_hooks('PreCompact') runs registered callbacks"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        callback = MagicMock()
        Hooks().register_hook("PreCompact", callback)
        Hooks().trigger_hooks("PreCompact", [], 0)
        try:
            callback.assert_called_once()
        except AssertionError:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"callback called {callback.call_count} time(s), expected 1",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"callback called once with args: {callback.call_args!r}",
        )


class PreCompactCallbackReceivesArgs(EvalCase):
    name = "pre-compact-callback-receives-args"
    description = "PreCompact callback receives (event, messages, last_input_tokens)"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        captured_messages: list | None = None
        captured_tokens: int | None = None

        def capture(event: str, messages: list, last_input_tokens: int) -> None:
            nonlocal captured_messages, captured_tokens
            captured_messages = messages
            captured_tokens = last_input_tokens

        Hooks().register_hook("PreCompact", capture)
        Hooks().trigger_hooks("PreCompact", ["msg1", "msg2"], 42)

        if captured_messages != ["msg1", "msg2"]:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"expected messages=['msg1', 'msg2'], got {captured_messages!r}",
            )
        if captured_tokens != 42:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"expected last_input_tokens=42, got {captured_tokens!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="callback received messages=['msg1', 'msg2'] and last_input_tokens=42",
        )


class PreCompactFiresBeforeAutocompact(EvalCase):
    name = "pre-compact-fires-before-autocompact"
    description = "PreCompact event fires before autocompact is called when should_compact returns True"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        from anthropic.types import MessageParam

        from loom.agent.context import Context

        call_order: list[str] = []

        Hooks().register_hook(
            "PreCompact", lambda event, *args: call_order.append("pre_compact")
        )

        ctx = Context()
        ctx.last_input_tokens = 10000

        # Mock autocompact to avoid real LLM call
        original_autocompact = ctx.autocompact

        def mock_autocompact(*args: object, **kwargs: object) -> None:
            call_order.append("autocompact")

        ctx.autocompact = mock_autocompact  # type: ignore[method-assign]

        messages: list[MessageParam] = [{"role": "user", "content": "Hello, can you help me?"}]
        context_window = 10000

        if not ctx.should_compact(messages, context_window):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="should_compact returned False even with last_input_tokens=10000 and context_window=10000",
            )

        Hooks().trigger_hooks("PreCompact", messages, ctx.last_input_tokens)
        ctx.autocompact(messages, None, "model", context_window)

        ctx.autocompact = original_autocompact  # type: ignore[method-assign]

        if "pre_compact" not in call_order:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="PreCompact callback was not triggered",
            )
        if "autocompact" not in call_order:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="autocompact was not called",
            )

        pre_idx = call_order.index("pre_compact")
        auto_idx = call_order.index("autocompact")
        if pre_idx >= auto_idx:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"PreCompact ({pre_idx}) did not fire before autocompact ({auto_idx}); order={call_order}",
            )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"PreCompact fired before autocompact; call order: {call_order}",
        )
