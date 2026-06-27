"""stdio JSON-RPC server that bridges the TUI frontend to loom's agent_loop.

Lifecycle:
1. TUI spawns ``python -m loom.cli serve`` as a child process.
2. Server emits ``event/session_started`` immediately.
3. TUI sends ``request/send_message``; server replies ``Response.ok`` then
   streams events until ``event/assistant_turn_end``.
4. For permission-gated tools, server sends ``request/permission`` and
   blocks until TUI replies with ``request/permission_response``.
5. On ``request/shutdown`` or stdin EOF, server exits cleanly.

The server runs the agent_loop on a worker thread (so the main thread can
read requests from stdin concurrently). Events from the worker thread are
written to stdout via the thread-safe :class:`LineCodec`.

Test mode: setting ``LOOM_RPC_TEST_STUB=1`` substitutes a stub LLM client
that yields a fixed text response without calling any real API. This lets
the integration tests run end-to-end without credentials.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from loom.rpc.codec import LineCodec
from loom.rpc.protocol import Event, Request, Response, _Message


def _configure_server_logging(workdir: Path) -> None:
    """Configure loguru to write to **stderr** (not stdout).

    The JSON-RPC protocol reserves stdout for Event/Response lines; any
    loguru sink on stdout would corrupt the wire format. This replaces
    the default ``configure_logging()`` from ``loom.agent.loop`` which
    writes to stdout.
    """
    from loguru import logger
    logger.remove()
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    def _stderr_sink(msg) -> None:
        sys.stderr.write(msg)

    logger.add(
        sink=_stderr_sink,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:7}</level> | <level>{message}</level>",
        level=LOG_LEVEL,
        colorize=True,
    )
    logger.add(
        sink=workdir / "loom.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:7} | {message}",
        level="DEBUG",
        rotation="10 MB",
        retention=3,
    )


def run_server(workdir: Path | None = None) -> int:
    """Main entry point for the ``loom cli serve`` subcommand.

    Args:
        workdir: the working directory (defaults to cwd).
    """
    if workdir is None:
        workdir = Path.cwd()

    codec = LineCodec(reader=sys.stdin, writer=sys.stdout)
    server = _Server(workdir, codec)
    return server.run()


class _StubLLMClient:
    """Test stub — yields a fixed text response, no API calls.

    Used when ``LOOM_RPC_TEST_STUB=1`` is set so integration tests can run
    end-to-end without LLM credentials. The stub yields one text event and
    one usage event with ``stop_reason="end_turn"`` so agent_loop completes
    a normal turn.
    """

    model = "stub/test-model"
    api_key = ""
    base_url = ""

    def __init__(self, response_text: str = "hello from stub"):
        self._response_text = response_text

    def get_context_window(self) -> int:
        """Match LLMClient.get_context_window — agent_loop calls this."""
        return 128_000

    def invoke(self, system, messages, tools, max_tokens: int | None = None):
        """Non-streaming fallback — not used in stub mode but provided
        for parity with the LLMClient interface."""
        from loom.agent.providers.types import (
            ProviderResponse,
            StopReason,
            TextBlock,
            Usage,
        )
        return ProviderResponse(
            model=self.model,
            content=[TextBlock(text=self._response_text)],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=10, output_tokens=len(self._response_text.split())),
        )

    def stream_iter(self, system, messages, tools, max_tokens: int | None = None):
        # Late import — providers.types is only needed when actually streaming.
        from loom.agent.providers.types import StreamEvent
        yield StreamEvent(kind="text", text=self._response_text)
        yield StreamEvent(
            kind="usage",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=len(self._response_text.split()),
        )


class _Server:
    """Manages the agent_loop lifecycle + request/event translation."""

    def __init__(self, workdir: Path, codec: LineCodec, llm_client: Any = None):
        self.workdir = workdir
        self.codec = codec
        self._shutdown = threading.Event()
        self._agent_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        # Permission prompt state: perm_id -> Event; perm_id -> decision
        self._pending_permissions: dict[str, threading.Event] = {}
        self._permission_responses: dict[str, str] = {}
        self._perm_lock = threading.Lock()
        self._history: list = []
        self._llm_client: Any = llm_client
        self._session_id: str | None = None
        self._turn_start_ts: float = 0.0

    def _make_llm_client(self) -> Any:
        """Build the LLM client. Honors ``LOOM_RPC_TEST_STUB`` for tests."""
        if os.getenv("LOOM_RPC_TEST_STUB") == "1":
            return _StubLLMClient(response_text=os.getenv("LOOM_RPC_STUB_TEXT", "hello from stub"))
        from loom.agent.llm import LLMClient
        model = os.getenv("MODEL") or "deepseek/deepseek-v4-flash"
        return LLMClient(model=model)

    def run(self) -> int:
        """Main loop: read requests, dispatch, emit events."""
        # Late imports to avoid circular deps + respect config loading order
        from loom.agent.config import load_config
        from loom.agent.loop import apply_config

        _configure_server_logging(self.workdir)
        config = load_config(self.workdir)
        apply_config(config)

        if self._llm_client is None:
            self._llm_client = self._make_llm_client()
        model = getattr(self._llm_client, "model", "unknown")
        self._session_id = uuid.uuid4().hex[:12]

        # Emit session_started
        self.codec.write_event(Event.session_started(
            session_id=self._session_id, model=model
        ))

        # Main request loop (runs on main thread)
        while not self._shutdown.is_set():
            try:
                msg = self.codec.read_message()
            except ValueError as exc:
                logger.warning("malformed message from TUI: {}", exc)
                self.codec.write_event(Event.error(
                    message=f"malformed message: {exc}"
                ))
                continue
            if msg is None:
                # EOF — TUI closed stdin
                break
            self._dispatch(msg)

        # Wait for agent thread to finish
        if self._agent_thread and self._agent_thread.is_alive():
            self._cancel_event.set()
            self._agent_thread.join(timeout=5.0)

        self.codec.write_event(Event.session_ended(session_id=self._session_id or ""))
        return 0

    def _dispatch(self, msg: _Message) -> None:
        """Route a message to the appropriate handler."""
        if not isinstance(msg, Request):
            return  # We don't expect events from the TUI

        method = msg.method
        if method == "request/send_message":
            self._handle_send_message(msg)
        elif method == "request/cancel":
            self._handle_cancel(msg)
        elif method == "request/permission_response":
            self._handle_permission_response(msg)
        elif method == "request/pick_model":
            self._handle_pick_model(msg)
        elif method == "request/list_sessions":
            self._handle_list_sessions(msg)
        elif method == "request/load_session":
            self._handle_load_session(msg)
        elif method == "request/new_session":
            self._handle_new_session(msg)
        elif method == "request/shutdown":
            self._handle_shutdown(msg)
        else:
            self.codec.write_event(Response.error(
                id=msg.id, code=-32601,
                message=f"unknown method: {method}"
            ))

    def _build_callbacks(self) -> dict:
        """Build the agent_loop callbacks dict that emits RPC events."""
        codec = self.codec
        import time as _time

        def _emit(event: Event) -> None:
            codec.write_event(event)

        def _on_message_start() -> None:
            self._turn_start_ts = _time.monotonic()
            _emit(Event.assistant_turn_start(agent_name="织轴"))

        def _on_assistant_message_start() -> None:
            # Per-LLM-call reset (mirrors TUI's on_assistant_message_start).
            # We don't emit a new assistant_turn_start here because the
            # first call already did; subsequent LLM calls in the same
            # turn just continue streaming.
            pass

        def _on_message_end(calls: int, total: int) -> None:
            duration = _time.monotonic() - (self._turn_start_ts or _time.monotonic())
            _emit(Event.assistant_turn_end(
                tool_calls=calls, total_messages=total, duration=duration
            ))

        return {
            "on_message_start": _on_message_start,
            "on_assistant_message_start": _on_assistant_message_start,
            "on_text_delta": lambda chunk: _emit(Event.text_delta(text=chunk)),
            "on_thinking_delta": lambda chunk: _emit(Event.thinking_delta(text=chunk)),
            "on_tool_use": lambda name, inp, uid: _emit(
                Event.tool_use_started(tool_name=name, tool_input=inp, tool_use_id=uid)
            ),
            "on_tool_result": lambda uid, out, err: _emit(
                Event.tool_use_completed(tool_use_id=uid, output=out, is_error=err)
            ),
            "on_compact": lambda before, after: _emit(
                Event.compact_occurred(msgs_before=before, msgs_after=after)
            ),
            "on_message_end": _on_message_end,
            "on_todo_update": lambda todos: _emit(Event.todo_update(todos=list(todos))),
            "on_subagent_start": lambda sid, desc, agent_name="织针": _emit(
                Event.subagent_start(subagent_id=sid, description=desc, agent_name=agent_name)
            ),
            "on_subagent_end": lambda sid, elapsed, state: _emit(
                Event.subagent_end(subagent_id=sid, elapsed=elapsed, state=state)
            ),
        }

    def _make_rpc_asker(self) -> Any:
        """Build a permission asker that routes through RPC instead of Textual."""
        def asker(tool_name: str, args: dict, reason: str) -> str:
            perm_id = uuid.uuid4().hex[:8]
            event = Response.permission_request(
                id=perm_id, tool_name=tool_name,
                tool_input=args, reason=reason
            )
            self.codec.write_event(event)
            # Wait for the TUI's permission_response
            done = threading.Event()
            with self._perm_lock:
                self._pending_permissions[perm_id] = done
            done.wait(timeout=300.0)  # 5 min timeout
            with self._perm_lock:
                self._pending_permissions.pop(perm_id, None)
                decision = self._permission_responses.pop(perm_id, "deny")
            return decision
        return asker

    def _handle_send_message(self, req: Request) -> None:
        """Reply immediately, then run agent_loop on a worker thread."""
        # Ack the request
        self.codec.write_event(Response.ok(id=req.id, result={"ack": True}))

        text = req.params.get("text", "")
        self._history.append({"role": "user", "content": text})
        self._cancel_event.clear()

        # Wire the RPC asker into hooks (best-effort — hooks module may
        # not expose _asker in future refactors; if not, permission
        # prompts just default to deny).
        try:
            from loom.agent.loop import hooks as loop_hooks
            loop_hooks._asker = self._make_rpc_asker()
        except Exception:
            logger.debug("could not wire RPC asker into hooks; permission prompts will default")

        callbacks = self._build_callbacks()

        def _run_agent():
            from loom.agent.loop import agent_loop
            try:
                agent_loop(
                    self._history,
                    self._llm_client,
                    callbacks,
                    self._llm_client.stream_iter,
                    self._session_id,
                )
            except Exception as exc:
                self.codec.write_event(Event.error(
                    message=f"agent crashed: {type(exc).__name__}: {exc}"
                ))

        self._agent_thread = threading.Thread(
            target=_run_agent, name="agent-loop", daemon=True
        )
        self._agent_thread.start()

    def _handle_cancel(self, req: Request) -> None:
        self._cancel_event.set()
        # Best-effort: tell the LLM client to cancel its in-flight stream.
        try:
            cancel = getattr(self._llm_client, "cancel", None)
            if cancel is not None:
                cancel()
        except Exception:
            logger.debug("LLM cancel raised; ignoring")
        self.codec.write_event(Response.ok(id=req.id, result={"cancelled": True}))

    def _handle_permission_response(self, req: Request) -> None:
        request_id = req.params.get("request_id", "")
        decision = req.params.get("decision", "deny")
        with self._perm_lock:
            done = self._pending_permissions.get(request_id)
            self._permission_responses[request_id] = decision
        if done is not None:
            done.set()
        # Ack the response so the TUI can match it.
        self.codec.write_event(Response.ok(id=req.id, result={"ack": True}))

    def _handle_pick_model(self, req: Request) -> None:
        model = req.params.get("model", "")
        if model and self._llm_client is not None:
            try:
                self._llm_client.model = model
            except Exception:
                logger.debug("could not set model on LLM client")
        self.codec.write_event(Response.ok(id=req.id, result={"model": model}))

    def _handle_list_sessions(self, req: Request) -> None:
        try:
            from loom.agent.session_store import SessionStore
            store = SessionStore(self.workdir)
            sessions = store.list_sessions()
        except Exception as exc:
            self.codec.write_event(Response.error(
                id=req.id, code=-32603,
                message=f"could not list sessions: {exc}"
            ))
            return
        self.codec.write_event(Response.ok(id=req.id, result={"sessions": sessions}))

    def _handle_load_session(self, req: Request) -> None:
        try:
            from loom.agent.session_store import SessionStore
            session_id = req.params.get("session_id", "")
            store = SessionStore(self.workdir)
            loaded = store.load_session(session_id)
        except Exception as exc:
            self.codec.write_event(Response.error(
                id=req.id, code=-32603,
                message=f"could not load session: {exc}"
            ))
            return
        if loaded is not None:
            # SessionStore.load_session returns an object with .messages
            self._history = list(
                getattr(loaded, "messages", loaded)
                if not isinstance(loaded, list)
                else loaded
            )
            self._session_id = session_id
            self.codec.write_event(Response.ok(id=req.id, result={"loaded": True}))
        else:
            self.codec.write_event(Response.error(
                id=req.id, code=-32602, message=f"session not found: {session_id}"
            ))

    def _handle_new_session(self, req: Request) -> None:
        self._history = []
        self._session_id = uuid.uuid4().hex[:12]
        self.codec.write_event(Response.ok(id=req.id, result={
            "session_id": self._session_id
        }))

    def _handle_shutdown(self, req: Request) -> None:
        self.codec.write_event(Response.ok(id=req.id, result={"shutting_down": True}))
        self._shutdown.set()
