"""Textual TUI for the loom coding agent.

Wraps agent_loop with streaming, tool cards, and lifecycle hooks.
Callbacks use post_message to safely cross from the worker thread
into the main Textual event loop.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from typing import Literal, cast

from loguru import logger
from textual import messages as textual_messages
from textual import work
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Vertical
from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.reactive import reactive
from textual.theme import Theme

from loom.agent.llm import LLMClient
from loom.agent.loop import WORKDIR, agent_loop
from loom.agent.model_resolver import resolve_model
from loom.agent.model_state import ModelState, ProjectConfig
from loom.tui import kitty_patch  # noqa: F401  # side-effect: patches XTermParser
from loom.tui.chat_log import ChatLog
from loom.tui.completer import CommandCompleter
from loom.tui.composer import Composer
from loom.tui.header import Header, HeaderOverlay, HeaderState, MCPServer, Subagent, TodoItem
from loom.tui.messages import (
    AssistantTurnEnd,
    AssistantTurnStart,
    CompactOccurred,
    SubagentEnd,
    SubagentStart,
    TextDelta,
    ThinkingDelta,
    TodoUpdate,
    ToolUseCompleted,
    ToolUseStarted,
)
from loom.tui.status_bar import EngineState, StatusBar
from loom.tui.welcome import WelcomeModal

# Mapping from agent_loop's todo status (string) to the TUI Header's
# TodoItem.state (Literal["pending", "active", "done"]). The agent uses
# "in_progress" and "completed" (Claude Code style); the TUI uses shorter
# "active" and "done" for compact rendering in the 1-line Header.
_TODO_STATE_FROM_AGENT: dict[str, Literal["pending", "active", "done"]] = {
    "pending": "pending",
    "in_progress": "active",
    "completed": "done",
}


# loom-ink: the single canonical color identity. Every hex is ported 1:1 from
# the visual reference docs/tui-design.html (:root custom properties) so the
# running app faithfully realizes the documented "ink & sage" aesthetic. This
# is the ONLY place colors live — widgets reference theme tokens, never hex.
# $accent-light is a derived sage (lighter than $accent) for the "upper face"
# of 3D-extruded wordmarks in the WelcomeBanner — see §9.1 for the gradient
# that drives the opencode-style 3D stencil mark.
_LOOM_INK_THEME = Theme(
    name="loom-ink",
    primary="#5b8a72",
    secondary="#4a8a8a",
    accent="#5b8a72",
    warning="#8a7a3b",
    error="#8a3b3b",
    success="#4a8a5b",
    foreground="#c5cdd8",
    background="#0c0e12",
    surface="#0a0d11",
    panel="#13161c",
    dark=True,
    variables={
        "text-muted": "#5c6570",
        "text-dim": "#5c6570",
        "text-faint": "#3a4048",
        "border": "#1e2328",
        "hairline": "#1a1e24",
        "accent-dim": "#2d4539",
        "accent-light": "#84ad9a",
    },
)




class AgentTUIApp(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }
    /* Header CSS lives in loom.tui.header.Header.DEFAULT_CSS (single source
       of truth — see HeaderSectionButton.width: 1fr for click-target grid). */
    HeaderOverlay {
        dock: top;
        height: auto;
        max-height: 16;
        overflow-y: auto;
        background: $panel 97%;
        padding: 1 2;
        border-bottom: solid $border;
    }
    #chat-log {
        height: 1fr;
        background: $background;
        padding: 1 2 0 2;
        overflow-y: auto;
        overflow-x: hidden;
        scrollbar-background: $background;
        scrollbar-color: $text-muted;
        scrollbar-color-hover: $accent;
        scrollbar-size-vertical: 3;
    }
    #chat-log:focus {
        background: $boost 5%;
    }
    #chat-log:focus-within {
        background: $boost 3%;
    }
    #chrome {
        dock: bottom;
        height: auto;
        background: $surface;
        margin: 0 2 1 2;
        padding: 0;
        border: none;
    }
    #chrome:focus-within {
        background: $boost;
    }
    #status-bar {
        height: 1;
        background: transparent;
        color: $text-muted;
        padding: 0 1;
    }
    #composer {
        height: auto;
        max-height: 8;
        min-height: 3;
        background: transparent;
        border: none;
        padding: 0 1 1 1;
        margin: 0;
        color: $text;
    }
    #composer .text-area--cursor {
        background: $accent;
        color: $background;
    }
    #composer Text {
        background: transparent;
    }
    """

    _main_loop: asyncio.AbstractEventLoop | None
    streaming_text: reactive[str] = reactive("")
    tool_call_count: reactive[int] = reactive(0)
    user_turn_count: reactive[int] = reactive(0)
    ctx_tokens: reactive[int] = reactive(0)
    engine_state: reactive[EngineState] = reactive("idle")

    BINDINGS = [
        ("ctrl+c", "cancel_stream", "Cancel"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+l", "clear_screen", "Clear"),
        ("ctrl+p", "show_command_palette", "Commands"),
        ("escape", "collapse_header", "Collapse header"),
    ]

    def __init__(self, resume: bool = False, model: str | None = None):
        super().__init__()
        self.resume = resume
        # Skip the welcome modal when running under pytest (detected by
        # looking for the pytest module in sys.modules, which is set by
        # pytest before any test module is imported).
        import sys

        self._skip_welcome: bool = "pytest" in sys.modules
        resolved = resolve_model(
            workdir=WORKDIR,
            cli_model=model,                                       # --model flag
            env_model=os.getenv("MODEL"),                          # $MODEL
            config_model=ProjectConfig(WORKDIR).model,             # .minicode/config.json (向上 walk)
            state_model=ModelState(WORKDIR).default_model(),       # .minicode/state/model.json
        )
        self.llm = LLMClient(model=resolved)
        self.history: list = []
        self._cancelled = False
        self._main_loop = None
        self._init_sh_thread: threading.Thread | None = None

        # Wire up harness.toml config (mirrors run_repl L193)
        from loom.agent.config import load_config
        from loom.agent.loop import apply_config

        apply_config(load_config(WORKDIR))

        # Register user hook scripts (mirrors run_repl L196-206)
        from loom.agent.loop import hooks
        from loom.agent.user_hooks import discover_user_hooks, make_shell_callback

        _EVENT_MAP = {"session_start": "SessionStart", "session_end": "SessionEnd"}
        for event_name, scripts in discover_user_hooks(WORKDIR).items():
            hook_event = _EVENT_MAP.get(event_name, event_name)
            for script in scripts:
                try:
                    hooks.register_hook(hook_event, make_shell_callback(script))
                except Exception:
                    logger.warning("Failed to register user hook {} for {}", script, hook_event)

        # Inject TUI asker (after apply_config so hooks instance is finalized)
        hooks._asker = self._make_tui_asker()

        # Build initial HeaderState from real backend sources.  MCP servers
        # are read from mcp_manager (real MCP connections — empty list when
        # no servers are configured).  Todo + subagent lists start empty and
        # are populated by agent_loop callbacks during a session.
        self._header_state = self._build_initial_header_state()

    def _build_initial_header_state(self) -> HeaderState:
        """Snapshot of real MCP servers + empty todo/subagent lists.

        MCP server state comes from ``mcp_manager.get_server_snapshot()``:
        each configured server shows as ``connected`` (handshake succeeded)
        or ``error`` (handshake failed, evicted, or still discovering).
        Native loom tools are not included — the MCP section shows only
        user-configured MCP servers.
        """
        mcps: list[MCPServer] = []
        try:
            from loom.agent.mcp_manager import get_server_snapshot

            for s in get_server_snapshot():
                mcps.append(MCPServer(name=s["name"], state=s["state"]))  # type: ignore[arg-type]
        except Exception:
            pass
        return HeaderState(mcps=mcps, todos=[], subagents=[])

    def _convert_agent_todos(self, todos: list) -> list[TodoItem]:
        """Map agent_loop's todo_write format to TUI Header's TodoItem.

        Agent uses Claude Code style ('in_progress' / 'completed');
        Header uses shorter ('active' / 'done') for compact rendering.
        Unknown statuses fall back to 'pending'.
        """
        result: list[TodoItem] = []
        for t in todos:
            content = str(t.get("content", ""))
            agent_state = str(t.get("status", "pending"))
            state = _TODO_STATE_FROM_AGENT.get(agent_state, "pending")
            result.append(TodoItem(text=content, state=state))
        return result

    def on_mount(self) -> None:
        """Capture the main event loop for cross-thread async dispatch."""
        self.register_theme(_LOOM_INK_THEME)
        self.theme = "loom-ink"
        self._main_loop = asyncio.get_running_loop()
        self.query_one("#composer", Composer).focus()
        status_bar = self.query_one(StatusBar)
        status_bar.ctx_window = self.llm.get_context_window()
        self._sync_status_bar()
        self.query_one(Header).update_state(self._header_state)
        # Kick off MCP discovery early so the Header shows real server state
        # (connected / error) before the first agent turn.  The discovery
        # threads run in the background; our 3s _resync_mcp_state timer picks
        # up results as they arrive.  Idempotent: agent_loop's SessionStart
        # hook also calls start_discovery, but the module-level guard skips
        # the second call.
        try:
            from loom.agent.loop import _active_config
            from loom.agent.mcp_manager import start_discovery as _mcp_start_discovery

            _mcp_start_discovery(_active_config)
        except Exception:
            pass
        self.set_interval(1.0, self._tick_shuttle, name="shuttle-tick")
        self.set_interval(3.0, self._resync_mcp_state, name="mcp-sync")
        self._detect_git_branch()
        self._check_credentials_on_startup()
        self._lock_focus_to_composer()
        # Push the full-screen centered welcome page on top of the empty
        # layout.  Dismissed when the user types Enter (or ESC to skip).
        # Skipped when _skip_welcome is set (used by test harness).
        if not self._skip_welcome:
            self.push_screen(
                WelcomeModal(model=self.llm.model),
                self._on_welcome_dismissed,
            )

    def _on_welcome_dismissed(self, text: str | None) -> None:
        """Callback when the WelcomeModal is dismissed.

        If the user typed something and pressed Enter, submit it as the
        first user message.  ESC or empty text just reveals the empty
        layout underneath.
        """
        if text:
            asyncio.create_task(self._submit_welcome_text(text))

    async def _submit_welcome_text(self, text: str) -> None:
        """Submit the welcome page input as the first user message."""
        composer = self.query_one("#composer", Composer)
        composer.text = ""
        # Dispatch a Submitted event so run_agent_turn handles it
        self.post_message(Composer.Submitted(text))

    def on_key(self, event) -> None:
        """Ensure printable keys always reach the Composer (input box)."""
        if event.is_printable:
            composer = self.query_one("#composer", Composer)
            if not composer.has_focus:
                composer.focus()

    def _lock_focus_to_composer(self) -> None:
        """Walk all widgets and disable ``can_focus`` except on the Composer.

        Called once at startup so the input box is the only widget that
        can receive keyboard focus.  Other widgets (ChatLog, Header
        buttons, tool markers, etc.) declare ``can_focus = True`` to
        support click-to-focus, but here we disable that to keep the
        Composer permanently focused.
        """
        composer = self.query_one("#composer", Composer)
        for widget in self.walk_children():
            if widget is not composer and hasattr(widget, 'can_focus'):
                widget.can_focus = False

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if self._forward_scroll_to_chatlog(event, direction=-1):
            event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        if self._forward_scroll_to_chatlog(event, direction=1):
            event.stop()

    async def on_event(self, event) -> None:
        """Intercept mouse wheel events at the App boundary so they always
        reach the ChatLog, regardless of which widget the cursor is over.

        Textual's standard event flow dispatches mouse events to the topmost
        widget at the cursor, then bubbles through that widget's parent
        chain. The App's `on_mouse_scroll_*` only fires when an event is
        posted to the App's message pump — which never happens for real
        driver input. So we have to intercept before the screen forwards
        the event. We allow the event to continue through `super().on_event`
        after handling so the cursor widget still receives it (e.g. to
        update hover states), but the scroll has already been applied.
        """
        if isinstance(event, MouseScrollUp) and not event.is_forwarded:
            if self._forward_scroll_to_chatlog(event, direction=-1):
                event.stop()
                return
        elif isinstance(event, MouseScrollDown) and not event.is_forwarded:
            if self._forward_scroll_to_chatlog(event, direction=1):
                event.stop()
                return
        try:
            await super().on_event(event)
        except ScreenStackError:
            return

    def _forward_scroll_to_chatlog(self, event, direction: int) -> bool:
        """Route any unhandled wheel event to the ChatLog so the user can
        scroll history regardless of where the cursor sits (Composer, gutter,
        status bar, etc.). Returns True if the chat log accepted the scroll.
        """
        try:
            chat_log = self.query_one(ChatLog)
        except Exception:
            return False
        if chat_log.max_scroll_y <= 0:
            return False
        step = max(1, int(self.scroll_sensitivity_y))
        new_y = max(
            0.0,
            min(float(chat_log.max_scroll_y), chat_log.scroll_y + direction * step),
        )
        if new_y == chat_log.scroll_y:
            return False
        chat_log.scroll_to(y=new_y, animate=False, immediate=True)
        # ``scroll_to`` only updates the logical scroll position; the
        # visual repaint is normally scheduled via the compositor's idle
        # process, but in real terminals that repaint can be deferred
        # until something else (focus change, next event) wakes the loop.
        # Post the Update/UpdateScroll messages directly so the screen
        # sees the dirty widget on its next pump cycle and repaints
        # without waiting for the wheel-event focus handoff.
        try:
            chat_log._set_dirty(chat_log.size.region)
            self.screen.post_message(textual_messages.Update(chat_log))
            self.screen.post_message(textual_messages.UpdateScroll())
        except Exception:
            pass
        return True

    def _sync_status_bar(self) -> None:
        try:
            status_bar = self.query_one(StatusBar)
        except Exception:
            return
        status_bar.turns = self.user_turn_count
        status_bar.tools = self.tool_call_count
        status_bar.ctx_tokens = self.ctx_tokens
        status_bar.ctx_window = self.llm.get_context_window()

    def _resync_mcp_state(self) -> None:
        """3Hz poll: refresh MCP server state from mcp_manager into Header.

        MCP discovery is async (background threads start during agent_loop).
        Servers transition ``error → connected`` as discovery completes and
        ``connected → error`` on eviction.  We poll because adding a
        push-based callback hook into the existing mcp_manager event paths
        (start_discovery, eviction handler in _make_mcp_handler) would be
        more invasive than the 3s polling cost — MCP state changes at most
        a few times per session, so the poll is almost always a no-op.
        """
        try:
            from loom.agent.mcp_manager import get_server_snapshot

            snapshot = get_server_snapshot()
        except Exception:
            return
        new_mcps: list[MCPServer] = []
        for s in snapshot:
            state = cast(Literal["connected", "error"], s["state"])
            new_mcps.append(MCPServer(name=s["name"], state=state))
        if new_mcps == self._header_state.mcps:
            return
        self._header_state.mcps = new_mcps
        try:
            self.query_one(Header).update_state(self._header_state)
        except Exception:
            pass

    def _tick_shuttle(self) -> None:
        """§2.2.3 primitive 1: 1Hz gear-rack advance, ONLY when state != idle.

        Idle freezes gear at base frame (no view churn).
        Active: phase cycles 0→1→2→0 each tick → 3-frame gear animation.
        """
        if self.engine_state == "idle":
            return
        try:
            status_bar = self.query_one(StatusBar)
        except Exception:
            return
        status_bar.shuttle_phase = (status_bar.shuttle_phase + 1) % 3

    def _detect_git_branch(self) -> None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2.0,
                cwd=WORKDIR,
            )
            branch = result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            branch = ""
        self._git_branch = branch
        try:
            self.query_one(StatusBar).git_branch = branch
        except Exception:
            pass

    def _check_credentials_on_startup(self) -> None:
        """If no provider credentials are configured, auto-prompt the user.

        Runs once at TUI mount. Reads ``CredentialManager.all()`` — if the
        resulting dict is empty, no provider has an API key configured across
        any of the four priority layers (keyring, ``LOOM_AUTH_CONTENT``, env
        var, ``auth.json``). In that case we push ``ConnectProviderModal`` so
        the user can pick a provider and log in. ESC-cancel returns ``None``
        and the TUI continues normally (the user can re-trigger via
        ``/connect`` later).

        Guard: don't double-push — if a ``ConnectProviderModal`` is already
        on the screen stack (e.g. from a prior ``/connect`` slash command),
        skip. This makes the startup hook idempotent against any future
        re-entry path.
        """
        from loom.agent.credential import credentials  # lazy: avoid circular
        from loom.tui.connect_provider import ConnectProviderModal  # lazy

        try:
            configured = credentials.all()
        except Exception:
            # Credential lookup is best-effort — never block startup on a
            # credential backend failure (keyring, malformed file, etc.).
            return

        if configured:
            return

        # Idempotency guard: skip if ConnectProviderModal is already pushed.
        try:
            stack = self.screen_stack
        except Exception:
            stack = []
        for screen in stack:
            if isinstance(screen, ConnectProviderModal):
                return

        self.push_screen(ConnectProviderModal(), self._on_connect_done)

    def watch_user_turn_count(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

    def watch_tool_call_count(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

    def watch_ctx_tokens(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

    def watch_engine_state(self, _old: EngineState, new: EngineState) -> None:
        if new == "idle":
            try:
                self.query_one(StatusBar).shuttle_phase = 0
            except Exception:
                pass
        try:
            self.query_one(StatusBar).engine_state = new
        except Exception:
            pass

    def _set_engine_state(self, new: EngineState) -> bool:
        """§2.2.1 transitions are instant — no fade, no tween. Returns True if state changed."""
        if self.engine_state != new:
            self.engine_state = new
            return True
        return False

    def _sync_chat_engine_state(self, state: EngineState) -> bool:
        """§2.2.3 primitive 2: propagate engine_state to ChatLog (and hence to live tool markers)."""
        try:
            self.query_one(ChatLog).engine_state = state
            return True
        except Exception:
            return False

    def _make_tui_asker(self):
        """Build an asker that pushes PermissionScreen onto the app via the main loop.

        Called from worker thread (agent_loop running via asyncio.to_thread).
        Uses asyncio.run_coroutine_threadsafe to schedule the async push on the main
        event loop, then blocks the worker thread on the Future result.
        """
        def asker(tool_name: str, args: dict, reason: str) -> str:
            from loom.tui.screens import PermissionScreen

            if self._main_loop is None:
                logger.warning("asker called before on_mount; defaulting to deny")
                return "deny"
            # Schedule push_screen_wait on the main loop
            future = asyncio.run_coroutine_threadsafe(
                self.push_screen_wait(PermissionScreen(tool_name, args, reason)),
                self._main_loop,
            )
            # Block the worker thread until user responds
            return future.result()
        return asker

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        yield ChatLog(id="chat-log")
        with Vertical(id="chrome"):
            yield StatusBar(id="status-bar")
            yield CommandCompleter(id="cmd-completer")
            yield Composer(id="composer")

    def on_header_section_toggle(self, message: Header.SectionToggle) -> None:
        """Per-section toggle: expand / switch / collapse the overlay.

        Cases:
          * No overlay open → mount overlay for the clicked section.
          * Overlay open for SAME section → remove overlay (collapse).
          * Overlay open for DIFFERENT section → remove old, mount new
            (switch). Only one overlay is ever visible.
        """
        new_section = message.section
        try:
            existing = self.query_one(HeaderOverlay)
            current_section = existing.section
        except Exception:
            existing = None
            current_section = None

        header = self.query_one(Header)

        if existing is not None and current_section == new_section:
            existing.remove()
            header.active_section = None
            message.stop()
            return

        if existing is not None:
            existing.remove()

        # Per-section ID avoids DuplicateIds when switching (old overlay may
        # still be in DOM pending async removal).
        overlay = HeaderOverlay(
            section=new_section,
            state=header._state,
            id=f"header-overlay-{new_section}",
        )
        header.active_section = new_section
        self.screen.mount(overlay, before=self.query_one(ChatLog))
        message.stop()

    def action_collapse_header(self) -> None:
        """ESC binding — collapse the Header overlay if one is open.

        Spec §4.3.2: "ESC collapses". No-op when no overlay is open.
        """
        try:
            self.query_one(HeaderOverlay).remove()
            self.query_one(Header).active_section = None
        except Exception:
            pass

    def on_click(self, event: Click) -> None:
        """Click outside the Header line collapses the overlay.

        HeaderSectionButton.on_click calls event.stop() (so section
        clicks don't reach this handler), and Header.on_click + the
        HeaderOverlay itself consume their own clicks. So any click
        that reaches this App-level handler is on something else
        (chat log, status bar, composer, etc.) — the user's intent
        is "move focus away from the overlay", so collapse it.
        """
        try:
            self.query_one(HeaderOverlay).remove()
            self.query_one(Header).active_section = None
        except Exception:
            pass

    def on_assistant_turn_start(self, _: AssistantTurnStart) -> None:
        # Per opencode's pattern: don't pre-emptively show a thinking
        # spinner.  Thinking content only appears when thinking events
        # actually arrive from the provider (on_thinking_delta).  If the
        # model doesn't emit thinking, nothing shows — no visual noise.
        pass

    def on_text_delta(self, message: TextDelta) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.append_streaming_text(message.text)

    def on_thinking_delta(self, message: ThinkingDelta) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.append_thinking_text(message.text)

    def on_tool_use_started(self, message: ToolUseStarted) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.add_tool_call_inline(
            message.tool_name, message.tool_input, message.tool_use_id
        )

    def on_tool_use_completed(self, message: ToolUseCompleted) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.complete_tool_call_inline(
            message.tool_use_id, message.output, message.is_error
        )

    def on_compact_occurred(self, message: CompactOccurred) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.append_system_note(
            f"[Compacted: {message.msgs_before} → {message.msgs_after} messages]"
        )
        self._refresh_ctx_tokens()

    def on_assistant_turn_end(self, message: AssistantTurnEnd) -> None:
        self.tool_call_count = self.tool_call_count + message.tool_calls
        chat_log = self.query_one(ChatLog)
        chat_log._finalize_streaming()
        chat_log.append_assistant_summary(self.llm.model, message.duration)
        self._refresh_ctx_tokens()

    def _refresh_ctx_tokens(self) -> None:
        from loom.agent.loop import context as global_context

        try:
            self.ctx_tokens = global_context.current_tokens(self.history)
        except Exception:
            pass

    def on_todo_update(self, message: TodoUpdate) -> None:
        """f-tui-header-backend-wiring: agent's todo list changed (todo_write ran)."""
        self._header_state.todos = self._convert_agent_todos(message.todos)
        self.query_one(Header).update_state(self._header_state)
        done = sum(1 for t in message.todos if t.get("status") == "completed")
        active = sum(1 for t in message.todos if t.get("status") == "in_progress")
        pending = sum(1 for t in message.todos if t.get("status") == "pending")
        summary = f"{done} done, {active} active, {pending} pending"
        chat_log = self.query_one(ChatLog)
        chat_log.emit_todo_note(summary)

    def on_subagent_start(self, message: SubagentStart) -> None:
        """f-tui-header-backend-wiring: a subagent was spawned (task tool called)."""
        self._header_state.subagents.append(
            Subagent(id=message.subagent_id, state="running", elapsed="0s")
        )
        self.query_one(Header).update_state(self._header_state)
        chat_log = self.query_one(ChatLog)
        chat_log.add_subagent_marker(message.subagent_id, message.description)

    def on_subagent_end(self, message: SubagentEnd) -> None:
        """f-tui-header-backend-wiring: subagent finished (done or error)."""
        for sub in self._header_state.subagents:
            if sub.id == message.subagent_id:
                sub.state = message.state
                sub.elapsed = f"{int(message.elapsed)}s"
                break
        self.query_one(Header).update_state(self._header_state)
        chat_log = self.query_one(ChatLog)
        chat_log.complete_subagent_marker(
            message.subagent_id, message.elapsed, message.state
        )

    def on_subagent_row_clicked(self, message: Header.SubagentRowClicked) -> None:
        """Spec §4.3.2: dismiss overlay + scroll ChatLog to subagent marker."""
        try:
            self.query_one(HeaderOverlay).remove()
        except Exception:
            pass
        try:
            chat_log = self.query_one(ChatLog)
            marker = chat_log._tool_markers.get(message.tool_use_id)
            if marker is None:
                return
            marker.scroll_visible(top=True, animate=False, immediate=True)
            chat_log._set_dirty(chat_log.size.region)
            self.screen.post_message(textual_messages.Update(chat_log))
            self.screen.post_message(textual_messages.UpdateScroll())
        except Exception:
            logger.warning("Failed to scroll to subagent marker {}", message.tool_use_id)

    def on_composer_completion_query(self, event: Composer.CompletionQuery) -> None:
        self.query_one(CommandCompleter).show_for(event.text)

    def on_composer_completion_move(self, event: Composer.CompletionMove) -> None:
        self.query_one(CommandCompleter).move(event.direction)

    def on_composer_completion_hide(self, event: Composer.CompletionHide) -> None:
        self.query_one(CommandCompleter).hide()

    def on_composer_completion_tab(self, event: Composer.CompletionTab) -> None:
        completer = self.query_one(CommandCompleter)
        cmd = completer.current()
        if cmd:
            composer = self.query_one(Composer)
            composer.text = f"/{cmd.name} "
            completer.hide()
            composer.focus()
            composer.cursor_location = (0, len(composer.text))

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        user_msg = event.value.strip()
        if not user_msg:
            return
        # Enter 提交时隐藏 completer（_on_key 的 enter 分支早返回，不会触发 CompletionHide）
        try:
            self.query_one(CommandCompleter).hide()
        except Exception:
            pass
        if user_msg.startswith("/"):
            await self.run_slash_command(user_msg[1:])
            return
        await self.run_agent_turn(user_msg)

    async def run_slash_command(self, cmd_line: str) -> None:
        parts = cmd_line.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        chat_log = self.query_one(ChatLog)

        from loom.tui.slash_commands import find_command

        entry = find_command(cmd)
        if entry is None:
            chat_log.append_system_note(f"Unknown command: **/{cmd}**. Try /help.")
            return
        await entry.handler(self, args)

    async def run_agent_turn(self, user_msg: str) -> None:
        self.history.append({"role": "user", "content": user_msg})
        self.user_turn_count = self.user_turn_count + 1
        chat_log = self.query_one(ChatLog)
        await chat_log.append_user_message(user_msg)

        callbacks = {
            "on_message_start": lambda: (
                self.post_message(AssistantTurnStart()),
                self._set_engine_state("thinking"),
                setattr(self, "_turn_start", time.monotonic()),  # type: ignore[func-returns-value]
            ),
            "on_assistant_message_start": lambda: (
                self.post_message(AssistantTurnStart()),
                self._set_engine_state("thinking"),
            ),
            "on_text_delta": lambda chunk: (
                self.post_message(TextDelta(chunk)),
                self._set_engine_state("streaming"),
            ),
            "on_thinking_delta": lambda chunk: (
                self.post_message(ThinkingDelta(chunk)),
                self._set_engine_state("thinking"),
            ),
            "on_tool_use": lambda name, inp, uid: (
                self.post_message(ToolUseStarted(name, inp, uid)),
                self._set_engine_state("executing"),
                self._sync_chat_engine_state("executing"),
            ),
            "on_tool_result": lambda uid, out, err: (
                self.post_message(ToolUseCompleted(uid, out, err)),
                self._set_engine_state("error" if err else "executing"),
                self._sync_chat_engine_state("error" if err else "executing"),
            ),
            "on_compact": lambda before, after: (
                self.post_message(CompactOccurred(before, after)),
                self._set_engine_state("compacting"),
                self._sync_chat_engine_state("compacting"),
            ),
            "on_message_end": lambda calls, turns: (
                self.post_message(AssistantTurnEnd(
                    calls, turns,
                    time.monotonic() - getattr(self, "_turn_start", time.monotonic()),
                )),
                self._set_engine_state("idle"),
                self._sync_chat_engine_state("idle"),
            ),
            # f-tui-header-backend-wiring: cross-thread bridge for todo /
            # subagent state. Callbacks fire from the worker thread inside
            # agent_loop → fire_callback (loop.py) → these lambdas →
            # post_message (thread-safe) → main thread handlers below.
            "on_todo_update": lambda todos: self.post_message(TodoUpdate(list(todos))),
            "on_subagent_start": lambda sid, desc: self.post_message(
                SubagentStart(sid, desc)
            ),
            "on_subagent_end": lambda sid, elapsed, state: self.post_message(
                SubagentEnd(sid, elapsed, state)
            ),
        }

        self._run_turn(callbacks)

    @work(exclusive=True, group="agent-turn")
    async def _run_turn(self, callbacks: dict) -> None:
        await asyncio.to_thread(
            agent_loop,
            self.history,
            self.llm,
            callbacks,
            self.llm.stream_iter,
        )

    def action_cancel_stream(self) -> None:
        self._cancelled = True
        self.llm.cancel()
        try:
            for worker in self.workers:
                if getattr(worker, "group", "") == "agent-turn":
                    worker.cancel()
        except Exception:
            pass

    def action_clear_screen(self) -> None:
        chat_log = self.query_one(ChatLog)
        asyncio.create_task(chat_log.clear_content())

    async def action_quit(self) -> None:
        """Override default quit: fire-and-forget init.sh, exit immediately."""
        from loom.agent.loop import _active_config, hooks, schedule_init_sh_on_session_end

        hooks.trigger_hooks("SessionEnd", self.history, self.tool_call_count)

        if _active_config.run_init_sh_on_session_end:
            def on_done(result, err):
                self.call_from_thread(self._on_init_sh_complete, result, err)
            self._init_sh_thread = schedule_init_sh_on_session_end(
                WORKDIR, _active_config,
                on_complete=on_done,
                timeout=120.0,
            )

        hooks._asker = hooks._default_asker
        self.exit()

    def _on_init_sh_complete(
        self,
        result: subprocess.CompletedProcess | None,
        err: str,
    ) -> None:
        """Banner displayed when the fire-and-forget init.sh thread finishes.

        Called via call_from_thread from the helper's worker thread so it's
        safe to touch the ChatLog here (we're on the main loop).
        """
        if err == "file not found" or err == "stopped":
            return  # silent
        try:
            chat_log = self.query_one(ChatLog)
        except Exception:
            return
        if err == "timed out":
            chat_log.append_system_note("[init.sh: TIMEOUT > 120s]")
        elif result is not None and result.returncode == 0:
            chat_log.append_system_note("[init.sh: pass (exit 0)]")
        else:
            rc = getattr(result, "returncode", -1) if result else -1
            tail = ""
            if result is not None:
                stderr_tail = (result.stderr or "")[-200:]
                if stderr_tail:
                    tail = f" {stderr_tail}"
                else:
                    stdout_tail = (result.stdout or "")[-200:]
                    if stdout_tail:
                        tail = f" {stdout_tail}"
            chat_log.append_system_note(f"[init.sh: FAIL exit={rc}]{tail}")

    def _on_model_picked(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        provider_id, model_id = result
        self.llm.change_model(f"{provider_id}/{model_id}")
        self._sync_status_bar()
        chat_log = self.query_one(ChatLog)
        chat_log.append_system_note(f"Model changed to **{self.llm.model}**")
        ms = ModelState(WORKDIR)
        ms.add_recent(provider_id, model_id)
        ms.set_default(provider_id, model_id)

    def _on_connect_done(self, result: tuple[str, str | None] | None) -> None:
        if result is None:
            return  # cancelled
        provider_id, model_id_info = result
        if model_id_info == "":
            # Connected provider → push ModelPicker
            from loom.tui.model_picker import ModelPicker
            self.push_screen(ModelPicker(), self._on_model_picked)
        elif model_id_info is None:
            # Unconnected provider → push AuthInputModal
            from loom.tui.auth_input import AuthInputModal
            self.push_screen(AuthInputModal(provider_id), self._on_connect_auth_done)

    def _on_connect_auth_done(self, result: str | None) -> None:
        if result is None:
            return  # cancelled
        provider_id = result
        # After successful auth, log the provider name and push ModelPicker.
        # Do NOT auto-select a model — let the user pick from the dialog.
        from loom.agent.providers.registry import PROVIDERS
        try:
            inst = PROVIDERS[provider_id](api_key="", base_url=None)
            display = inst.display_name or provider_id
            chat_log = self.query_one(ChatLog)
            chat_log.append_system_note(f"Logged in to **{display}**")
        except Exception:
            pass
        from loom.tui.model_picker import ModelPicker
        self.push_screen(ModelPicker(), self._on_model_picked)

    def action_show_command_palette(self) -> None:
        """Open the Ctrl+P command palette."""
        from loom.tui.command_palette import CommandPaletteModal  # lazy: avoid circular
        self.push_screen(CommandPaletteModal(), self._on_palette_selected)

    def _on_palette_selected(self, cmd: SlashCommand | None) -> None:  # noqa: F821
        """Handle command palette selection — immediately submit the command."""
        if cmd is None:
            return
        from loom.tui.slash_commands import SlashCommand  # noqa: F401 — runtime type check

        composer = self.query_one(Composer)
        composer.text = f"/{cmd.name} "
        self.post_message(Composer.Submitted(f"/{cmd.name} "))
