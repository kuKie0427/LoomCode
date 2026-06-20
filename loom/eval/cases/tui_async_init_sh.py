"""Eval cases for TUI async init.sh fire-and-forget behavior (Phase P2).

Case 1 — tui-quit-does-not-block-on-init-sh:
    action_quit returns in <3s even with a 60s init.sh (fire-and-forget).

Case 2 — tui-init-sh-completion-banner-runs:
    _on_init_sh_complete writes "[init.sh: pass (exit 0)]" banner via
    chat_log.append_system_note when init.sh exits with rc=0.
"""

from __future__ import annotations

import asyncio
import os
import subprocess as sp
import threading
import time
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


class TuiQuitDoesNotBlockOnInitSh(EvalCase):
    name = "tui-quit-does-not-block-on-init-sh"
    description = (
        "action_quit returns in <3s even when init.sh takes 60s "
        "(proves fire-and-forget via daemon thread)"
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
        def slow_schedule(
            workdir, config, *, on_complete=None, on_failure_log=None,
            timeout=120.0, stop_event=None,
        ):
            def _slow() -> None:
                time.sleep(60)
            t = threading.Thread(target=_slow, daemon=True)
            t.start()
            return t

        slow_schedule.__name__ = "schedule_init_sh_on_session_end"

        with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop.schedule_init_sh_on_session_end", slow_schedule), \
             patch("loom.agent.loop._active_config") as mock_config, \
             patch("loom.agent.loop.hooks"):

            mock_config.run_init_sh_on_session_end = True

            from loom.tui.app import AgentTUIApp

            app = AgentTUIApp(resume=False)

            with patch.object(AgentTUIApp, "exit"):
                t0 = time.monotonic()
                asyncio.run(app.action_quit())
                elapsed = time.monotonic() - t0

                if elapsed >= 3.0:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=(
                            f"action_quit took {elapsed:.2f}s (≥ 3s threshold) — "
                            "blocked on init.sh instead of fire-and-forget"
                        ),
                    )
                return EvalResult(
                    name=self.name, passed=True,
                    detail=(
                        f"action_quit returned in {elapsed:.2f}s even with "
                        "init.sh=sleep 60 daemon thread (fire-and-forget confirmed)"
                    ),
                )


class TuiInitShCompletionBannerRuns(EvalCase):
    name = "tui-init-sh-completion-banner-runs"
    description = (
        "When _on_init_sh_complete receives rc=0, it writes "
        "'[init.sh: pass (exit 0)]' via chat_log.append_system_note"
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
        captured_notes: list[str] = []

        async def _run() -> None:
            with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}):
                from loom.tui.app import AgentTUIApp
                from loom.tui.chat_log import ChatLog

                app = AgentTUIApp()

            async with app.run_test() as pilot:
                chat_log = app.query_one(ChatLog)
                original = chat_log.append_system_note

                def spy(text: str) -> None:
                    captured_notes.append(text)
                    return original(text)

                chat_log.append_system_note = spy  # type: ignore[method-assign]

                result = sp.CompletedProcess(
                    args=["init.sh"], returncode=0,
                    stdout="ok\n", stderr="",
                )
                app._on_init_sh_complete(result, "")
                await pilot.pause()

        try:
            asyncio.run(_run())
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Exception during test: {type(exc).__name__}: {exc}",
            )

        if not any("[init.sh: pass (exit 0)]" in n for n in captured_notes):
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Banner not found in captured notes. "
                    f"Captured: {captured_notes!r}"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "Banner '[init.sh: pass (exit 0)]' written via "
                "chat_log.append_system_note"
            ),
        )
