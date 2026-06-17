# Architecture

See `main.py::agent_loop` for the canonical implementation. This doc is a map, not a manual.

## Agent Loop

```text
[user input] → messages
  ↓
agent_loop(messages)
  ├── context.should_compact? ──yes──→ context.autocompact
  ├── llm_client.messages.create(tools=TOOLS)
  ├── context.update(tokens)
  ├── stop_reason == "tool_use"?
  │     ├── for each tool_use block:
  │     │     ├── hooks.trigger_hooks("PreToolUse", block)   ← may BLOCK
  │     │     ├── TOOL_HANDLERS[block.name](**block.input)
  │     │     └── hooks.trigger_hooks("PostToolUse", block, output)
  │     └── append tool_results → messages
  └── hooks.trigger_hooks("AgentStop", messages)
```

## Key Invariants

- **Tool handlers are pure functions of input** (no hidden state, except `CURRENT_TODOS` in `todo_write`).
- **Hooks can block tools**: a non-None return value from any `PreToolUse` callback short-circuits the tool; that return value becomes the `tool_result` content with `is_error=True`.
- **Subagent recursion is forbidden**: `SUB_TOOLS` does NOT include `task`. The subagent's `SUB_HANDLERS` mirrors the parents' tool set minus `task` and `todo_write`.

## Subagent (`task` tool)

`spawn_subagent(description)` runs an independent LLM loop (≤ 30 turns) with:

- Fresh `messages = [{"role": "user", "content": description}]`
- `SUB_TOOLS` (no `task`, no `todo_write`)
- Hard cap of 30 turns; final text is returned to the parent

The parent only sees the final summary — no message history crosses the boundary.

## Topic Docs

- `docs/tools.md` — tool definitions and safety
- `docs/hooks.md` — hook system, permission pipeline
- `docs/context.md` — context management, compression
- `docs/testing.md` — test strategy, fixtures
