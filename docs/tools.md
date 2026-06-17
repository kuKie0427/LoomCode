# Tools

Defined in `main.py::TOOLS` (parent) and `SUB_TOOLS` (subagent). Implemented by `TOOL_HANDLERS` / `SUB_HANDLERS` dicts.

## Parent tools

| Tool | Handler | Safety surface |
|---|---|---|
| `bash` | `run_bash` | Deny list (`rm -rf /`, `sudo`, `shutdown`, `reboot`, `mkfs`, `dd if=`, `> /dev/sda`); permission rules (`rm `, `> /etc/`, `chmod 777`) |
| `read_file` | `run_read` | `safe_path` resolves inside WORKDIR; rejects escapes |
| `write_file` | `run_write` | Same `safe_path` check + auto-mkdir parents |
| `edit_file` | `run_edit` | Same `safe_path` + exact-text match (single replacement) |
| `glob` | `run_glob` | Scoped to WORKDIR |
| `todo_write` | `run_todo_write` | Schema validation (`content` + `status` enum) |
| `task` | `spawn_subagent` | Subagent uses `SUB_TOOLS` (no recursion) |

## Subagent tools (`SUB_TOOLS`)

`bash`, `read_file`, `write_file`, `edit_file`, `glob` — same handlers, but:

- No `task` (no recursion)
- No `todo_write` (subagent has no in-session task tracking)

## Safety guarantees

- **Workspace boundary**: All file tools resolve paths through `safe_path(p)`, which uses `Path.resolve().is_relative_to(WORKDIR)`. Any escape raises `ValueError` and is surfaced as a tool error to the LLM.
- **Bash sandboxing**: Two-tier — `DENY_LIST` (hard block, no user override) and `PERMISSION_RULES` (triggers interactive `y/N` prompt via `_ask_user`).
- **Timeout**: `run_bash` has a 120s timeout; long-running commands fail with a clear error.

## Adding a new tool

1. Add the entry to `TOOLS` (and optionally `SUB_TOOLS`) with `name` + `description` + `input_schema`.
2. Implement the handler as a plain function `def run_X(...) -> str: ...`
3. Register the handler in `TOOL_HANDLERS` (and `SUB_HANDLERS` if applicable).
4. Add at least one happy-path test in `tests/test_tools.py::TestRunX`.
5. Run `./init.sh` to confirm.
