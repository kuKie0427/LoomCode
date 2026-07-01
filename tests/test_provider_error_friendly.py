"""Tests for f-provider-error-friendly-display.

When the LLM provider raises ``ProviderError`` (e.g. 302ai 401 Unauthorized
on missing API key), both the TUI (``AgentTUIApp._run_turn`` →
``_handle_turn_exception``) and the bare REPL (``run_repl`` →
``_print_repl_error``) must display a friendly, actionable message and
keep the UI/REPL alive instead of crashing.

Regression for the user-reported 302ai 401 crash: ``ProviderError`` was
raised from the provider's stream method, propagated out of ``agent_loop``
(only try/finally, no except), and crashed the ``_run_turn`` Textual
worker — leaving the TUI stuck in "thinking" state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from loom.agent.providers.types import ProviderError, ProviderErrorCode

# ---------------------------------------------------------------------------
# CLI path — _print_repl_error
# ---------------------------------------------------------------------------


def test_print_repl_error_auth_uses_red(capsys: pytest.CaptureFixture[str]) -> None:
    """Auth/credential errors render a red, actionable message on stderr."""
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(
        ProviderErrorCode.AUTH,
        "302ai: 401 unauthorized — Missing 302 Apikey",
        provider="302ai",
        status_code=401,
    )
    _print_repl_error(exc)

    out = capsys.readouterr()
    assert out.out == "", "friendly error must go to stderr, not stdout"
    assert "API key 缺失或无效" in out.err
    assert "302ai: 401 unauthorized" in out.err
    assert "\033[31m" in out.err, "auth error should be red"


def test_print_repl_error_missing_credential_treated_like_auth(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(ProviderErrorCode.MISSING_CREDENTIAL, "no key on file")
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "API key 缺失或无效" in err
    assert "no key on file" in err


def test_print_repl_error_rate_limit_uses_yellow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(ProviderErrorCode.RATE_LIMIT, "too many requests", retryable=True)
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "限流" in err
    assert "\033[33m" in err, "rate_limit should be yellow (warning)"


def test_print_repl_error_network_uses_yellow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(ProviderErrorCode.NETWORK, "connection reset", retryable=True)
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "网络错误" in err
    assert "\033[33m" in err


def test_print_repl_error_context_overflow_uses_yellow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(ProviderErrorCode.CONTEXT_OVERFLOW, "prompt too long")
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "上下文超长" in err
    assert "/clear" in err and "/compact" in err


def test_print_repl_error_generic_provider_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom.agent.loop import _print_repl_error

    exc = ProviderError(ProviderErrorCode.SERVER, "internal server error")
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "模型调用失败" in err
    assert "server" in err
    assert "internal server error" in err


def test_print_repl_error_non_provider_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-ProviderError exceptions are still printed, with type name."""
    from loom.agent.loop import _print_repl_error

    exc = RuntimeError("kaboom")
    _print_repl_error(exc)

    err = capsys.readouterr().err
    assert "agent_loop 异常" in err
    assert "RuntimeError" in err
    assert "kaboom" in err
    assert "\033[31m" in err


# ---------------------------------------------------------------------------
# TUI path — _handle_turn_exception
# ---------------------------------------------------------------------------


def _make_app():
    """Build an AgentTUIApp with a stubbed LLMClient (no real network)."""
    from loom.tui.app import AgentTUIApp

    app = AgentTUIApp()
    # Replace the LLM with a stub so no provider calls happen.
    fake_llm = MagicMock()
    fake_llm.provider_id = "302ai"
    fake_llm.model_id = "gpt-4o-mini"
    fake_llm.model = "302ai/gpt-4o-mini"
    fake_llm.get_context_window.return_value = 128000
    app.llm = fake_llm
    return app


def test_handle_turn_exception_auth_shows_friendly_systemnote() -> None:
    """Auth ProviderError surfaces a SystemNote with friendly text, no crash.

    Uses a spy on ChatLog.append_system_note rather than querying mounted
    widgets — Textual's async mount (asyncio.create_task) is flaky in
    test teardown, but the *call* (which is what we own) is synchronous
    and deterministic.
    """
    from loom.tui.app import AgentTUIApp
    from loom.tui.chat_log import ChatLog

    async def driver() -> None:
        app = _make_app()
        calls: list[tuple[str, str]] = []

        original = ChatLog.append_system_note

        def spy(self, text: str, severity: str = "info") -> None:
            calls.append((text, severity))
            return original(self, text, severity)

        with patch.object(ChatLog, "append_system_note", spy):
            async with app.run_test(size=(80, 24)) as pilot:
                exc = ProviderError(
                    ProviderErrorCode.AUTH,
                    "302ai: 401 unauthorized — Missing 302 Apikey",
                    provider="302ai",
                    status_code=401,
                )
                await AgentTUIApp._handle_turn_exception(app, exc)
                await pilot.pause(0.05)

        # Engine state must reset to idle (the user-reported crash left it stuck).
        assert app.engine_state == "idle"

        # append_system_note must have been called once with the auth text.
        assert len(calls) == 1, f"expected 1 append_system_note call, got {calls}"
        text, severity = calls[0]
        assert severity == "error"
        assert "无法连接到" in text
        assert "302ai/gpt-4o-mini" in text
        assert "API key 缺失或无效" in text
        assert "302ai: 401 unauthorized" in text

    asyncio.run(driver())


def test_handle_turn_exception_resets_streaming_state() -> None:
    """Even if ChatLog finalization partially fails, engine state still resets."""
    from loom.tui.app import AgentTUIApp

    async def driver() -> None:
        app = _make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            # Sabotage the ChatLog's _dismiss_thinking_widget to raise —
            # _handle_turn_exception should swallow this and still recover.
            chat_log = app.query_one("#chat-log")
            with patch.object(
                type(chat_log),
                "_dismiss_thinking_widget",
                side_effect=RuntimeError("boom"),
            ):
                exc = ProviderError(ProviderErrorCode.NETWORK, "conn reset")
                await AgentTUIApp._handle_turn_exception(app, exc)
                await pilot.pause(0.05)

            assert app.engine_state == "idle"

    asyncio.run(driver())


def test_run_turn_catches_provider_error_and_calls_handler() -> None:
    """End-to-end: _run_turn wraps agent_loop; on ProviderError calls handler."""
    from loom.tui.app import AgentTUIApp

    async def driver() -> None:
        app = _make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            handler_calls: list[Exception] = []

            async def fake_handler(exc: Exception) -> None:
                handler_calls.append(exc)

            with patch.object(
                AgentTUIApp, "_handle_turn_exception", side_effect=fake_handler
            ):
                with patch(
                    "loom.tui.app.asyncio.to_thread",
                    side_effect=ProviderError(
                        ProviderErrorCode.AUTH, "missing key"
                    ),
                ):
                    # _start_new_session requires SessionStore; stub it.
                    with patch.object(
                        AgentTUIApp, "_start_new_session", return_value="s1"
                    ):
                        app._run_turn({})
                        await pilot.pause(0.1)

            assert len(handler_calls) == 1, (
                f"expected handler called once, got {len(handler_calls)}"
            )
            assert isinstance(handler_calls[0], ProviderError)
            assert handler_calls[0].code == ProviderErrorCode.AUTH

    asyncio.run(driver())


def test_handle_turn_exception_non_provider_error_still_handled() -> None:
    """Generic exceptions are surfaced with type name + message, no crash."""
    from loom.tui.app import AgentTUIApp
    from loom.tui.chat_log import ChatLog

    async def driver() -> None:
        app = _make_app()
        calls: list[tuple[str, str]] = []

        original = ChatLog.append_system_note

        def spy(self, text: str, severity: str = "info") -> None:
            calls.append((text, severity))
            return original(self, text, severity)

        with patch.object(ChatLog, "append_system_note", spy):
            async with app.run_test(size=(80, 24)) as pilot:
                exc = RuntimeError("unexpected boom")
                await AgentTUIApp._handle_turn_exception(app, exc)
                await pilot.pause(0.05)

        assert app.engine_state == "idle"
        assert len(calls) == 1
        text, severity = calls[0]
        assert severity == "error"
        assert "agent_loop 异常" in text
        assert "RuntimeError" in text
        assert "unexpected boom" in text

    asyncio.run(driver())
