"""Tests for the multi-LLM-call thinking display.

Reproduces the bug where the agent's thinking was only shown for the first
LLM call within a session. The second LLM call (after a tool_use round)
appended its thinking text to the stale, hidden ``ThinkingDisplay`` from
the first call, so the user never saw it.

The fix is in two parts:
1. ``loom/agent/loop.py`` fires a new ``on_assistant_message_start`` callback
   before **each** LLM call within the while loop. The existing
   ``on_message_start`` keeps its once-per-session semantic.
2. ``loom/tui/app.py`` wires ``on_assistant_message_start`` to the same
   ``AssistantTurnStart`` message that triggers ``show_thinking_spinner``
   on the chat log, so the spinner (and a fresh ``ThinkingDisplay``) is
   set up for every round of reasoning.
"""

from __future__ import annotations

import asyncio
import inspect

from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import ChatLog, ThinkingDisplay


def test_run_agent_turn_callbacks_include_per_message_start():
    """The TUI's run_agent_turn must wire on_assistant_message_start so
    the spinner appears for every LLM call, not just the first.
    """
    src = inspect.getsource(AgentTUIApp.run_agent_turn)
    assert "on_assistant_message_start" in src, (
        "AgentTUIApp.run_agent_turn must wire the per-LLM-call "
        "on_assistant_message_start callback so thinking displays "
        "are set up for every round of reasoning"
    )
    assert "on_message_start" in src, (
        "AgentTUIApp.run_agent_turn must still wire the once-per-"
        "session on_message_start callback"
    )


def test_show_thinking_spinner_creates_new_display_each_call():
    """Verify that the second thinking round creates a fresh, visible
    ThinkingDisplay — not an append to a hidden one.
    """

    async def driver():
        from loom.tui.messages import (
            AssistantTurnEnd,
            AssistantTurnStart,
            TextDelta,
            ThinkingDelta,
        )

        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("first")
            await pilot.pause(0.1)

            app.post_message(AssistantTurnStart())
            await pilot.pause(0.1)
            app.post_message(ThinkingDelta("First: "))
            await pilot.pause(0.1)
            app.post_message(ThinkingDelta("reasoning one"))
            await pilot.pause(0.1)
            app.post_message(TextDelta("First answer"))
            await pilot.pause(0.1)
            app.post_message(AssistantTurnEnd(0, 0))
            await pilot.pause(0.1)

            first_display = chat_log._thinking_display
            assert first_display is not None, (
                "first LLM call should have created a ThinkingDisplay"
            )

            app.post_message(AssistantTurnStart())
            await pilot.pause(0.1)
            app.post_message(ThinkingDelta("Second: "))
            await pilot.pause(0.1)
            app.post_message(ThinkingDelta("reasoning two"))
            await pilot.pause(0.1)
            app.post_message(TextDelta("Second answer"))
            await pilot.pause(0.1)
            app.post_message(AssistantTurnEnd(0, 0))
            await pilot.pause(0.1)

            second_display = chat_log._thinking_display
            assert second_display is not first_display, (
                "second LLM call must create a fresh ThinkingDisplay; "
                f"got the same id as the first: {first_display!r}"
            )
            assert "Second" in second_display._markdown
            assert "First" not in second_display._markdown, (
                "second ThinkingDisplay must not contain first-round text"
            )

    asyncio.run(driver())


def test_thinking_display_widget_count_grows_with_llm_calls():
    """A real conversation has multiple LLM calls (think → tool → think →
    answer). The chat log should end up with one ``ThinkingDisplay`` widget
    per thinking round, not just the first one.
    """

    async def driver():
        from loom.tui.messages import (
            AssistantTurnEnd,
            AssistantTurnStart,
            TextDelta,
            ThinkingDelta,
        )

        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            chat_log = app.query_one(ChatLog)
            await chat_log.append_user_message("multi-round question")
            await pilot.pause(0.1)

            async def think_and_answer(thinking: str, answer: str) -> None:
                app.post_message(AssistantTurnStart())
                await pilot.pause(0.1)
                app.post_message(ThinkingDelta(thinking))
                await pilot.pause(0.1)
                app.post_message(TextDelta(answer))
                await pilot.pause(0.1)
                app.post_message(AssistantTurnEnd(0, 0))
                await pilot.pause(0.1)

            await think_and_answer("thinking #1", "answer #1")
            await think_and_answer("thinking #2", "answer #2")
            await think_and_answer("thinking #3", "answer #3")

            displays = list(chat_log.query(ThinkingDisplay))
            assert len(displays) == 3, (
                "one ThinkingDisplay widget should be mounted per LLM call; "
                f"got {len(displays)} displays after 3 rounds"
            )

    asyncio.run(driver())
