# Hooks

Four events: `AgentStart`, `PreToolUse`, `PostToolUse`, `AgentStop`. Implementation in `hook.py::Hooks`.

## Pipeline Order

For each `tool_use` block:

1. **`PreToolUse`** callbacks run in registration order. If any returns non-None → the tool is **blocked**; that return value becomes the `tool_result` content with `is_error=True`. The handler does NOT run.
2. **The tool handler runs** (if not blocked).
3. **`PostToolUse`** callbacks run. None of them gate execution; they observe and log.

`AgentStart` / `AgentStop` are session-level events; `AgentStop` callbacks are run after the loop returns.

## Permission Pipeline (3 gates, in `check_permission_hook`)

1. **Deny list** (`DENY_LIST`): hard-block dangerous bash patterns. Always wins, no user override. Triggers for `rm -rf /`, `sudo`, `shutdown`, `reboot`, `mkfs`, `dd if=`, `> /dev/sda`.
2. **Permission rules** (`PERMISSION_RULES`): check if the tool call matches a rule. Current rules:
   - `write_file` / `edit_file` with path outside WORKDIR → "Writing outside workspace"
   - `bash` containing `rm `, `> /etc/`, `chmod 777` → "Potentially destructive command"
3. **User approval** (`_ask_user`): interactive `y/N` prompt when a rule matches. Default = deny. `y`/`yes` = allow.

## Registered Hooks (current)

```python
# main.py
hooks.register_hook("PreToolUse", hooks.check_permission_hook)
hooks.register_hook("PreToolUse", hooks.log_hook)
hooks.register_hook("PostToolUse", hooks.log_hook)
hooks.register_hook("AgentStart", hooks.log_hook)
hooks.register_hook("AgentStop", hooks.log_hook)
hooks.register_hook("AgentStop", context.microcompact)
```

`check_permission_hook` runs **before** `log_hook` — that's intentional. A denied tool still gets logged (we want to see what was attempted), but it doesn't execute.

`context.microcompact` is registered on `AgentStop` for the main loop. Subagents (`spawn_subagent`) also trigger `AgentStop` and thus microcompact on their way out.

## Registering a new hook

1. Define a callback: `def my_hook(event: str, *args) -> Optional[str]: ...`
2. Return `None` to pass; return a string to block (only meaningful for `PreToolUse`).
3. Register with `hooks.register_hook("PreToolUse", my_hook)` (or other event).
4. Hooks run in **registration order** within an event. Register order matters.
