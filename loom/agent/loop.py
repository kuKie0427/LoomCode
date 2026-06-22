import concurrent.futures
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import dotenv
from loguru import logger

import loom.agent.checkpoint as checkpoint
import loom.agent.trace as trace_mod
from loom.agent.config import LLM_CONFIG, HarnessConfig, load_config
from loom.agent.context import Context
from loom.agent.hooks import Hooks
from loom.agent.llm import LLMClient, StreamEvent
from loom.agent.prompt import AGENTS_MD_STATIC_LIMIT, SystemPrompt
from loom.agent.system_prompt import get_system_prompt, invalidate_system_prompt
from loom.agent.tools import (
    TOOL_HANDLERS,
    TOOLS,
)
from loom.agent.user_hooks import discover_user_hooks, make_shell_callback
from loom.memory import load_session_continuity, load_tier1, load_tier2
from loom.skills import build_skill_index

dotenv.load_dotenv()
WORKDIR = Path.cwd()

AgentCallback = Callable[..., None]

DEFAULT_CALLBACKS: dict[str, AgentCallback | None] = {
    "on_message_start": None,
    "on_assistant_message_start": None,
    "on_text_delta": None,
    "on_thinking_delta": None,
    "on_tool_use": None,
    "on_tool_result": None,
    "on_compact": None,
    "on_message_end": None,
    # Backend wiring for f-tui-header-backend-wiring — fired by tools.py
    # to expose MCP/todo/subagent state to the TUI Header. Module-level
    # dispatcher below lets tools.py fire without circular imports.
    "on_todo_update": None,
    "on_subagent_start": None,
    "on_subagent_end": None,
}


# Module-level callback dispatcher — lets loom/agent/tools.py fire
# callbacks (on_todo_update / on_subagent_start / on_subagent_end)
# without importing agent_loop at module load (would be a circular
# import: tools.py is imported by loop.py). set_active_callbacks() is
# called once at agent_loop entry; clear_active_callbacks() in finally.
_active_callbacks: dict | None = None


def set_active_callbacks(cb: dict) -> None:
    """Store the active callbacks dict for in-loop access by tools.

    Called once at the start of ``agent_loop``. ``tools.py`` reads
    this via ``fire_callback(name, *args)``.
    """
    global _active_callbacks
    _active_callbacks = cb


def clear_active_callbacks() -> None:
    """Clear the active callbacks dict. Called in agent_loop's finally."""
    global _active_callbacks
    _active_callbacks = None


def fire_callback(name: str, *args: object) -> None:
    """Fire a named callback if one is active. Silent no-op if not set.

    Errors in user-supplied callbacks are logged but do not propagate —
    a buggy TUI callback must never crash the agent loop.
    """
    if _active_callbacks is None:
        return
    cb = _active_callbacks.get(name)
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:
        logger.warning("Callback {} raised; ignoring", name)


def configure_logging() -> None:
    logger.remove()
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.add(
        sink=lambda msg: __import__("sys").stdout.write(msg),
        format="<green>{time:HH:mm:ss}</green> | <level>{level:7}</level> | <level>{message}</level>",
        level=LOG_LEVEL,
        colorize=True,
    )
    logger.add(
        WORKDIR / "loom.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:7} | {message}",
        level="DEBUG",
        rotation="10 MB",
        retention=3,
    )


def build_system_prompt(workdir: Path = WORKDIR) -> SystemPrompt:
    sp = SystemPrompt()
    sp.add_static("你是MiniCode,一个编程助手,协助用户进行开发任务。")
    sp.add_static("行为准则：小心操作，不破坏系统，不泄露数据。")
    sp.add_static("语言风格：简洁、直接、无废话。")

    # AGENTS.md ≤ AGENTS_MD_STATIC_LIMIT → 进 static 段(可被 prompt cache 利用)
    # > AGENTS_MD_STATIC_LIMIT → 不进 static,留给 load_tier2 处理(避免膨胀 system prompt)
    agents_md_path = workdir / "AGENTS.md"
    if agents_md_path.exists():
        agents_md = agents_md_path.read_text(encoding="utf-8")
        if len(agents_md) <= AGENTS_MD_STATIC_LIMIT:
            sp.add_static("--- Project Working Rules ---\n" + agents_md)

    sp.add_dynamic(f"工作目录: {workdir}")
    sp.add_dynamic(sp.get_git_context(workdir))

    memory_parts: list[str] = []
    skill_index = build_skill_index(workdir).list_for_prompt()
    if skill_index:
        memory_parts.append(skill_index)
    tier1 = load_tier1(workdir)
    if tier1:
        memory_parts.append(tier1)
    continuity = load_session_continuity(workdir)
    if continuity:
        memory_parts.append(continuity)
    tier2 = load_tier2(workdir)
    if tier2:
        memory_parts.append(tier2)
    if memory_parts:
        sp.add_memory("\n\n".join(memory_parts))

    return sp


def _get_system_prompt() -> str:
    return get_system_prompt(WORKDIR)


context = Context()
hooks = Hooks()
hooks.register_hook("PreToolUse", hooks.check_permission_hook)
hooks.register_hook("PreToolUse", hooks.log_hook)
hooks.register_hook("PostToolUse", hooks.log_hook)
hooks.register_hook("AgentStart", hooks.log_hook)
hooks.register_hook("AgentStop", hooks.log_hook)

_active_config: HarnessConfig = HarnessConfig.from_defaults()


def apply_config(config: HarnessConfig) -> None:
    """Apply a loaded HarnessConfig to the module-level hooks + checkpoint thresholds."""
    global _active_config
    _active_config = config
    if config.telemetry.sink_command:
        trace_mod.set_sink(config.telemetry.sink_command)
    hooks.policy = config.policy
    hooks.disabled_tools = config.disabled_tools
hooks.register_hook("AgentStop", context.microcompact)

# PL-2: shut down any LSP servers we started during the session. Done on
# SessionEnd (not AgentStop) so subagents that triggered get_or_start do
# not each kill the parent's server; only the outermost session does.
def _on_session_end(*args):
    try:
        from loom.agent.lsp_manager import shutdown_all as _lsp_shutdown_all
        _lsp_shutdown_all()
    except Exception:
        logger.warning("LSP shutdown_all raised", exc_info=True)
hooks.register_hook("SessionEnd", _on_session_end)

# PL-3: detect orphan LSP rollback journals from a prior crashed run.
# If a previous loom process died mid-rename, files listed in /tmp/
# loom-lsp-rollback-<PID>.json may be inconsistent. We log a warning
# and let the user decide whether to inspect + restore. Failures are
# swallowed so one bad journal can't break SessionStart (and therefore
# the whole session).
def _on_session_start(event, *args):
    try:
        from loom.agent.lsp_apply import recover_stale_journals as _lsp_recover
        _lsp_recover(WORKDIR)
    except Exception:
        logger.warning("LSP journal recovery raised", exc_info=True)
hooks.register_hook("SessionStart", _on_session_start)

llm_client = LLMClient(model=os.getenv("MODEL", "deepseek-v4-flash"))


def _run_tool_block(block, hooks) -> dict:
    """Execute a single tool_use block and return the tool_result dict.

    For ``task`` tools, wraps the handler call with on_subagent_start /
    on_subagent_end (using ``block.id`` as subagent_id) so the TUI can
    map subagent overlay rows directly to ChatLog ToolCallMarkers.
    """
    blocked = hooks.trigger_hooks("PreToolUse", block)
    if blocked is not None:
        tr = trace_mod.current()
        if tr is not None:
            tr.record("tool_denied", tool=block.name, reason=str(blocked)[:200])
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": blocked, "is_error": True}
    handler = TOOL_HANDLERS.get(block.name)
    if block.name == "task":
        cb = _active_callbacks
        raw_desc = str(block.input.get("description", ""))
        description = (raw_desc[:59] + "…") if len(raw_desc) > 60 else raw_desc
        if cb is not None and cb.get("on_subagent_start") is not None:
            cb["on_subagent_start"](block.id, description)
        t0 = time.monotonic()
        state = "done"
        try:
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
        except Exception:
            state = "error"
            raise
        finally:
            elapsed = time.monotonic() - t0
            if cb is not None and cb.get("on_subagent_end") is not None:
                cb["on_subagent_end"](block.id, elapsed, state)
    else:
        output = handler(**block.input) if handler else f"Unknown: {block.name}"
    hooks.trigger_hooks("PostToolUse", block, output)
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": output, "is_error": False}


def _run_tool_turn(tool_uses, hooks):
    """Run a batch of tool_use blocks. 'task' calls run concurrently (Fork mode)."""
    results: list[dict | None] = [None] * len(tool_uses)
    task_idx = [i for i, b in enumerate(tool_uses) if b.name == "task"]
    non_task_idx = [i for i, b in enumerate(tool_uses) if b.name != "task"]

    for i in non_task_idx:
        results[i] = _run_tool_block(tool_uses[i], hooks)

    if len(task_idx) == 1:
        results[task_idx[0]] = _run_tool_block(tool_uses[task_idx[0]], hooks)
    elif len(task_idx) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(task_idx)) as ex:
            futures = {ex.submit(_run_tool_block, tool_uses[i], hooks): i for i in task_idx}
            for fut in concurrent.futures.as_completed(futures):
                results[futures[fut]] = fut.result()

    return results


def agent_loop(messages: list, llm_client=None, callbacks: dict | None = None, stream_text: Callable[[str, list, list, int], Iterator[StreamEvent]] | None = None) -> None:
    configure_logging()
    cb = {**DEFAULT_CALLBACKS, **(callbacks or {})}
    hooks.trigger_hooks("SessionStart")
    if llm_client is None:
        llm_client = globals()["llm_client"]
    hooks.trigger_hooks("AgentStart")
    # Wire module-level dispatcher for tools.py to fire backend callbacks
    # (on_todo_update / on_subagent_start / on_subagent_end). Must be
    # cleared in finally so stale callbacks don't leak between sessions.
    set_active_callbacks(cb)
    try:
            if cb["on_message_start"] is not None:
                cb["on_message_start"]()
            session_id = uuid.uuid4().hex[:12]
            trace_mod.start(WORKDIR, session_id)
            tr = trace_mod.current()
            if tr is not None:
                tr.record("session_start", workdir=str(WORKDIR), initial_messages=len(messages))
            from loom.agent.cost import reset_session_cost
            reset_session_cost()
            tool_call_count = 0
            tokens_at_last_checkpoint = context.current_tokens(messages)
            max_turns = _active_config.max_turns
            for turn in range(max_turns):
                from loom.agent.tool_errors import detect_repeated_failures, build_retry_guidance
                detection = detect_repeated_failures(messages)
                if detection is not None and (not messages or messages[-1].get("role") != "user" or "<system-reminder>" not in str(messages[-1].get("content", ""))[:200]):
                    messages.append({"role": "user", "content": build_retry_guidance(detection)})
                    logger.warning("tool_errors: detected {}-time failure of {}", detection["failure_count"], detection["tool"])
                if context.should_compact(messages, llm_client.get_context_window(), llm_client.model):
                    hooks.trigger_hooks("PreCompact", messages, context.last_input_tokens)
                    msg_count_before = len(messages)
                    context.autocompact(messages, llm_client.client, llm_client.model, llm_client.get_context_window())
                    if cb["on_compact"] is not None:
                        cb["on_compact"](msg_count_before, len(messages))
                    if tr is not None:
                        tr.record("autocompact", tool_calls_so_far=tool_call_count)
                # Fires before EACH LLM call, so the TUI can show the
                # thinking spinner (and reset the thinking display) for every
                # round of reasoning, not just the first. ``on_message_start``
                # is preserved for the once-per-session semantic.
                if cb["on_assistant_message_start"] is not None:
                    cb["on_assistant_message_start"]()
                if stream_text is not None:
                    # ===== STREAMING PATH =====
                    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
                    content_blocks: list = []
                    input_tokens = 0
                    output_tokens = 0
                    stop_reason = "end_turn"
                    for ev in stream_text(_get_system_prompt(), messages, cast(list, TOOLS), LLM_CONFIG.max_output_tokens):
                        if ev.kind == "text":
                            content_blocks.append(TextBlock(type="text", text=ev.text))
                            if cb["on_text_delta"] is not None:
                                cb["on_text_delta"](ev.text)
                        elif ev.kind == "thinking":
                            if cb["on_thinking_delta"] is not None:
                                cb["on_thinking_delta"](ev.text)
                        elif ev.kind == "tool_use":
                            content_blocks.append(ToolUseBlock(
                                type="tool_use",
                                id=ev.tool_id,
                                name=ev.tool_name,
                                input=ev.tool_input or {},
                            ))
                        elif ev.kind == "usage":
                            if ev.input_tokens:
                                input_tokens = ev.input_tokens
                            if ev.output_tokens:
                                output_tokens = ev.output_tokens
                            if ev.stop_reason:
                                stop_reason = ev.stop_reason
                    response = Message(
                        id="stream-" + uuid.uuid4().hex[:8],
                        type="message",
                        role="assistant",
                        content=content_blocks,
                        model=llm_client.model,
                        stop_reason=cast(Any, stop_reason),
                        stop_sequence=None,
                        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
                    )
                else:
                    # ===== SYNC PATH (unchanged) =====
                    from loom.agent.llm import with_cache_control, with_tool_cache_control
                    response = llm_client.client.messages.create(
                        model=llm_client.model,
                        system=with_cache_control(_get_system_prompt()),
                        messages=messages,
                        tools=with_tool_cache_control(cast(list, TOOLS)) if TOOLS else cast(list, TOOLS),
                        max_tokens=LLM_CONFIG.max_output_tokens,
                    )
                context.update(len(messages), response)
                if tr is not None:
                    from loom.agent.cost import usage_from_response, record_turn
                    usage = usage_from_response(response.usage)
                    cost = record_turn(usage, llm_client.model)
                    tr.record("llm_response", stop_reason=response.stop_reason,
                              tokens_in=usage.input_tokens,
                              tokens_out=usage.output_tokens,
                              cache_read=usage.cache_read_tokens,
                              cache_creation=usage.cache_creation_tokens,
                              cost_usd=round(cost.total_usd, 6))
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    hooks.trigger_hooks("AgentStop", messages)
                    checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count)
                    if tr is not None:
                        from loom.agent.cost import get_session_cost
                        sess = get_session_cost()
                        if sess is not None:
                            d = sess.as_dict()
                            d.pop("turns", None)
                            tr.record("session_end", tool_calls=tool_call_count, turns=len(messages), **d)
                        else:
                            tr.record("session_end", tool_calls=tool_call_count, turns=len(messages))
                    if cb["on_message_end"] is not None:
                        cb["on_message_end"](tool_call_count, len(messages))
                    trace_mod.stop()
                    return

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if cb["on_tool_use"] is not None:
                    for block in tool_uses:
                        cb["on_tool_use"](block.name, block.input, block.id)
                if tr is not None and tool_uses:
                    tr.record("tool_batch", tools=[b.name for b in tool_uses], size=len(tool_uses))
                results = _run_tool_turn(tool_uses, hooks)
                if cb["on_tool_result"] is not None:
                    for r in results:
                        if r is not None:
                            cb["on_tool_result"](r.get("tool_use_id", ""), str(r.get("content", "")), r.get("is_error", False))
                tool_call_count += len(tool_uses)
                messages.append({"role": "user", "content": results})

                new_tokens = context.current_tokens(messages) - tokens_at_last_checkpoint
                ckpt_cfg = _active_config.checkpoint
                if checkpoint.is_due(tool_call_count, new_tokens,
                                     ckpt_cfg.every_tool_calls, ckpt_cfg.every_tokens):
                    saved_path = checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count)
                    if tr is not None:
                        tr.record("checkpoint_save", path=str(saved_path),
                                  tool_calls=tool_call_count, new_tokens=new_tokens)
                    tokens_at_last_checkpoint = context.current_tokens(messages)
            else:
                if tr is not None:
                    from loom.agent.cost import get_session_cost
                    sess = get_session_cost()
                    if sess is not None:
                        d = sess.as_dict()
                        d.pop("turns", None)
                        tr.record("loop_limit_reached", turn=turn + 1, tool_calls=tool_call_count,
                                  max_turns=max_turns, **d)
                    else:
                        tr.record("loop_limit_reached", turn=turn + 1, tool_calls=tool_call_count,
                                  max_turns=max_turns)
                messages.append({
                    "role": "user",
                    "content": (
                        f"<system-reminder>\n"
                        f"You have reached the maximum turn limit ({max_turns}). "
                        f"Summarize current progress, list remaining work, and stop. "
                        f"Do not call any more tools.\n"
                        f"</system-reminder>"
                    ),
                })
                hooks.trigger_hooks("AgentStop", messages)
                checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count)
                if cb["on_message_end"] is not None:
                    cb["on_message_end"](tool_call_count, len(messages))
                trace_mod.stop()
                return
    finally:
        clear_active_callbacks()


def schedule_init_sh_on_session_end(
    workdir: Path,
    config: HarnessConfig,
    *,
    on_complete: Callable[[subprocess.CompletedProcess | None, str], None] | None = None,
    on_failure_log: Callable[[int, str, str], None] | None = None,
    stop_event: threading.Event | None = None,
    timeout: float = 120.0,
) -> threading.Thread:
    """Fire-and-forget run of init.sh on session end.

    Schedules a daemon thread that runs ``init.sh`` (if present) with the
    given timeout. Returns the Thread immediately. The thread is daemon,
    so it dies when the Python process exits.

    P2 (TUI) and P4 (REPL) wire-up use this helper directly for non-blocking
    exit. P1 only provides the helper + sync wrapper; behavior change to
    REPL/TUI is deferred.

    stop_event: optional threading.Event. When provided, the runner uses
      subprocess.Popen and polls the event every 0.5s. If the event is set,
      the subprocess is terminated (SIGTERM → timeout 2s → SIGKILL) and
      on_complete fires with error_msg="stopped". Default None preserves
      backward-compatible subprocess.run behavior.

    on_complete(result, error_msg): called when the thread finishes.
      result = CompletedProcess on success, None on file-not-found, timeout,
        or stop.
      error_msg = "" on success, "file not found" / "timed out" /
        "stopped" / "exception: ..." otherwise.

    on_failure_log(returncode, stdout_tail, stderr_tail): called ONLY on
      failure (returncode != 0) with the last 200 chars of each stream.
      None = no logging. Caller can write to progress.md etc.
    """
    def _runner() -> None:
        result: subprocess.CompletedProcess | None = None
        error_msg: str = ""
        try:
            if not config.run_init_sh_on_session_end:
                return  # config says no
            init_sh = workdir / "init.sh"
            if not init_sh.is_file():
                error_msg = "file not found"
                return

            if stop_event is not None:
                proc = subprocess.Popen(
                    [str(init_sh)], cwd=workdir,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True,
                )
                t_start = time.monotonic()
                try:
                    while True:
                        if stop_event.is_set():
                            _terminate_proc(proc)
                            error_msg = "stopped"
                            break

                        if time.monotonic() - t_start >= timeout:
                            _terminate_proc(proc)
                            error_msg = "timed out"
                            break

                        ret = proc.poll()
                        if ret is not None:
                            stdout, stderr = proc.communicate()
                            result = subprocess.CompletedProcess(
                                proc.args, ret, stdout or "", stderr or "",
                            )
                            break

                        stop_event.wait(0.5)
                except Exception as exc:
                    _terminate_proc(proc)
                    error_msg = f"exception: {exc}"

                if result is not None and result.returncode != 0 and on_failure_log is not None:
                    try:
                        on_failure_log(
                            result.returncode,
                            (result.stdout or "")[-200:],
                            (result.stderr or "")[-200:],
                        )
                    except Exception as exc:
                        logger.warning("on_failure_log callback raised; ignoring: {}", exc)
            else:
                try:
                    result = subprocess.run(
                        [str(init_sh)], cwd=workdir, capture_output=True,
                        text=True, timeout=timeout,
                    )
                    if result.returncode != 0 and on_failure_log is not None:
                        try:
                            on_failure_log(
                                result.returncode,
                                (result.stdout or "")[-200:],
                                (result.stderr or "")[-200:],
                            )
                        except Exception as exc:
                            logger.warning("on_failure_log callback raised; ignoring: {}", exc)
                except subprocess.TimeoutExpired:
                    error_msg = "timed out"
        except Exception as exc:
            error_msg = f"exception: {exc}"
        finally:
            if on_complete is not None:
                try:
                    on_complete(result, error_msg)
                except Exception as exc:
                    logger.warning("on_complete callback raised; ignoring: {}", exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return thread


def _log_init_sh_failure_to_progress_md(
    returncode: int, stdout_tail: str, stderr_tail: str
) -> None:
    """Append a SessionEnd failure note to progress.md (best-effort).

    Used by run_repl when schedule_init_sh_on_session_end's daemon thread
    reports init.sh exited non-zero. Best-effort: swallows all exceptions.
    """
    try:
        from datetime import datetime
        progress_path = WORKDIR / "progress.md"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## SessionEnd auto-record ({ts})\n")
            f.write(f"- status: FAILED (exit {returncode})\n")
            f.write(f"- last 200 chars stderr: {stderr_tail[-200:]}\n")
    except Exception as exc:
        logger.warning("Failed to write progress.md: {}", exc)


def _terminate_proc(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        return  # Already exited
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait()


def run_init_sh_on_session_end(
    workdir: Path,
    config: HarnessConfig,
    *,
    timeout: float = 120.0,
    session_tool_calls: int = 0,
) -> None:
    """Synchronous run of init.sh on session end. Blocks until complete.

    Legacy sync API. Internally delegates to ``schedule_init_sh_on_session_end``
    and joins the thread, preserving the original synchronous behavior of
    log-on-failure + write-to-progress.md.
    """
    def _on_failure_log(returncode: int, stdout_tail: str, stderr_tail: str) -> None:
        logger.warning(
            "init.sh exited {} on SessionEnd: {}\n{}",
            returncode, stdout_tail, stderr_tail,
        )
        try:
            from datetime import datetime
            progress_path = workdir / "progress.md"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            with progress_path.open("a", encoding="utf-8") as f:
                f.write(f"\n## SessionEnd auto-record ({ts})\n")
                f.write(f"- status: FAILED (exit {returncode})\n")
                f.write(f"- last stdout: {stdout_tail}\n")
                f.write(f"- last stderr: {stderr_tail}\n")
                f.write(f"- session tool calls: ~{session_tool_calls}\n")
        except Exception as exc:
            logger.warning("Failed to write progress.md: {}", exc)

    def _on_complete(result: subprocess.CompletedProcess | None, error_msg: str) -> None:
        if error_msg == "timed out":
            logger.warning("init.sh timed out on SessionEnd")
            try:
                from datetime import datetime
                progress_path = workdir / "progress.md"
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                with progress_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n## SessionEnd auto-record ({ts})\n")
                    f.write("- status: TIMEOUT (init.sh >120s)\n")
                    f.write(f"- session tool calls: ~{session_tool_calls}\n")
            except Exception as exc:
                logger.warning("Failed to write progress.md: {}", exc)

    thread = schedule_init_sh_on_session_end(
        workdir, config,
        on_complete=_on_complete,
        on_failure_log=_on_failure_log,
        timeout=timeout,
    )
    thread.join()


def run_repl(resume: bool = False) -> None:
    apply_config(load_config(WORKDIR))

    # Discover and register user hook scripts from .minicode/hooks/
    _EVENT_MAP = {
        "session_start": "SessionStart",
        "session_end": "SessionEnd",
    }
    for event_name, scripts in discover_user_hooks(WORKDIR).items():
        hook_event = _EVENT_MAP.get(event_name, event_name)
        for script in scripts:
            try:
                hooks.register_hook(hook_event, make_shell_callback(script))
            except Exception:
                logger.warning("Failed to register user hook {} for {}", script, hook_event)

    print("输入问题，回车发送。\n")
    history: list = []                                   
    if resume and checkpoint.exists(WORKDIR):
        ckpt = checkpoint.load(WORKDIR)
        if ckpt is not None:
            history = ckpt.get("messages", [])
            saved_at = ckpt.get("saved_at", "unknown")
            logger.info(f"Resumed from checkpoint ({saved_at}, {len(history)} messages, {ckpt.get('tool_call_count', '?')} tool calls)")
    while True:
            try:
                query = input("\033[36m >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("exit", ""):
                break
            history.append({"role": "user", "content": query})
            agent_loop(history)
            for msg in history:
                for block in msg["content"]:
                    if getattr(block, "type", None) == "thinking":
                        print(f"\033[35m{block.thinking}\033[0m")
                    if getattr(block, "type", None) == "text":
                        print(block.text)
            print()
    hooks.trigger_hooks("SessionEnd", history, 0)
    schedule_init_sh_on_session_end(
        WORKDIR, _active_config,
        on_failure_log=_log_init_sh_failure_to_progress_md,
    )

