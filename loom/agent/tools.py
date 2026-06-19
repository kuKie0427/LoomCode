import subprocess
import time
from pathlib import Path

from anthropic.types import MessageParam, ToolParam
from loguru import logger

import loom.agent.trace as trace_mod
from loom.agent.permissions import DEFAULT_POLICY
from loom.agent.tool_registry import Tool, ToolRegistry
from loom.memory import MemoryStore
from loom.skills import build_skill_index

WORKDIR = Path.cwd()

CURRENT_TODOS: list = []

def run_bash(command: str) -> str:
    matched = DEFAULT_POLICY.matches_deny(command)
    if matched is not None:
        return f"Error: Dangerous command blocked (matched: {matched})"
    try:
        r = subprocess.run(
            command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g
    try:
        results = []
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

def run_todo_write(todos: list) -> str:
    global CURRENT_TODOS
    for i, t in enumerate(todos):
        if "content" not in t or "status" not in t:
            return f"Error: todos[{i}] missing 'content' or 'status'"
        if t["status"] not in ("pending", "in_progress", "completed"):
            return f"Error: todos[{i}] has invalid status '{t['status']}'"
    CURRENT_TODOS = todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for t in CURRENT_TODOS:
        icon = {"pending": " ", "in_progress": "\033[36m▸\033[0m", "completed": "\033[32m✓\033[0m"}[t["status"]]
        lines.append(f"  [{icon}] {t['content']}")
    logger.info("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"


def run_memory_read() -> str:
    return MemoryStore(WORKDIR).read()

def run_memory_search(query: str) -> str:
    matches = MemoryStore(WORKDIR).search(query)
    if not matches:
        return f"(no matches for {query!r})"
    return "\n".join(matches)

def run_memory_write(entry: str, heading: str | None = None) -> str:
    try:
        MemoryStore(WORKDIR).append(entry, heading=heading)
    except ValueError as e:
        return f"Memory cap exceeded: {e}"
    return f"Appended {len(entry)} chars to MEMORY.md"


def run_load_skill(name: str) -> str:
    skill = build_skill_index(WORKDIR).get(name)
    if skill is None:
        return f"Error: skill {name!r} not found in {WORKDIR / '.minicode/skills'}"
    if not skill.has_body:
        return f"Error: skill {name!r} has no body (SKILL.md is empty after the metadata section)"
    return skill.body


TOOL_REGISTRY = ToolRegistry()
TOOL_REGISTRY.register(Tool(
    name="bash",
    description="Run a shell command.",
    input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    handler=run_bash,
))
TOOL_REGISTRY.register(Tool(
    name="read_file",
    description="Read file contents.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]},
    handler=run_read,
    is_read_only=True,
    is_concurrent_safe=True,
))
TOOL_REGISTRY.register(Tool(
    name="write_file",
    description="Write content to a file.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    handler=run_write,
))
TOOL_REGISTRY.register(Tool(
    name="edit_file",
    description="Replace exact text in a file once.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
    handler=run_edit,
))
TOOL_REGISTRY.register(Tool(
    name="glob",
    description="Find files matching a glob pattern.",
    input_schema={"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    handler=run_glob,
    is_read_only=True,
    is_concurrent_safe=True,
))
TODO_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["content", "status"],
            },
        },
    },
    "required": ["todos"],
}
TOOL_REGISTRY.register(Tool(
    name="todo_write",
    description="Create and manage a task list for your current coding session.",
    input_schema=TODO_WRITE_SCHEMA,
    handler=run_todo_write,
))
TOOL_REGISTRY.register(Tool(
    name="memory_read",
    description="Read the project's MEMORY.md (long-term cross-session memory).",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=run_memory_read,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="memory_search",
    description="Search MEMORY.md for lines containing the query (case-insensitive).",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    handler=run_memory_search,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="memory_write",
    description="Append a dated entry to MEMORY.md.",
    input_schema={"type": "object", "properties": {"entry": {"type": "string"}, "heading": {"type": "string"}}, "required": ["entry"]},
    handler=run_memory_write,
))
TOOL_REGISTRY.register(Tool(
    name="load_skill",
    description="Load a skill's body into context by name. Call after seeing the skill index in your system prompt.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    handler=run_load_skill,
    is_read_only=True,
))

TOOLS = TOOL_REGISTRY.to_anthropic_schema()
TOOL_HANDLERS = {name: TOOL_REGISTRY.handler_for(name) for name in TOOL_REGISTRY.names()}

SUB_TOOLS: list[ToolParam] = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

SUB_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}

SUB_SYSTEM = (
    "你是一个编程子智能体（subagent），由主 agent 通过 `task` 工具委派任务。\n"
    "你的工作目录和主 agent 相同，但上下文独立（不继承主 agent 的对话历史）。\n"
    "规则：\n"
    "- 专注完成委派给你的任务，不要做超出范围的工作\n"
    "- 不要进一步委派（不要调用 task 工具）\n"
    "- 完成后用简洁的摘要返回结果\n"
    "- 如果任务需要多步，列出你做了什么、找到/改了什么\n"
    "- 遇到错误就直接报告，不要重试同一操作超过一次"
)


def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")


def spawn_subagent(description: str, llm_client=None, hooks=None) -> str:
    if llm_client is None or hooks is None:
        from loom.agent.loop import hooks as _hooks
        from loom.agent.loop import llm_client as _llm_client
        llm_client = llm_client or _llm_client
        hooks = hooks or _hooks
    tr = trace_mod.current()
    if tr is not None:
        tr.record("subagent_start", description_len=len(description))
    t0 = time.monotonic()
    hooks.trigger_hooks("AgentStart")
    messages: list[MessageParam] = [{"role": "user", "content": description}]

    turn_count = 0
    tool_call_count = 0
    for _ in range(30):
        turn_count += 1
        response = llm_client.client.messages.create(
            model=llm_client.model, system=SUB_SYSTEM,
            messages=messages, tools=SUB_TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_call_count += 1
                blocked = hooks.trigger_hooks("PreToolUse", block)
                if blocked:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(blocked), "is_error": True})
                    continue
                handler = SUB_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
                hooks.trigger_hooks("PostToolUse", block, output)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": False})
        messages.append({"role": "user", "content": results})

    result = extract_text(messages[-1]["content"])
    if not result:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                result = extract_text(msg["content"])
                if result:
                    break
        if not result:
            result = "Subagent stopped after 30 turns without final answer."
    hooks.trigger_hooks("AgentStop", messages)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if tr is not None:
        tr.record("subagent_end", turns=turn_count, tool_calls=tool_call_count,
                  duration_ms=elapsed_ms)
    return f"[done: {turn_count} turns, {tool_call_count} tool calls]\n{result}"


TOOLS.append({
    "name": "task",
    "description": "Launch a subagent to handle a complex subtask. Returns only the final conclusion.",
    "input_schema": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
})


def run_task(description: str) -> str:
    return spawn_subagent(description)


TOOL_HANDLERS["task"] = run_task
