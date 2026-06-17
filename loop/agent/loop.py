import os
from pathlib import Path

import dotenv
from loguru import logger

from loop.agent.context import Context
from loop.agent.hooks import Hooks
from loop.agent.llm import LLMClient
from loop.agent.prompt import SystemPrompt
from loop.agent.tools import (
    TOOL_HANDLERS,
    TOOLS,
)
from loop.memory import load_tier1, load_tier2

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

    memory_tier1 = load_tier1(workdir)
    memory_tier2 = load_tier2(workdir)
    if memory_tier1 or memory_tier2:
        sp.add_memory(memory_tier1 + ("\n\n" if memory_tier1 and memory_tier2 else "") + memory_tier2)

    return sp


SYSTEM = build_system_prompt().build()


context = Context()
hooks = Hooks()
hooks.register_hook("PreToolUse", hooks.check_permission_hook)
hooks.register_hook("PreToolUse", hooks.log_hook)
hooks.register_hook("PostToolUse", hooks.log_hook)
hooks.register_hook("AgentStart", hooks.log_hook)
hooks.register_hook("AgentStop", hooks.log_hook)
hooks.register_hook("AgentStop", context.microcompact)

llm_client = LLMClient(model=os.getenv("MODEL", "deepseek-v4-flash"))


def agent_loop(messages: list) -> None:
    configure_logging()
    hooks.trigger_hooks("AgentStart")
    while True:
        if context.should_compact(messages, llm_client.get_context_window()):
            context.autocompact(messages, llm_client.client, llm_client.model, llm_client.get_context_window())
        response = llm_client.client.messages.create(
            model=llm_client.model, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        context.update(len(messages), response)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            hooks.trigger_hooks("AgentStop", messages)
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            blocked = hooks.trigger_hooks("PreToolUse", block)
            if blocked is not None:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": blocked, "is_error": True})
                continue
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            hooks.trigger_hooks("PostToolUse", block, output)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": False})

        messages.append({"role": "user", "content": results})


def run_repl() -> None:
    print("输入问题，回车发送。\n")
    history: list = []
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
