"""Tests for the SessionPicker TUI modal and _on_session_picked callback."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from textual.screen import ModalScreen

from loom.agent.session_store import SessionMeta
from loom.tui.session_picker import SessionPicker


def _make_meta(
    session_id: str = "abc123",
    name: str = "Test Session",
    message_count: int = 5,
    tool_call_count: int = 3,
) -> SessionMeta:
    return SessionMeta(
        session_id=session_id,
        name=name,
        created_at="2026-06-20T10:00:00+00:00",
        updated_at="2026-06-26T12:00:00+00:00",
        message_count=message_count,
        tool_call_count=tool_call_count,
        model="test-model",
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_import_and_instantiation() -> None:
    sp = SessionPicker()
    assert isinstance(sp, ModalScreen)


def test_compose_yields_expected_widgets() -> None:
    sp = SessionPicker()
    assert hasattr(sp, "compose")
    assert callable(sp.compose)


def test_bindings_include_escape_and_shortcuts() -> None:
    binding_str = " ".join(str(b) for b in SessionPicker.BINDINGS)
    assert "escape" in binding_str
    assert "d" in binding_str
    assert "n" in binding_str


def test_default_current_session_id_is_none() -> None:
    sp = SessionPicker()
    assert sp._current_session_id is None


def test_custom_current_session_id() -> None:
    sp = SessionPicker(current_session_id="xyz")
    assert sp._current_session_id == "xyz"


# ---------------------------------------------------------------------------
# Dismiss actions
# ---------------------------------------------------------------------------


def test_action_cancel_dismisses_none() -> None:
    sp = SessionPicker()
    with patch.object(sp, "dismiss") as mock_dismiss:
        sp.action_cancel()
    mock_dismiss.assert_called_once_with(None)


def test_action_new_session_dismisses_new_sentinel() -> None:
    sp = SessionPicker()
    with patch.object(sp, "dismiss") as mock_dismiss:
        sp.action_new_session()
    mock_dismiss.assert_called_once_with("__new__")


# ---------------------------------------------------------------------------
# on_list_view_selected
# ---------------------------------------------------------------------------


def test_on_list_view_selected_dismisses_session_id() -> None:
    sp = SessionPicker()
    sp._index_to_id = {0: "sess-a", 1: "sess-b"}
    mock_event = MagicMock()
    mock_event.item = MagicMock()
    mock_event.list_view = MagicMock()
    # list_view_index returns 1 (the second item).
    mock_event.list_view.children = [MagicMock(), mock_event.item]

    with patch.object(sp, "dismiss") as mock_dismiss:
        sp.on_list_view_selected(mock_event)
    mock_dismiss.assert_called_once_with("sess-b")


def test_on_list_view_selected_ignores_unknown_index() -> None:
    sp = SessionPicker()
    sp._index_to_id = {}  # empty
    mock_event = MagicMock()
    mock_event.item = MagicMock()
    mock_event.list_view = MagicMock()
    mock_event.list_view.children = [mock_event.item]

    with patch.object(sp, "dismiss") as mock_dismiss:
        sp.on_list_view_selected(mock_event)
    mock_dismiss.assert_not_called()


# ---------------------------------------------------------------------------
# _populate_list
# ---------------------------------------------------------------------------


def test_populate_list_shows_empty_message_when_no_sessions() -> None:
    """When there are no sessions, an empty-state ListItem is shown (disabled).

    Tests _populate_list directly on a standalone ListView (without mounting
    the SessionPicker screen) to avoid on_mount interference.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import ListView

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ListView(id="session-picker-list")

    async def _run():
        app = TestApp()
        async with app.run_test() as pilot:
            sp = SessionPicker()  # not mounted — no on_mount
            sp._sessions = []  # no sessions
            lv = app.query_one("#session-picker-list", ListView)
            sp._populate_list(lv)
            await pilot.pause(0.1)
            assert len(lv.children) == 1
            assert lv.children[0].disabled

    asyncio.run(_run())


def test_populate_list_shows_sessions_with_marker() -> None:
    """Sessions are listed; the current session gets a marker.

    Tests _populate_list directly on a standalone ListView.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import ListView

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ListView(id="session-picker-list")

    async def _run():
        app = TestApp()
        async with app.run_test() as pilot:
            sp = SessionPicker(current_session_id="sess-a")  # not mounted
            sp._sessions = [_make_meta("sess-a", "A"), _make_meta("sess-b", "B")]
            lv = app.query_one("#session-picker-list", ListView)
            sp._populate_list(lv)
            await pilot.pause(0.1)
            assert len(lv.children) == 2
            assert sp._index_to_id[0] == "sess-a"
            assert sp._index_to_id[1] == "sess-b"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# action_delete_session
# ---------------------------------------------------------------------------


def test_action_delete_session_removes_selected() -> None:
    """Deleting a non-current session removes it from the store and list."""
    from textual.app import App, ComposeResult
    from textual.widgets import ListView

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ListView(id="session-picker-list")

    async def _run():
        app = TestApp()
        async with app.run_test() as pilot:
            sp = SessionPicker(current_session_id="sess-current")
            app.push_screen(sp)
            await pilot.pause(0.2)
            sp._sessions = [
                _make_meta("sess-current", "Current"),
                _make_meta("sess-other", "Other"),
            ]
            lv = sp.query_one("#session-picker-list", ListView)
            sp._populate_list(lv)
            # Select the second item (sess-other).
            lv.index = 1

            with patch(
                "loom.agent.session_store.SessionStore.delete_session"
            ) as mock_del:
                sp.action_delete_session()
                mock_del.assert_called_once_with("sess-other")

    asyncio.run(_run())


def test_action_delete_session_refuses_current_session() -> None:
    """Deleting the current session is refused (footer shows a message)."""
    from textual.app import App, ComposeResult
    from textual.widgets import ListView

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ListView(id="session-picker-list")

    async def _run():
        app = TestApp()
        async with app.run_test() as pilot:
            sp = SessionPicker(current_session_id="sess-current")
            app.push_screen(sp)
            await pilot.pause(0.2)
            sp._sessions = [_make_meta("sess-current", "Current")]
            lv = sp.query_one("#session-picker-list", ListView)
            sp._populate_list(lv)
            lv.index = 0

            with patch(
                "loom.agent.session_store.SessionStore.delete_session"
            ) as mock_del:
                sp.action_delete_session()
                mock_del.assert_not_called()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# on_mount robustness
# ---------------------------------------------------------------------------


def test_on_mount_does_not_crash_when_list_view_missing() -> None:
    """on_mount should not crash if query_one fails (compose not finished)."""
    from textual.css.query import NoMatches

    sp = SessionPicker()

    def _raise_no_matches(*args, **kwargs):
        raise NoMatches("simulated")

    async def _run():
        with patch.object(sp, "query_one", side_effect=_raise_no_matches):
            await sp.on_mount()  # should not raise

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _on_session_picked callback (app.py)
# ---------------------------------------------------------------------------


def _dismiss_welcome(app, pilot) -> None:
    """Dismiss the welcome modal if present (mirrors model_picker tests)."""
    try:
        from loom.tui.welcome import WelcomeModal

        app.query_one(WelcomeModal).dismiss("")
    except Exception:
        pass


def test_on_session_picked_none_is_noop() -> None:
    """_on_session_picked(None) does nothing (cancelled)."""

    async def _run():
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            _dismiss_welcome(app, pilot)
            await pilot.pause(0.2)
            original_sid = app._session_id
            app._on_session_picked(None)
            await pilot.pause(0.1)
            # Session id unchanged, app still alive.
            assert app._session_id == original_sid
            assert app._exit is False

    asyncio.run(_run())


def test_on_session_picked_new_calls_new_session() -> None:
    """_on_session_picked('__new__') starts a new session."""

    async def _run():
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            _dismiss_welcome(app, pilot)
            await pilot.pause(0.2)

            with patch.object(app, "new_session") as mock_new:
                app._on_session_picked("__new__")
                mock_new.assert_called_once()

    asyncio.run(_run())


def test_on_session_picked_session_id_calls_switch_session() -> None:
    """_on_session_picked(<id>) calls switch_session with that id."""

    async def _run():
        from loom.tui.app import AgentTUIApp

        app = AgentTUIApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            _dismiss_welcome(app, pilot)
            await pilot.pause(0.2)

            with patch.object(app, "switch_session") as mock_switch:
                app._on_session_picked("sometargetid")
                mock_switch.assert_called_once_with("sometargetid")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Slash command wiring
# ---------------------------------------------------------------------------


def test_slash_commands_registry_has_sessions_and_new() -> None:
    """The SLASH_COMMANDS registry includes /sessions and /new."""
    from loom.tui.slash_commands import SLASH_COMMANDS, find_command

    names = {cmd.name for cmd in SLASH_COMMANDS}
    assert "sessions" in names
    assert "new" in names

    assert find_command("sessions") is not None
    assert find_command("new") is not None


def test_handle_sessions_pushes_session_picker() -> None:
    """handle_sessions pushes SessionPicker with the current session_id."""
    from loom.tui.slash_commands import handle_sessions

    app = MagicMock()
    app._session_id = "current-sid"

    asyncio.run(handle_sessions(app, ""))

    app.push_screen.assert_called_once()
    args = app.push_screen.call_args[0]
    assert isinstance(args[0], SessionPicker)
    assert args[0]._current_session_id == "current-sid"


def test_handle_new_calls_app_new_session() -> None:
    """handle_new delegates to app.new_session()."""
    from loom.tui.slash_commands import handle_new

    app = MagicMock()
    asyncio.run(handle_new(app, ""))
    app.new_session.assert_called_once()


def test_handle_resume_with_session_id_calls_switch_session() -> None:
    """handle_resume with an arg calls app.switch_session(target)."""
    from loom.tui.slash_commands import handle_resume

    app = MagicMock()
    app.query_one.return_value = MagicMock()
    asyncio.run(handle_resume(app, "  targetsid  "))
    app.switch_session.assert_called_once_with("targetsid")


def test_handle_resume_no_arg_uses_checkpoint(monkeypatch) -> None:
    """handle_resume with no arg falls back to legacy checkpoint behavior."""
    from loom.tui.slash_commands import handle_resume

    app = MagicMock()
    chat_log = MagicMock()
    app.query_one.return_value = chat_log

    fake_ckpt = {
        "messages": [{"role": "user", "content": "hi"}],
        "tool_call_count": 2,
        "saved_at": "2026-06-26",
    }
    # Patch the checkpoint module that handle_resume imports inline.
    import loom.agent.checkpoint as ckpt_mod

    monkeypatch.setattr(ckpt_mod, "exists", lambda w: True, raising=False)
    monkeypatch.setattr(ckpt_mod, "load", lambda w: fake_ckpt, raising=False)

    asyncio.run(handle_resume(app, ""))

    assert app.history == fake_ckpt["messages"]
    assert app.tool_call_count == 2
    chat_log.append_system_note.assert_called_once()
