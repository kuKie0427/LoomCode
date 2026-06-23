"""Tests for the P4b auto-connect prompt behavior.

Tests the startup credential check, the ModelPicker auto-jump to
AuthInputModal, and the stream error hint appended by loop.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from loom.tui.auth_input import AuthInputModal
from loom.tui.connect_provider import ConnectProviderModal
from loom.tui.model_picker import ModelPicker


def test_startup_with_no_credentials_pushes_modal() -> None:
    """Mock empty credentials → _check_credentials_on_startup calls push_screen
    with ConnectProviderModal.
    """
    with patch("loom.tui.app.resolve_model", return_value="test/model"), patch(
        "loom.tui.app.LLMClient"
    ):
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()

    with (
        patch("loom.agent.credential.credentials.all", return_value={}),
        patch.object(app, "push_screen") as mock_push,
    ):
        app._check_credentials_on_startup()

    mock_push.assert_called_once()
    args, _ = mock_push.call_args
    assert isinstance(args[0], ConnectProviderModal)


def test_startup_with_credentials_does_not_push_modal() -> None:
    """Mock non-empty credentials → push_screen NOT called."""
    with patch("loom.tui.app.resolve_model", return_value="test/model"), patch(
        "loom.tui.app.LLMClient"
    ):
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()

    fake_cred = MagicMock()
    with (
        patch(
            "loom.agent.credential.credentials.all",
            return_value={"anthropic": fake_cred},
        ),
        patch.object(app, "push_screen") as mock_push,
    ):
        app._check_credentials_on_startup()

    mock_push.assert_not_called()


def test_model_picker_unconnected_provider_pushes_auth() -> None:
    """ModelPicker select unconnected provider → pushes AuthInputModal.

    Verify that dismiss is NOT called when the provider has no credential.
    """
    mp = ModelPicker()
    mock_event = MagicMock()
    mock_event.item.id = "model:openai/gpt-4o"

    mock_app = MagicMock()
    with (
        patch("loom.agent.credential.credentials.get", return_value=None),
        patch.object(
            ModelPicker, "app", new_callable=PropertyMock, return_value=mock_app
        ),
        patch.object(mp, "dismiss") as mock_dismiss,
    ):
        mp.on_list_view_selected(mock_event)

    mock_app.push_screen.assert_called_once()
    args, _ = mock_app.push_screen.call_args
    assert isinstance(args[0], AuthInputModal)
    mock_dismiss.assert_not_called()


def test_auth_error_appends_connect_hint(tmp_path, monkeypatch) -> None:
    """agent_loop streaming path: auth error_code → /connect hint appended
    to the user-visible text chunk. Conversely, a non-auth error code MUST
    NOT append the hint.

    Drives the real agent_loop with a fake provider whose stream yields an
    ``error`` StreamEvent — this exercises the inline handler in
    loom/agent/loop.py (lines ~373-389). Previously this test copied the
    inline logic into a local helper and asserted on the copy, providing
    zero regression protection. Now it asserts against the actual
    on_text_delta chunk + the final assistant message.
    """
    import loom.agent.loop as loop_mod
    from loom.agent.config import CheckpointConfig, HarnessConfig, LLMConfig
    from loom.agent.permissions import DEFAULT_POLICY
    from loom.agent.providers.types import ProviderErrorCode, StreamEvent

    def _run(error_code: str | None) -> tuple[list[str], str]:
        monkeypatch.chdir(tmp_path)
        cfg = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            llm=LLMConfig.from_defaults(),
            max_turns=5,
        )

        fake_llm = MagicMock()
        fake_llm.get_context_window.return_value = 200000
        fake_llm.model = "anthropic/claude-sonnet-4-5"

        def fake_stream(system, messages, tools, max_tokens):
            yield StreamEvent(
                kind="error",
                error_code=error_code,
                error_message="provider returned an error event",
            )

        fake_trace = MagicMock()
        fake_trace.record = lambda ev, **kw: None
        monkeypatch.setattr(loop_mod.trace_mod, "current", lambda: fake_trace)
        monkeypatch.setattr(loop_mod.trace_mod, "stop", lambda: None)

        from loom.agent.hooks import Hooks
        hooks = Hooks(
            loop_mod._active_config.policy,
            frozenset(),
            asker=lambda *a, **k: True,
        )
        monkeypatch.setattr(loop_mod, "hooks", hooks)
        loop_mod.apply_config(cfg)

        text_chunks: list[str] = []
        callbacks = {"on_text_delta": lambda chunk: text_chunks.append(chunk)}

        msgs: list[dict] = [{"role": "user", "content": "hi"}]
        loop_mod.agent_loop(
            msgs,
            llm_client=fake_llm,
            callbacks=callbacks,
            stream_text=fake_stream,
        )

        last_assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        assistant_text = ""
        for block in last_assistant["content"]:
            if getattr(block, "type", None) == "text":
                assistant_text += block.text
        return text_chunks, assistant_text

    chunks, assistant_text = _run(ProviderErrorCode.AUTH)
    assert any("/connect" in c for c in chunks), (
        f"on_text_delta should have received a chunk with /connect hint, got {chunks}"
    )
    assert "/connect" in assistant_text, (
        f"final assistant message should contain /connect hint, got {assistant_text!r}"
    )

    for code in (ProviderErrorCode.TIMEOUT, ProviderErrorCode.RATE_LIMIT, None):
        chunks, assistant_text = _run(code)
        assert not any("/connect" in c for c in chunks), (
            f"non-auth error code {code!r} should NOT add /connect hint, got {chunks}"
        )
        assert "/connect" not in assistant_text
        assert "LLM error" in assistant_text
