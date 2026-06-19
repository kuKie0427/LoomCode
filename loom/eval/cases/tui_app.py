"""Eval cases for the Textual TUI (f-tui-textual-app).

Phase F2 Task 9 — 5 EvalCase classes that lock in the TUI's public contract
without testing visual snapshots (F3) or real LLM calls.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: package imports ──────────────────────────────────────────────────

class TuiPackageImports(EvalCase):
    name = "tui-package-imports"
    description = "loop.tui and AgentTUIApp are importable"

    def run(self) -> EvalResult:
        try:
            import loom.tui  # noqa: F811, F401
            from loom.tui.app import AgentTUIApp  # noqa: F401
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Import failed: {type(exc).__name__}: {exc}",
            )
        return EvalResult(name=self.name, passed=True,
                          detail="loop.tui + AgentTUIApp importable")


# ── Case 2: required attributes ──────────────────────────────────────────────

class TuiAppHasRequiredMethods(EvalCase):
    name = "tui-app-has-required-methods"
    description = "AgentTUIApp exposes 10 required attributes/methods"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        required = [
            "compose",
            "BINDINGS",
            "__init__",
            "action_quit",
            "on_assistant_turn_start",
            "on_text_delta",
            "on_tool_use_started",
            "on_tool_use_completed",
            "on_compact_occurred",
            "on_assistant_turn_end",
        ]
        missing = [m for m in required if not hasattr(AgentTUIApp, m)]
        if missing:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Missing attributes: {missing}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"All {len(required)} attributes present",
        )


# ── Case 3: Message subclasses ───────────────────────────────────────────────

class TuiMessagesDefined(EvalCase):
    name = "tui-messages-defined"
    description = "6 Message classes are textual.message.Message subclasses"

    def run(self) -> EvalResult:
        from textual.message import Message

        from loom.tui.messages import (
            AssistantTurnEnd,
            AssistantTurnStart,
            CompactOccurred,
            TextDelta,
            ToolUseCompleted,
            ToolUseStarted,
        )

        classes = [
            AssistantTurnStart,
            TextDelta,
            ToolUseStarted,
            ToolUseCompleted,
            CompactOccurred,
            AssistantTurnEnd,
        ]
        for cls in classes:
            if not issubclass(cls, Message):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"{cls.__name__} is not a Message subclass",
                )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"All {len(classes)} messages are Message subclasses",
        )


# ── Case 4: apply_config + SessionEnd ────────────────────────────────────────

class TuiAppCallsApplyConfigAndSessionEnd(EvalCase):
    name = "tui-app-calls-apply-config-and-session-end"
    description = "AgentTUIApp.__init__ calls apply_config; action_quit fires SessionEnd"

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
        with patch("loom.agent.loop.apply_config") as mock_apply, \
             patch("loom.agent.loop.hooks") as mock_hooks, \
             patch("loom.agent.user_hooks.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop._active_config") as mock_config:
            mock_config.run_init_sh_on_session_end = False

            from loom.tui.app import AgentTUIApp

            app = AgentTUIApp(resume=False)

            if mock_apply.call_count < 1:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="apply_config was not called in __init__",
                )

            # Reset mock to isolate SessionEnd assertion
            mock_hooks.trigger_hooks.reset_mock()

            with patch.object(AgentTUIApp, "exit"):
                asyncio.run(app.action_quit())

            session_end_calls = [
                c for c in mock_hooks.trigger_hooks.call_args_list
                if c.args and c.args[0] == "SessionEnd"
            ]
            if not session_end_calls:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="SessionEnd hook not triggered on quit",
                )

        return EvalResult(
            name=self.name, passed=True,
            detail="apply_config called in __init__; SessionEnd triggered on quit",
        )


# ── Case 5: test-mode pilot ──────────────────────────────────────────────────

class TuiLaunchesInTestMode(EvalCase):
    name = "tui-launches-in-test-mode"
    description = "AgentTUIApp.run_test() + pilot.press works without exceptions"

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
        from loom.tui.app import AgentTUIApp

        async def _pilot_test() -> None:
            with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}):
                app = AgentTUIApp()
            async with app.run_test() as pilot:
                await pilot.press("h", "e", "l", "l", "o")
                await pilot.pause()

        try:
            asyncio.run(_pilot_test())
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"{type(exc).__name__}: {exc}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="AgentTUIApp starts and accepts input via pilot",
        )
