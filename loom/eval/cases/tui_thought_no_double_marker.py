"""Regression guard for f-tui-thinking-per-llm-call: show_thinking_spinner
must be idempotent within a turn. Both on_message_start and
on_assistant_message_start fire per LLM call and both post
AssistantTurnStart, so without idempotency each call dismisses+remounts
the thinking widget, producing an extra '◦ thought · 0s' line per turn.
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
        "on_assistant_message_start) in the same turn mounts exactly one "
        "thinking widget — regression guard for the extra '◦ thought · 0s' "
        "marker that appeared before the idempotency fix"
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
        mount_calls: list[int] = []

        async def _run() -> None:
            with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}):
                from loom.tui.app import AgentTUIApp
                from loom.tui.chat_log import ChatLog

                app = AgentTUIApp()

            async with app.run_test() as pilot:
                chat_log = app.query_one(ChatLog)
                original_mount = chat_log._mount_thinking_widget

                def spy_mount() -> None:
                    mount_calls.append(1)
                    return original_mount()

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

        if len(mount_calls) != 1:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"_mount_thinking_widget called {len(mount_calls)} times "
                    f"for 2 AssistantTurnStart posts in same turn, "
                    f"expected 1 (regression: extra ◦ thought · 0s "
                    f"marker on every turn)"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "2 AssistantTurnStart posts → 1 _mount_thinking_widget "
                "call (idempotent within turn, no extra marker)"
            ),
        )
