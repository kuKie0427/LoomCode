import concurrent.futures
import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import dotenv
from loguru import logger

import loom.agent.checkpoint as checkpoint
import loom.agent.trace as trace_mod
from loom.agent.config import LLM_CONFIG, HarnessConfig, load_config
from loom.agent.context import Context
from loom.agent.hooks import Hooks
from loom.agent.llm import LLMClient, StreamEvent
from loom.agent.prompt import AGENTS_MD_STATIC_LIMIT, SystemPrompt
from loom.agent.subagent_templates import agent_display_name
from loom.agent.system_prompt import get_system_prompt
from loom.agent.tools import (
    get_tool_handlers,
    get_tools,
    is_concurrent_safe_tool,
)
from loom.agent.triangle_protocol import FeedbackDirective
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
_LAST_REVIEWED_FEATURE_ID: str | None = None


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


def get_active_callbacks() -> dict | None:
    """Return the currently active callbacks dict, or None if no loop is running."""
    return _active_callbacks


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

# PM-1: shut down any MCP servers we started during the session, and
# unregister their tools so stale entries don't leak into the next session.
# Coexists with _on_session_end (LSP) — both fire on SessionEnd; the
# manager functions are independent.
def _on_session_end_mcp(event, *args):
    try:
        from loom.agent.mcp_manager import shutdown_all as _mcp_shutdown_all
        _mcp_shutdown_all()
    except Exception:
        logger.warning("MCP shutdown_all raised", exc_info=True)
hooks.register_hook("SessionEnd", _on_session_end_mcp)

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

# PM-1: kick off background discovery for every [mcp.servers.*] entry.
# SessionStart already has the LSP journal-recovery hook above; this MCP
# hook is independent and both must fire. Signature MUST be (event, *args)
# because trigger_hooks calls callback(event, *args) — the LSP PL-3
# lesson was: a missing `event` first arg silently breaks the hook under
# real driver paths.
def _on_session_start_mcp(event, *args):
    try:
        from loom.agent.mcp_manager import start_discovery as _mcp_start_discovery
        _mcp_start_discovery(_active_config)
    except Exception:
        logger.warning("MCP discovery startup raised", exc_info=True)
hooks.register_hook("SessionStart", _on_session_start_mcp)

llm_client = LLMClient(model=os.getenv("MODEL") or "deepseek/deepseek-v4-flash")


# Tools that launch a subagent and should fire on_subagent_start / on_subagent_end
# (plus show a SubagentMarker in the TUI with the weaving-themed display name).
_SUBAGENT_TOOLS = frozenset({
    "task",
    "task_investigate_code",
    "task_refactor_across_files",
    "task_fix_failing_test",
    "review",
})

# Maps each subagent tool to the block.input key holding its human-readable
# description (used for the TUI SubagentMarker text).
_SUBAGENT_DESC_KEY: dict[str, str] = {
    "task": "description",
    "task_investigate_code": "question",
    "task_refactor_across_files": "pattern",
    "task_fix_failing_test": "test_path",
    "review": "feature_id",
}


def _run_tool_block(block, hooks) -> dict:
    """Execute a single tool_use block and return the tool_result dict.

    For subagent-launching tools (``task``, ``task_*``, ``review``), wraps
    the handler call with on_subagent_start / on_subagent_end (using
    ``block.id`` as subagent_id) so the TUI can map subagent overlay rows
    directly to ChatLog ToolCallMarkers. The weaving-themed display name
    (织针 / 飞梭 / 经线 / 织补 / 验布) is passed to on_subagent_start.
    """
    blocked = hooks.trigger_hooks("PreToolUse", block)
    if blocked is not None:
        tr = trace_mod.current()
        if tr is not None:
            tr.record("tool_denied", tool=block.name, reason=str(blocked)[:200])
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": blocked, "is_error": True}
    handler = get_tool_handlers().get(block.name)
    # B1: track error state so detect_repeated_failures can see exception-caught
    # failures. Previously is_error was hardcoded False, so the retry-detection
    # safety net only fired for tools that explicitly returned error results —
    # tools that raised exceptions were invisible to it, causing infinite retries.
    is_error = False
    if block.name in _SUBAGENT_TOOLS:
        cb = _active_callbacks
        agent_name = agent_display_name(block.name)
        desc_key = _SUBAGENT_DESC_KEY.get(block.name, "description")
        raw_desc = str(block.input.get(desc_key, ""))
        description = (raw_desc[:59] + "…") if len(raw_desc) > 60 else raw_desc
        is_background = bool(block.input.get("background", False))
        if cb is not None and cb.get("on_subagent_start") is not None:
            cb["on_subagent_start"](block.id, description, agent_name)
        t0 = time.monotonic()
        state = "done"
        try:
            if is_background:
                # Pass block.id as _subagent_id so the background thread
                # can fire on_subagent_end with the same id the TUI saw
                # in on_subagent_start.  The loop does NOT fire
                # on_subagent_end here — the background thread does it
                # when the subagent completes.
                kwargs = dict(block.input)
                kwargs["_subagent_id"] = block.id
                output = handler(**kwargs) if handler else f"Unknown: {block.name}"
            else:
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
        except Exception as exc:
            # P0-H: 不再 re-raise。如果 re-raise，_run_tool_turn 会抛出，
            # agent_loop 的 messages.append(tool_result) 永远不执行，
            # 历史就出现 "assistant 有 tool_calls 但后面不是 tool message"
            # 的非法状态——下次 LLM 调用会被 DeepSeek/OpenAI 拒绝 (400)。
            # 转为 error tool_result 返回，保证消息历史始终合法。
            state = "error"
            is_error = True
            output = f"Error: {block.name} raised {type(exc).__name__}: {exc}"
        finally:
            elapsed = time.monotonic() - t0
            # Skip on_subagent_end for background tasks — the background
            # thread fires it when the subagent actually completes.
            if not is_background:
                if cb is not None and cb.get("on_subagent_end") is not None:
                    cb["on_subagent_end"](block.id, elapsed, state)
    else:
        try:
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
        except Exception as exc:
            # P0-H: 捕获所有异常（不只 TypeError），转为 error tool_result。
            # 原来只捕 TypeError，其他异常上抛导致 tool_result 丢失，
            # 消息历史出现 "tool_calls 无对应 tool message" 的非法状态。
            is_error = True
            output = f"Error: {block.name} raised {type(exc).__name__}: {exc}"
    hooks.trigger_hooks("PostToolUse", block, output)
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": output, "is_error": is_error}


def _run_tool_turn(tool_uses, hooks):
    """Run a batch of tool_use blocks.

    Execution strategy (L2):
    - 'task' tools run concurrently (existing Fork mode).
    - Concurrent-safe tools (is_concurrent_safe=True: read_file, glob, grep,
      web_fetch, subagent_poll, subagent_list) run concurrently with each
      other — they have no side effects and the LLM cannot see mid-batch
      results, so within a single batch they are independent by construction.
    - All other non-task tools (bash, write_file, edit_file, todo_write, ...)
      run serially in their original order to preserve observable ordering.

    Order of execution: serial → concurrent-safe → task. Results are placed
    back into ``results[i]`` by index, so the caller always sees tool results
    in the same order as the LLM emitted them.
    """
    results: list[dict | None] = [None] * len(tool_uses)
    task_idx = [i for i, b in enumerate(tool_uses) if b.name == "task"]
    # L2: split non-task tools into concurrent-safe vs serial groups.
    concurrent_safe_idx: list[int] = []
    serial_idx: list[int] = []
    for i, b in enumerate(tool_uses):
        if b.name == "task":
            continue
        if is_concurrent_safe_tool(b.name):
            concurrent_safe_idx.append(i)
        else:
            serial_idx.append(i)

    # Serial first — preserves write-before-read ordering for stateful tools
    # (e.g. bash writes a file then edit_file edits it).
    for i in serial_idx:
        results[i] = _run_tool_block(tool_uses[i], hooks)

    # Concurrent-safe tools in parallel — bounded pool to avoid spawning
    # one thread per tool when the LLM emits a huge batch.
    if len(concurrent_safe_idx) == 1:
        results[concurrent_safe_idx[0]] = _run_tool_block(
            tool_uses[concurrent_safe_idx[0]], hooks
        )
    elif len(concurrent_safe_idx) > 1:
        max_workers = min(len(concurrent_safe_idx), 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_run_tool_block, tool_uses[i], hooks): i
                for i in concurrent_safe_idx
            }
            for fut in concurrent.futures.as_completed(futures):
                results[futures[fut]] = fut.result()

    # 'task' tools in parallel (existing Fork mode, unchanged).
    if len(task_idx) == 1:
        results[task_idx[0]] = _run_tool_block(tool_uses[task_idx[0]], hooks)
    elif len(task_idx) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(task_idx)) as ex:
            futures = {ex.submit(_run_tool_block, tool_uses[i], hooks): i for i in task_idx}
            for fut in concurrent.futures.as_completed(futures):
                results[futures[fut]] = fut.result()

    return results


def _save_session_store(session_id: str | None, messages: list, llm_client, context, tool_call_count: int, *, async_io: bool = False) -> None:
    """Best-effort save to the session store (alongside checkpoint.save)."""
    if session_id is None:
        return
    try:
        from loom.agent.session_store import SessionStore
        store = SessionStore(WORKDIR)
        store.save_session(session_id, messages, llm_client, context, tool_call_count, async_io=async_io)
    except Exception:
        pass  # session store failures must never block the agent loop


def agent_loop(messages: list, llm_client=None, callbacks: dict | None = None, stream_text: Callable[[str, list, list, int], Iterator[StreamEvent]] | None = None, session_id: str | None = None) -> None:
    configure_logging()
    global _LAST_REVIEWED_FEATURE_ID
    _LAST_REVIEWED_FEATURE_ID = None
    # Reset per-feature review attempt counter at session boundary (TP-3).
    # TP-4 will replace this with feature_list.json persistence.
    from loom.agent.review import _REVIEW_ATTEMPT_COUNTER
    _REVIEW_ATTEMPT_COUNTER.clear()
    # Prune stale background subagent entries from previous sessions
    try:
        from loom.agent.background_registry import get_registry
        get_registry().cleanup_stale()
    except Exception:
        pass
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
            # Use the provided session_id (from TUI/CLI) or generate a new one.
            if session_id is None:
                session_id = uuid.uuid4().hex[:12]
            trace_mod.start(WORKDIR, session_id)
            tr = trace_mod.current()
            if tr is not None:
                tr.record("session_start", workdir=str(WORKDIR), initial_messages=len(messages))
            from loom.agent.cost import reset_session_cost
            reset_session_cost()
            tool_call_count = 0
            # P0-F: counter for unfinished_intent reminders (capped to avoid infinite loop)
            unfinished_intent_count = 0
            tokens_at_last_checkpoint = context.current_tokens(messages)
            max_turns = _active_config.max_turns
            for _turn in range(max_turns):
                from loom.agent.tool_errors import build_retry_guidance, detect_repeated_failures
                detection = detect_repeated_failures(messages)
                if detection is not None and (not messages or messages[-1].get("role") != "user" or "<system-reminder>" not in str(messages[-1].get("content", ""))[:200]):
                    messages.append({"role": "user", "content": build_retry_guidance(detection)})
                    logger.warning("tool_errors: detected {}-time failure of {}", detection["failure_count"], detection["tool"])
                if context.should_compact(messages, llm_client.get_context_window(), llm_client.model):
                    hooks.trigger_hooks("PreCompact", messages, context.last_input_tokens)
                    if _active_config.review.enabled and _active_config.review.pre_compact_review:
                        _run_pre_compact_review(messages, _active_config)
                    msg_count_before = len(messages)
                    context.autocompact(messages, llm_client, llm_client.get_context_window())
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
                    from loom.agent.providers.types import (
                        ProviderResponse,
                        StopReason,
                        TextBlock,
                        ToolUseBlock,
                        Usage,
                    )
                    content_blocks: list = []
                    input_tokens = 0
                    output_tokens = 0
                    cache_read_tokens = 0
                    cache_creation_tokens = 0
                    reasoning_tokens = 0
                    stop_reason = "end_turn"
                    for ev in stream_text(_get_system_prompt(), messages, cast(list, get_tools()), LLM_CONFIG.max_output_tokens):
                        if ev.kind == "text":
                            content_blocks.append(TextBlock(text=ev.text))
                            if cb["on_text_delta"] is not None:
                                cb["on_text_delta"](ev.text)
                        elif ev.kind == "thinking":
                            if cb["on_thinking_delta"] is not None:
                                cb["on_thinking_delta"](ev.text)
                        elif ev.kind == "tool_use":
                            content_blocks.append(ToolUseBlock(
                                id=ev.tool_id,
                                name=ev.tool_name,
                                input=ev.tool_input or {},
                            ))
                        elif ev.kind == "usage":
                            if ev.input_tokens:
                                input_tokens = ev.input_tokens
                            if ev.output_tokens:
                                output_tokens = ev.output_tokens
                            if ev.cache_read_tokens:
                                cache_read_tokens = ev.cache_read_tokens
                            if ev.cache_creation_tokens:
                                cache_creation_tokens = ev.cache_creation_tokens
                            if ev.reasoning_tokens:
                                reasoning_tokens = ev.reasoning_tokens
                            if ev.stop_reason:
                                stop_reason = ev.stop_reason
                        elif ev.kind == "error":
                            err_text = (
                                f"[LLM error: {ev.error_code or 'unknown'}] "
                                f"{ev.error_message or 'provider returned an error event'}"
                            )
                            if ev.error_code == "auth":
                                err_text += "\n→ Run /connect to register your API key."
                            logger.error("agent_loop stream error: {}", err_text)
                            content_blocks.append(TextBlock(text=err_text))
                            if cb["on_text_delta"] is not None:
                                cb["on_text_delta"](err_text)
                            if tr is not None:
                                tr.record(
                                    "llm_error",
                                    code=str(ev.error_code or "unknown"),
                                    message=str(ev.error_message or "")[:500],
                                )
                            stop_reason = "end_turn"
                    response = ProviderResponse(
                        model=llm_client.model,
                        content=content_blocks,
                        stop_reason=StopReason(stop_reason) if stop_reason in StopReason._value2member_map_ else StopReason.END_TURN,
                        usage=Usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                            reasoning_tokens=reasoning_tokens,
                        ),
                    )
                else:
                    # ===== SYNC PATH — provider-agnostic via LLMClient.invoke() =====
                    current_tools = get_tools()
                    response = llm_client.invoke(
                        system=_get_system_prompt(),
                        messages=messages,
                        tools=current_tools,
                        max_tokens=LLM_CONFIG.max_output_tokens,
                    )
                context.update(len(messages), response)
                if tr is not None:
                    from loom.agent.cost import record_turn, usage_from_response
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
                    # P0-F: 检测"未完成意图"——LLM 声明要做某事（"我来直接创建 CSS："）
                    # 但本轮 0 工具调用就 end_turn 了。注入 system-reminder 强制继续。
                    # 防死循环：最多注入 MAX_UNFINISHED_INTENT_REMINDERS 次。
                    if tool_call_count > 0:
                        from loom.agent.tool_errors import (
                            MAX_UNFINISHED_INTENT_REMINDERS,
                            build_unfinished_intent_guidance,
                            detect_unfinished_intent,
                        )
                        matched = detect_unfinished_intent(response.content)
                        if matched is not None and unfinished_intent_count < MAX_UNFINISHED_INTENT_REMINDERS:
                            unfinished_intent_count += 1
                            reminder = build_unfinished_intent_guidance(matched, unfinished_intent_count)
                            messages.append({"role": "user", "content": reminder})
                            if tr is not None:
                                tr.record(
                                    "unfinished_intent_reminder",
                                    matched_tail=matched[:200],
                                    attempt=unfinished_intent_count,
                                )
                            logger.warning(
                                "unfinished_intent: matched={!r} attempt={}/{}",
                                matched[:80], unfinished_intent_count, MAX_UNFINISHED_INTENT_REMINDERS,
                            )
                            continue
                    hooks.trigger_hooks("AgentStop", messages)
                    checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count, async_io=True)
                    _save_session_store(session_id, messages, llm_client, context, tool_call_count, async_io=True)
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
                try:
                    results = _run_tool_turn(tool_uses, hooks)
                except Exception as exc:
                    # P0-H safety net: _run_tool_block 已捕获工具异常，
                    # 但 hooks (PreToolUse/PostToolUse) 仍可能抛出。
                    # 如果此处抛出，messages.append(tool_result) 不执行，
                    # 历史就会出现 "tool_calls 无 tool message" 的非法状态。
                    # 合成全 error tool_results 保证历史合法。
                    logger.error("_run_tool_turn raised; synthesizing error results: {}", exc)
                    results = [
                        {"type": "tool_result", "tool_use_id": b.id,
                         "content": f"Error: _run_tool_turn raised {type(exc).__name__}: {exc}",
                         "is_error": True}
                        for b in tool_uses
                    ]
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
                    saved_path = checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count, async_io=True)
                    _save_session_store(session_id, messages, llm_client, context, tool_call_count, async_io=True)
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
                        tr.record("loop_limit_reached", turn=_turn + 1, tool_calls=tool_call_count,
                                  max_turns=max_turns, **d)
                    else:
                        tr.record("loop_limit_reached", turn=_turn + 1, tool_calls=tool_call_count,
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
                checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count, async_io=True)
                _save_session_store(session_id, messages, llm_client, context, tool_call_count, async_io=True)
                if cb["on_message_end"] is not None:
                    cb["on_message_end"](tool_call_count, len(messages))
                trace_mod.stop()
                return
    finally:
        # L6: ensure all background checkpoint/session writes are durable
        # before returning. Typically a no-op (writes finish during the
        # next LLM call); only blocks if the disk is very slow.
        try:
            checkpoint.flush_pending_writes()
        except Exception:
            pass
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


def _write_verdict_to_progress_md(workdir: Path, feature_id: str, verdict_str: str) -> None:
    """Append a review verdict to progress.md (best-effort).

    Used by ``_run_session_end_review`` to log the auto-review result.
    Best-effort: swallows all exceptions (OSError specifically).
    """
    try:
        import datetime
        progress_path = workdir / "progress.md"
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        section = (
            f"\n\n## Final Review (auto, {ts})\n\n"
            f"**Feature**: {feature_id}\n\n"
            f"**Verdict**:\n{verdict_str}\n"
        )
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError as exc:
        logger.warning("Failed to write review verdict to progress.md: {}", exc)


def _find_active_feature_for_review(workdir: Path | None = None) -> dict | None:
    """Read ``feature_list.json`` and find the first active feature.

    Args:
        workdir: Project directory. Falls back to ``WORKDIR`` when ``None``.

    Returns the feature dict (with ``id``, ``name``, ``description``,
    ``status`` keys) or None if no active feature is found.
    """
    wd = workdir if workdir is not None else WORKDIR
    try:
        fl_path = wd / "feature_list.json"
        if not fl_path.exists():
            return None
        fl = json.loads(fl_path.read_text(encoding="utf-8"))
        active = [
            f
            for f in fl.get("features", [])
            if isinstance(f, dict) and f.get("status") in ("in-progress", "review-pending")
        ]
        if not active:
            return None
        if len(active) > 1:
            logger.warning(
                "Multiple active features found, reviewing first only: %s",
                [f.get("id") for f in active],
            )
        return active[0]
    except OSError as exc:
        logger.warning("_find_active_feature_for_review: IO error: %s", exc)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("_find_active_feature_for_review: invalid JSON: %s", exc)
        return None


def _increment_review_attempts(feat_id: str) -> None:
    """I9 persistence: increment review_attempts in feature_list.json.

    Counter survives autocompact and cross-session (stored in JSON, not prompt).
    Reset by ``_reset_review_attempts`` on pass verdict.
    """
    import json
    fl_path = WORKDIR / "feature_list.json"
    if not fl_path.exists():
        return
    try:
        fl = json.loads(fl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("_increment_review_attempts: read failed: %s", exc)
        return
    for f in fl.get("features", []):
        if f.get("id") == feat_id:
            f["review_attempts"] = f.get("review_attempts", 0) + 1
            break
    try:
        fl_path.write_text(json.dumps(fl, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("_increment_review_attempts: write failed: %s", exc)


def _reset_review_attempts(feat_id: str) -> None:
    """I9: reset counter to 0 on pass verdict (called from _run_session_end_review)."""
    import json
    fl_path = WORKDIR / "feature_list.json"
    if not fl_path.exists():
        return
    try:
        fl = json.loads(fl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("_reset_review_attempts: read failed: %s", exc)
        return
    for f in fl.get("features", []):
        if f.get("id") == feat_id:
            f["review_attempts"] = 0
            break
    try:
        fl_path.write_text(json.dumps(fl, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("_reset_review_attempts: write failed: %s", exc)


def _parse_pre_compact_verdict_status(verdict_str: str) -> str:
    """Extract status from "[review: <status>]" prefix in verdict_str.

    Returns "unknown" if no match (defensive default).
    """
    import re
    m = re.search(r"\[review:\s*(\w+)", verdict_str)
    return m.group(1) if m else "unknown"


def _run_pre_compact_review(messages: list, config: HarnessConfig) -> None:
    """Run a code review before autocompact, if a new active feature is found.

    TP-4 upgrade: use new ``run_review`` return value (verdict_str + FeedbackDirective).
    C10 fix: system-reminder now includes the serialized <feedback_directive> block
    in addition to verdict_str, so the Orchestrator LLM sees both the verdict and
    the action directive it must execute.

    Fail-closed: exceptions are caught and logged; autocompact proceeds.
    Uses ``_LAST_REVIEWED_FEATURE_ID`` to avoid re-reviewing the same feature
    multiple times within the same session.
    """
    global _LAST_REVIEWED_FEATURE_ID
    if not config.review.enabled or not config.review.pre_compact_review:
        return
    try:
        feat = _find_active_feature_for_review()
        if feat is None:
            return
        feat_id = feat.get("id")
        if not feat_id or feat_id == _LAST_REVIEWED_FEATURE_ID:
            return
        from loom.agent.review import run_review

        verdict_str, fd = run_review(
            feat_id,
            feat.get("description", ""),
            feat.get("name", ""),
        )

        # I9 persistence: bump review_attempts in feature_list.json
        _increment_review_attempts(feat_id)

        # C10 fix: include <feedback_directive> block in addition to verdict_str
        reminder_content = (
            f"[system-reminder] PreCompact review verdict for {feat_id}:\n"
            f"{verdict_str}\n"
        )
        if fd is not None:
            from loom.agent.triangle_protocol import serialize_feedback_directive
            reminder_content += "\n" + serialize_feedback_directive(fd) + "\n"
        reminder_content += "保留此 verdict 作为下次 session 的上下文。"

        messages.append({
            "role": "user",
            "content": reminder_content,
        })

        # TP-4: if feedback_directive has a non-none action, trace triangle.feedback
        # and (in future phases) execute the action. action is now a list (TP-1 design).
        if fd is not None and "none" not in fd.action:
            _execute_feedback_directive(feat_id, fd)

        _LAST_REVIEWED_FEATURE_ID = feat_id
    except Exception as exc:
        logger.warning("_run_pre_compact_review: failed: %s", exc)


def _run_session_end_review(workdir: Path, config: HarnessConfig, history: list) -> None:
    """Fire-and-forget code review at session end.

    Spawns a thread that reads ``feature_list.json``, finds the active
    feature, calls ``run_review()``, writes the verdict to progress.md, and
    updates I9 ``review_attempts`` counter (reset on pass, increment on non-pass).

    P1-2 fix: previously used a daemon thread that could be killed by process
    exit before the Reviewer LLM call (typically 20-30s) completed. Now uses
    a non-daemon thread with a bounded join (default 60s) so the review has
    time to finish. The join is bounded so a hung LLM call doesn't block
    process exit indefinitely.
    """
    def _runner() -> None:
        if not config.review.enabled or not config.review.session_end_review:
            return
        try:
            feat = _find_active_feature_for_review(workdir)
            if feat is None:
                return
            from loom.agent.review import run_review

            feat_id = feat["id"]
            verdict_str, fd = run_review(
                feat_id, feat.get("description", ""), feat.get("name", ""),
                workdir=workdir, flip_status_on_pass=True,
            )
            _write_verdict_to_progress_md(workdir, feat_id, verdict_str)

            # I9: reset on pass, increment on non-pass (mirror _run_pre_compact_review)
            status = _parse_pre_compact_verdict_status(verdict_str)
            if status == "pass":
                _reset_review_attempts(feat_id)
            else:
                _increment_review_attempts(feat_id)
        except Exception as exc:
            logger.warning("SessionEnd review failed: %s", exc)

    # P1-2: non-daemon thread + bounded join. Daemon threads die when the
    # main thread exits, which killed SessionEnd reviews in Phase B (the
    # Reviewer LLM call takes ~27s but the main thread exited in <1s).
    # 60s bound is generous: longest observed review was 27s; if a future
    # review exceeds 60s, we let the thread be killed rather than block.
    t = threading.Thread(target=_runner, daemon=False, name="session-end-review")
    t.start()
    t.join(timeout=60.0)
    if t.is_alive():
        logger.warning("SessionEnd review thread still alive after 60s join; proceeding with exit")


def _execute_feedback_directive(feat_id: str, fd: FeedbackDirective) -> None:
    """Record triangle.feedback trace event and execute the action.

    TP-3 only records the trace event. Actual action execution (scope_trim,
    fix_bug, etc.) is Orchestrator-side logic implemented in TP-4 via
    system prompt guidance — the trace event here is the contract surface.
    """
    import loom.agent.trace as trace_mod
    from loom.agent.review import _REVIEW_ATTEMPT_COUNTER
    tr = trace_mod.current()
    if tr is not None:
        try:
            tr.record(
                trace_mod.TRIANGLE_FEEDBACK,
                feature_id=feat_id,
                action=list(fd.action),
                target_files=list(fd.target_files),
                retry_count=_REVIEW_ATTEMPT_COUNTER.get(feat_id, 0),
            )
        except Exception as trace_exc:
            logger.warning("_execute_feedback_directive: trace.record(triangle.feedback) failed: {}", trace_exc)
    # Action execution intentionally no-op in TP-3; see TP-4 plan.


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


_REPL_ERROR_COLORS = {
    "error": "\033[31m",      # red
    "warning": "\033[33m",    # yellow
    "info": "\033[36m",       # cyan
}
_RESET = "\033[0m"


def _print_repl_error(exc: Exception) -> None:
    """Print a friendly error to stderr instead of crashing the REPL.

    Mirrors ``AgentTUIApp._handle_turn_exception``: classifies
    ``ProviderError`` by code (auth / network / rate_limit / ...) and
    prints a one-line actionable hint. Non-ProviderError exceptions are
    printed with type + message. The REPL loop continues so the user
    can retry after fixing the underlying issue (e.g. setting an API key).
    """
    from loom.agent.providers.types import ProviderError

    code = getattr(exc, "code", "") or "" if isinstance(exc, ProviderError) else ""
    msg = getattr(exc, "message", str(exc)) or str(exc)

    if isinstance(exc, ProviderError):
        if code in ("auth", "missing_credential"):
            text = f"API key 缺失或无效：{msg}。请配置凭证后重试。"
            severity = "error"
        elif code == "rate_limit":
            text = f"请求被限流：{msg}。请稍后重试。"
            severity = "warning"
        elif code in ("network", "timeout"):
            text = f"网络错误：{msg}。请检查网络后重试。"
            severity = "warning"
        elif code == "context_overflow":
            text = f"上下文超长：{msg}。请用 /clear 或 /compact 压缩对话。"
            severity = "warning"
        else:
            text = f"模型调用失败（{code}）：{msg}"
            severity = "error"
    else:
        text = f"agent_loop 异常：{type(exc).__name__}: {exc}"
        severity = "error"

    color = _REPL_ERROR_COLORS.get(severity, _REPL_ERROR_COLORS["error"])
    import sys

    print(f"{color}{text}{_RESET}", file=sys.stderr)


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
            try:
                agent_loop(history)
            except Exception as exc:
                # Friendly error instead of crashing the REPL — mirrors
                # AgentTUIApp._handle_turn_exception.  Keeps the REPL
                # alive so the user can re-run after fixing credentials.
                _print_repl_error(exc)
                continue
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
    _run_session_end_review(WORKDIR, _active_config, history)

