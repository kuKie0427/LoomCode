import concurrent.futures
import os
import subprocess
import uuid
from pathlib import Path
from typing import cast

import dotenv
from loguru import logger

import loop.agent.checkpoint as checkpoint
import loop.agent.trace as trace_mod
from loop.agent.config import HarnessConfig, load_config
from loop.agent.context import Context
from loop.agent.hooks import Hooks
from loop.agent.llm import LLMClient
from loop.agent.prompt import SystemPrompt
from loop.agent.tools import (
    TOOL_HANDLERS,
    TOOLS,
)
from loop.agent.user_hooks import discover_user_hooks, make_shell_callback
from loop.memory import load_tier1, load_tier2
from loop.skills import build_skill_index

dotenv.load_dotenv()
WORKDIR = Path.cwd()


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
        WORKDIR / "loop.log",
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

    sp.add_dynamic(f"工作目录: {workdir}")
    sp.add_dynamic(sp.get_git_context(workdir))

    memory_parts: list[str] = []
    skill_index = build_skill_index(workdir).list_for_prompt()
    if skill_index:
        memory_parts.append(skill_index)
    tier1 = load_tier1(workdir)
    if tier1:
        memory_parts.append(tier1)
    tier2 = load_tier2(workdir)
    if tier2:
        memory_parts.append(tier2)
    if memory_parts:
        sp.add_memory("\n\n".join(memory_parts))

    return sp


SYSTEM = build_system_prompt().build()


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
    hooks.policy = config.policy
    hooks.disabled_tools = config.disabled_tools
hooks.register_hook("AgentStop", context.microcompact)

llm_client = LLMClient(model=os.getenv("MODEL", "deepseek-v4-flash"))


def _run_tool_block(block, hooks) -> dict:
    """Execute a single tool_use block and return the tool_result dict."""
    blocked = hooks.trigger_hooks("PreToolUse", block)
    if blocked is not None:
        tr = trace_mod.current()
        if tr is not None:
            tr.record("tool_denied", tool=block.name, reason=str(blocked)[:200])
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": blocked, "is_error": True}
    handler = TOOL_HANDLERS.get(block.name)
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


def agent_loop(messages: list, llm_client=None) -> None:
    configure_logging()
    hooks.trigger_hooks("SessionStart")
    if llm_client is None:
        llm_client = globals()["llm_client"]
    hooks.trigger_hooks("AgentStart")
    session_id = uuid.uuid4().hex[:12]
    trace_mod.start(WORKDIR, session_id)
    tr = trace_mod.current()
    if tr is not None:
        tr.record("session_start", workdir=str(WORKDIR), initial_messages=len(messages))
    tool_call_count = 0
    tokens_at_last_checkpoint = context.current_tokens(messages)
    while True:
        if context.should_compact(messages, llm_client.get_context_window()):
            hooks.trigger_hooks("PreCompact", messages, context.last_input_tokens)
            context.autocompact(messages, llm_client.client, llm_client.model, llm_client.get_context_window())
            if tr is not None:
                tr.record("autocompact", tool_calls_so_far=tool_call_count)
        response = llm_client.client.messages.create(
            model=llm_client.model, system=SYSTEM, messages=messages,
            tools=cast(list, TOOLS), max_tokens=8000,
        )
        context.update(len(messages), response)
        if tr is not None:
            tr.record("llm_response", stop_reason=response.stop_reason,
                      tokens_in=getattr(response.usage, "input_tokens", 0),
                      tokens_out=getattr(response.usage, "output_tokens", 0))
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            hooks.trigger_hooks("AgentStop", messages)
            checkpoint.save(WORKDIR, messages, llm_client, context, tool_call_count)
            if tr is not None:
                tr.record("session_end", tool_calls=tool_call_count, turns=len(messages))
            trace_mod.stop()
            return

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if tr is not None and tool_uses:
            tr.record("tool_batch", tools=[b.name for b in tool_uses], size=len(tool_uses))
        results = _run_tool_turn(tool_uses, hooks)
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

    if _active_config.run_init_sh_on_session_end:
        init_sh = WORKDIR / "init.sh"
        if init_sh.is_file():
            try:
                proc = subprocess.run(
                    [str(init_sh)], cwd=WORKDIR, capture_output=True,
                    text=True, timeout=120,
                )
                if proc.returncode != 0:
                    logger.warning(
                        "init.sh exited {} on SessionEnd: {}\n{}",
                        proc.returncode, proc.stdout[:200], proc.stderr[:200],
                    )
            except subprocess.TimeoutExpired:
                logger.warning("init.sh timed out on SessionEnd")
        else:
            logger.debug("init.sh not found, skip")

