"""Textual TUI for the loop coding agent.

Wraps agent_loop with streaming, tool cards, and lifecycle hooks.
Callbacks use post_message to safely cross from the worker thread
into the main Textual event loop.
"""

import asyncio
import os
import subprocess

from loguru import logger
from textual import work
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Footer, Header

from loop.agent.llm import LLMClient
from loop.agent.loop import WORKDIR, agent_loop
from loop.tui.chat_log import ChatLog
from loop.tui.composer import Composer
from loop.tui.messages import (
    AssistantTurnEnd,
    AssistantTurnStart,
    CompactOccurred,
    TextDelta,
    ToolUseCompleted,
    ToolUseStarted,
)
from loop.tui.status_bar import StatusBar


class AgentTUIApp(App):
    CSS = """
    Screen { layout: vertical; }
    #chat-log { height: 1fr; border: solid green; }
    #composer { height: 3; dock: bottom; }
    #status-bar { height: 1; dock: bottom; background: blue; }
    """

    _main_loop: asyncio.AbstractEventLoop | None

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
        self.streaming_text = reactive("")
        self.tool_call_count = reactive(0)
        self._cancelled = False
        self._main_loop = None

        # Wire up harness.toml config (mirrors run_repl L193)
        from loop.agent.config import load_config
        from loop.agent.loop import apply_config

        apply_config(load_config(WORKDIR))

        # Register user hook scripts (mirrors run_repl L196-206)
        from loop.agent.loop import hooks
        from loop.agent.user_hooks import discover_user_hooks, make_shell_callback

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

    def _make_tui_asker(self):
        """Build an asker that pushes PermissionScreen onto the app via the main loop.

        Called from worker thread (agent_loop running via asyncio.to_thread).
        Uses asyncio.run_coroutine_threadsafe to schedule the async push on the main
        event loop, then blocks the worker thread on the Future result.
        """
        def asker(tool_name: str, args: dict, reason: str) -> str:
            from loop.tui.screens import PermissionScreen

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
        yield Header()
        yield ChatLog(id="chat-log")
        yield Composer(id="composer")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_assistant_turn_start(self, _: AssistantTurnStart) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.show_thinking_spinner()

    def on_text_delta(self, message: TextDelta) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.append_streaming_text(message.text)

    def on_tool_use_started(self, message: ToolUseStarted) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.add_tool_card(message.tool_name, message.tool_input, message.tool_use_id)

    def on_tool_use_completed(self, message: ToolUseCompleted) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.complete_tool_card(message.tool_use_id, message.output, message.is_error)

    def on_compact_occurred(self, message: CompactOccurred) -> None:
        chat_log = self.query_one(ChatLog)
        chat_log.append_system_note(
            f"[Compacted: {message.msgs_before} → {message.msgs_after} messages]"
        )

    def on_assistant_turn_end(self, message: AssistantTurnEnd) -> None:
        self.tool_call_count = self.tool_call_count + message.tool_calls  # type: ignore[assignment]

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
            await chat_log.clear_content()
        elif cmd == "help":
            chat_log.append_system_note(
                "**Commands:** /help, /clear, /model <name>, /quit, /resume, /status"
            )
        elif cmd == "model":
            if args.strip():
                self.llm.change_model(args.strip())
                chat_log.append_system_note(f"Model changed to **{self.llm.model}**")
            else:
                chat_log.append_system_note(f"Current model: **{self.llm.model}**")
        elif cmd == "resume":
            import loop.agent.checkpoint as checkpoint

            if checkpoint.exists(WORKDIR):
                ckpt = checkpoint.load(WORKDIR)
                if ckpt is not None:
                    self.history = ckpt.get("messages", [])
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
        chat_log = self.query_one(ChatLog)
        await chat_log.append_user_message(user_msg)

        callbacks = {
            "on_message_start": lambda: self.post_message(AssistantTurnStart()),
            "on_text_delta": lambda chunk: self.post_message(TextDelta(chunk)),
            "on_tool_use": lambda name, inp, uid: self.post_message(ToolUseStarted(name, inp, uid)),
            "on_tool_result": lambda uid, out, err: self.post_message(
                ToolUseCompleted(uid, out, err)
            ),
            "on_compact": lambda before, after: self.post_message(CompactOccurred(before, after)),
            "on_message_end": lambda calls, turns: self.post_message(
                AssistantTurnEnd(calls, turns)
            ),
        }

        @work(exclusive=True, group="agent-turn")
        async def _turn() -> None:
            await asyncio.to_thread(
                agent_loop,
                self.history,
                self.llm,
                callbacks,
                self.llm.stream_iter,
            )

        _turn()

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
        from loop.agent.loop import _active_config, hooks

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
