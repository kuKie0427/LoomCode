"""Regression guard: thinking state resets without mounting extra widget.

``_reset_thinking_state`` is called on each ``AssistantTurnStart`` and
is inherently idempotent (clears accumulator + display ref). The
``ThinkingMarker`` is mounted later by ``append_thinking_text`` when
thinking delta events arrive — NOT by ``AssistantTurnStart``.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


class ThinkingSpinnerNoDoubleMountWithinTurn(EvalCase):
    name = "tui-thinking-spinner-no-double-mount-within-turn"
    description = (
        "Posting AssistantTurnStart twice (on_message_start then "
        "on_assistant_message_start) in the same turn resets thinking "
        "state without mounting a widget — the widget is created later "
        "by append_thinking_text when thinking deltas arrive"
    )

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = "test-key-for-eval"

    def teardown(self) -> None:
        if self._old_key:
            os.environ["ANTHROPIC_API_KEY"] = self._old_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def run(self) -> EvalResult:
        reset_calls: list[int] = []
        mount_calls: list[int] = []

        async def _run() -> None:
            with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}):
                from loom.tui.app import AgentTUIApp
                from loom.tui.chat_log import ChatLog

                app = AgentTUIApp()

            async with app.run_test() as pilot:
                chat_log = app.query_one(ChatLog)
                original_reset = chat_log._reset_thinking_state
                original_mount = chat_log._mount_thinking_widget

                def spy_reset() -> None:
                    reset_calls.append(1)
                    return original_reset()

                def spy_mount() -> None:
                    mount_calls.append(1)
                    return original_mount()

                chat_log._reset_thinking_state = spy_reset  # type: ignore[method-assign]
                chat_log._mount_thinking_widget = spy_mount  # type: ignore[method-assign]

                from loom.tui.messages import AssistantTurnStart

                app.post_message(AssistantTurnStart())
                await pilot.pause()
                app.post_message(AssistantTurnStart())
                await pilot.pause()

        try:
            asyncio.run(_run())
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Exception during test: {type(exc).__name__}: {exc}",
            )

        if len(reset_calls) != 2:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"_reset_thinking_state called {len(reset_calls)} times "
                    f"for 2 AssistantTurnStart posts (expected 2)"
                ),
            )
        if len(mount_calls) != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"_mount_thinking_widget called {len(mount_calls)} times "
                    f"from AssistantTurnStart handler (expected 0 — "
                    f"marker is mounted by append_thinking_text)"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "2 AssistantTurnStart posts → 2 _reset_thinking_state "
                "calls, 0 _mount_thinking_widget calls "
                "(state reset is idempotent; marker mounted on first thinking delta)"
            ),
        )
