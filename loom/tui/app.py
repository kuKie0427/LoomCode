"""Textual TUI for the loom coding agent.

Wraps agent_loop with streaming, tool cards, and lifecycle hooks.
Callbacks use post_message to safely cross from the worker thread
into the main Textual event loop.
"""

import asyncio
import os
import subprocess

from loguru import logger
from textual import messages as textual_messages
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.events import MouseScrollDown, MouseScrollUp
from textual.reactive import reactive

from loom.agent.llm import LLMClient
from loom.agent.loop import WORKDIR, agent_loop
from loom.tui import kitty_patch  # noqa: F401  # side-effect: patches XTermParser
from loom.tui.chat_log import ChatLog
from loom.tui.composer import Composer
from loom.tui.messages import (
    AssistantTurnEnd,
    AssistantTurnStart,
    CompactOccurred,
    TextDelta,
    ThinkingDelta,
    ToolUseCompleted,
    ToolUseStarted,
)
from loom.tui.status_bar import StatusBar


class AgentTUIApp(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
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

    BINDINGS = [
        ("ctrl+c", "cancel_stream", "Cancel"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+l", "clear_screen", "Clear"),
    ]

    def __init__(self, resume: bool = False, model: str | None = None):
        super().__init__()
        self.resume = resume
        self.llm = LLMClient(model=model or os.getenv("MODEL") or "deepseek-v4-flash")
        self.history: list = []
        self._cancelled = False
        self._main_loop = None

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

    def on_mount(self) -> None:
        """Capture the main event loop for cross-thread async dispatch."""
        self._main_loop = asyncio.get_running_loop()
        self.query_one("#composer", Composer).focus()
        status_bar = self.query_one(StatusBar)
        status_bar.ctx_window = self.llm.get_context_window()
        self._sync_status_bar()

    def on_key(self, event) -> None:
        pass

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
        await super().on_event(event)

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

    def watch_user_turn_count(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

    def watch_tool_call_count(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

    def watch_ctx_tokens(self, _old: int, _new: int) -> None:
        self._sync_status_bar()

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
        yield ChatLog(id="chat-log")
        with Vertical(id="chrome"):
            yield StatusBar(id="status-bar")
            yield Composer(id="composer")

    def on_assistant_turn_start(self, _: AssistantTurnStart) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.show_thinking_spinner()

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
        self._refresh_ctx_tokens()

    def _refresh_ctx_tokens(self) -> None:
        from loom.agent.loop import context as global_context

        try:
            self.ctx_tokens = global_context.current_tokens(self.history)
        except Exception:
            pass

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        user_msg = event.value.strip()
        if not user_msg:
            return
        if user_msg.startswith("/"):
            await self.run_slash_command(user_msg[1:])
            return
        await self.run_agent_turn(user_msg)

    async def run_slash_command(self, cmd_line: str) -> None:
        parts = cmd_line.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        chat_log = self.query_one(ChatLog)

        if cmd in ("q", "quit", "exit"):
            await self.action_quit()
        elif cmd == "clear":
            self.history.clear()
            self.user_turn_count = 0
            self.tool_call_count = 0
            self.ctx_tokens = 0
            await chat_log.clear_content()
        elif cmd == "help":
            chat_log.append_system_note(
                "**Commands:** /help, /clear, /model <name>, /quit, /resume, /status"
            )
        elif cmd == "model":
            if args.strip():
                self.llm.change_model(args.strip())
                self._sync_status_bar()
                chat_log.append_system_note(f"Model changed to **{self.llm.model}**")
            else:
                chat_log.append_system_note(f"Current model: **{self.llm.model}**")
        elif cmd == "resume":
            import loom.agent.checkpoint as checkpoint

            if checkpoint.exists(WORKDIR):
                ckpt = checkpoint.load(WORKDIR)
                if ckpt is not None:
                    self.history = ckpt.get("messages", [])
                    self.user_turn_count = sum(
                        1 for m in self.history if m.get("role") == "user"
                    )
                    self.tool_call_count = ckpt.get("tool_call_count", 0)
                    self._refresh_ctx_tokens()
                    chat_log.append_system_note(
                        f"Resumed from checkpoint ({ckpt.get('saved_at', '?')}, "
                        f"{len(self.history)} messages)"
                    )
                else:
                    chat_log.append_system_note("Checkpoint file corrupted or empty.")
            else:
                chat_log.append_system_note("No checkpoint found.")
        elif cmd == "status":
            status = (
                f"**Session Status**\n"
                f"- Model: `{self.llm.model}`\n"
                f"- Messages: {len(self.history)}\n"
                f"- Tool calls: {self.tool_call_count}\n"
            )
            chat_log.append_system_note(status)
        else:
            chat_log.append_system_note(f"Unknown command: **/{cmd}**. Try /help.")

    async def run_agent_turn(self, user_msg: str) -> None:
        self.history.append({"role": "user", "content": user_msg})
        self.user_turn_count = self.user_turn_count + 1
        chat_log = self.query_one(ChatLog)
        await chat_log.append_user_message(user_msg)

        callbacks = {
            "on_message_start": lambda: self.post_message(AssistantTurnStart()),
            "on_assistant_message_start": lambda: self.post_message(
                AssistantTurnStart()
            ),
            "on_text_delta": lambda chunk: self.post_message(TextDelta(chunk)),
            "on_thinking_delta": lambda chunk: self.post_message(ThinkingDelta(chunk)),
            "on_tool_use": lambda name, inp, uid: self.post_message(ToolUseStarted(name, inp, uid)),
            "on_tool_result": lambda uid, out, err: self.post_message(
                ToolUseCompleted(uid, out, err)
            ),
            "on_compact": lambda before, after: self.post_message(CompactOccurred(before, after)),
            "on_message_end": lambda calls, turns: self.post_message(
                AssistantTurnEnd(calls, turns)
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
        """Override default quit to fire SessionEnd + run init.sh first."""
        from loom.agent.loop import _active_config, hooks

        hooks.trigger_hooks("SessionEnd", self.history, self.tool_call_count)

        if _active_config.run_init_sh_on_session_end:
            init_sh = WORKDIR / "init.sh"
            if init_sh.is_file():
                try:
                    proc = subprocess.run(
                        [str(init_sh)],
                        cwd=WORKDIR,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if proc.returncode != 0:
                        logger.warning(
                            "init.sh exited {} on SessionEnd: {}\n{}",
                            proc.returncode,
                            proc.stdout[:200],
                            proc.stderr[:200],
                        )
                except subprocess.TimeoutExpired:
                    logger.warning("init.sh timed out on SessionEnd")
            else:
                logger.debug("init.sh not found, skip")

        hooks._asker = hooks._default_asker

        self.exit()
