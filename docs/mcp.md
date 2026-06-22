# MCP (Model Context Protocol) Integration

loom ships a generic MCP client that lets the agent call any standards-compliant MCP server as a tool. Discovered tools are registered into `TOOL_REGISTRY` and exposed to the main agent loop; a per-server `subagent_access` opt-in gate controls whether `spawn_subagent` subagents can call them too. The implementation lives in `loom/agent/mcp_client.py` and `loom/agent/mcp_manager.py`.

## 1. What & Why

The **Model Context Protocol** is an open standard (originally developed by Anthropic) that lets a single model talk to many external tools through one uniform JSON-RPC interface. An MCP *server* is a long-running process that advertises a list of *tools* (name + JSON-schema'd arguments) and executes them on demand. The spec lives at <https://modelcontextprotocol.io/specification>.

loom's **BYO (Bring Your Own) server** philosophy: we don't bundle MCP servers. You install the servers you care about (`npx -y @modelcontextprotocol/server-filesystem`, `pip install mcp-server-sqlite`, etc.), point `harness.toml` at the binary, and loom spawns them lazily in the background. The discovery handshake (`initialize` + `tools/list`) is the only standardized wire; everything else — the loop integration, the 3-state permission gate, the per-server lock, the 50KB output cap — is loom's own design, documented below.

loom wraps the raw JSON-RPC layer in four mechanisms:

- **TOOL_REGISTRY registration** — discovered tools become first-class tools in the agent loop, indistinguishable from native ones except for the `mcp__server__tool` name.
- **3-state permission gate** (PM-2) — every `mcp__*` call goes through deny / auto_approve / prompt; never a silent allow.
- **Per-server lock** — concurrent calls to the *same* server serialize on a `threading.Lock` (JSON-RPC has no multiplexing on a single stdin/stdout).
- **Output-shape mitigations** (PM-3) — flatten the `content` list, cap at 50KB, crash-recovery evict the dead server.

## 2. Install hints

Three of the most useful reference servers, with their canonical install command. The agent only spawns a server when its `[mcp.servers.<name>]` block exists in `harness.toml`, so installing extras has no runtime cost.

### Filesystem

```bash
npx -y @modelcontextprotocol/server-filesystem /Users/me/project
```

`<dir>` is the only allowed root. Reads, writes, and directory listings are all gated by `[mcp.permissions]` patterns — see §4.

### GitHub

```bash
npx -y @modelcontextprotocol/server-github
```

Requires `GITHUB_TOKEN` in the env block (see §3) — loom does **NOT** inherit your shell's `GITHUB_TOKEN`. Tools include `create_issue`, `search_code`, `create_pull_request`, etc.

### SQLite

```bash
npx -y @modelcontextprotocol/server-sqlite --db-path /path/to/db.sqlite
```

Read-only by default; pass `--read-write` to enable mutations, then immediately add a deny pattern for `*__drop_table` and `*__delete` in `harness.toml`.

## 3. `harness.toml` example

```toml
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/project"]
subagent_access = true   # safe: read-only filesystem ops

[mcp.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = {GITHUB_TOKEN = "ghp_xxx"}  # NOTE: does NOT inherit loom process env
subagent_access = false   # default: subagents can't call, prevents accidental push/PR

[mcp.permissions]
auto_approve = ["filesystem__read_file", "filesystem__list_files"]
deny = ["*__delete", "*__drop_table", "*__rm", "*__push"]
```

Schema per `[mcp.servers.<name>]`:

- `command` *(required)* — binary to run. Resolved via `PATH` lookup unless absolute.
- `args` *(optional)* — list of strings. Default `()`.
- `env` *(optional)* — dict of string→string. **Only** these vars are passed to the server process; the loom process environment is *not* inherited (M11). Default `{}`.
- `cwd` *(optional)* — string. Default `None` (inherits loom's CWD).
- `subagent_access` *(optional)* — bool. Default `false`. See §8.

Schema per `[mcp.permissions]`:

- `auto_approve` *(optional)* — list of `server__tool` patterns that bypass the prompt. Default `[]`.
- `deny` *(optional)* — list of patterns that hard-block (no user override). Default `[]`.

## 4. Permission model

Every `mcp__*` tool call is gated by a **3-state** permission check before it reaches the handler:

1. **deny** — if the tool name matches a `[mcp.permissions] deny` pattern, the call is hard-blocked. No user override. Returns `"Permission denied: MCP tool '<name>' matches deny pattern '<pattern>'"`.
2. **auto_approve** — if the tool matches an `auto_approve` pattern, the call passes silently. No prompt, no log.
3. **neither** — the call falls through to a y/N prompt. The user sees the tool name, the arguments, and `Allow? [y/N]`. Default is `N` (deny).

The order is **deny → auto_approve → prompt**. A tool that appears in both `deny` and `auto_approve` is always blocked (deny wins). There is no fourth "allow by default" path — this is the **M5 invariant**: *every* `mcp__*` call must go through one of these three states.

The patterns use `*` as a single-segment wildcard. Examples:

- `filesystem__read_file` — exact match
- `*__read_file` — any server's `read_file`
- `github__*` — every tool on the `github` server
- `*__*` — every MCP tool (rarely useful; usually you want a narrower pattern)

We deliberately did **not** reuse `PermissionRule` (the existing 2-state allow/deny with a `check: callable` predicate) because the MCP gate has 3 distinct *terminal* states. A boolean allow/deny is not enough; the user must always have the option to be *prompted*. The dedicated `_check_mcp_permissions` method on `Hooks` encodes this with a separate code path so the M5 invariant is structurally enforced, not just by convention.

## 5. Discovery timing

MCP servers are spawned **in the background at session start** — one daemon thread per `[mcp.servers.<name>]` entry. The thread runs the `initialize` handshake + `tools/list` RPC, then registers every tool into `TOOL_REGISTRY` (and, if `subagent_access=True`, into `SUB_TOOLS` / `SUB_HANDLERS` too).

Concretely:

- The agent loop starts immediately and runs the first LLM call against whatever tools are currently registered.
- If an MCP server takes 2s to respond to `initialize`, tools from that server appear 2s *after* the loop has already begun.
- The first user message may have **zero** MCP tools; the third turn may have four; the tenth may have eight. The agent sees them accumulate asynchronously.

This is intentional — loom doesn't block the REPL waiting for every external server to handshake. A slow server (or one that fails to start) never delays the agent. The trade-off is that an early tool call that depends on an MCP tool may need to be retried after the server has registered.

## 6. Limitations

These are explicit non-goals. Knowing them prevents wasted debugging time.

- **Tool names use `mcp__server__tool`** (double-underscore, three segments). This matches the Anthropic prompt-cache namespace format and the Claude Code convention. The leading `mcp__` is stripped before pattern matching so `filesystem__read_file` reads naturally in `harness.toml`.
- **Does NOT inherit the loom process environment.** `os.environ` is not passed to MCP servers (M11) — this is a deliberate defense against accidental API key leakage. If your server needs `PATH`, `HOME`, `NODE_PATH`, or anything else from your shell, declare it explicitly in the server's `env` block.
- **Output capped at 50KB.** A `tools/call` result is flattened (`_flatten_mcp_content`) into a string, then truncated to 50KB + a footer with the overflow count, and a `mcp_output_truncated` trace event is written. A misbehaving server (e.g. dumping a 200MB log file) cannot blow up the agent's context window.
- **Server crash → tools removed, no auto-restart.** When `call_tool` raises, the handler emits a visible `logger.warning`, evicts the server from `_ACTIVE_SERVERS` + `_PER_SERVER_LOCKS`, and unregisters every `mcp__<server>__*` tool from `TOOL_REGISTRY`. The next `task` block will fail fast with `"Unknown tool: mcp__<server>__*"` instead of hanging on a dead stdio pipe. Auto-restart is intentionally NOT attempted mid-session to prevent flapping on a server that crashes on every call. Restart the session to re-discover.
- **Per-server JSON-RPC mutex.** Concurrent calls to the *same* server serialize on a per-server `threading.Lock` because JSON-RPC over a single stdin/stdout has no request multiplexing. Calls to *different* servers run in parallel.
- **Unix-only `file://` URI parsing.** Resource block URIs from MCP servers are assumed to be POSIX paths. Windows paths will round-trip incorrectly. Workaround: run on WSL or use a server that returns POSIX-style paths.
- **No SSE / HTTP transport.** loom implements the stdio transport only. MCP servers that require SSE or HTTP will not work without an adapter.

## 7. Troubleshooting

### `command not found`

The `command` field couldn't be resolved on `$PATH`. Check:

```bash
which npx
echo $PATH
```

If `which` returns empty, your install didn't land on `$PATH` (common for `npm install -g` → `~/.npm-global/bin` and `go install` → `~/go/bin`). Either add the directory to `$PATH` or use the full absolute path in `harness.toml`.

### `tools/list timeout` / slow handshake

The server's `initialize` or `tools/list` response took >30s and loom gave up. Check the server's own logs (`npx` prints to stderr). Common causes:

- Missing runtime (e.g. `server-sqlite` needs Python 3.10+).
- Missing `GITHUB_TOKEN` in the server's `env` block (the GitHub server is silent about this and just hangs).
- A pre-flight script the server runs that takes forever (rare — file an issue).

The full trace is in `.minicode/trace.jsonl` under the `mcp_request` event. `jq` it: `jq 'select(.event == "mcp_request")' .minicode/trace.jsonl`.

### `Permission denied: MCP tool matches deny pattern`

A `[mcp.permissions] deny` pattern matched. Edit `harness.toml` and either remove the pattern or narrow it (e.g. change `*__delete` to `production_db__delete` to allow deletes on a different server). The error message names the offending pattern so you can grep for it.

### SIGKILL orphan servers

If you `kill -9` loom, the `SessionEnd` shutdown hook never fires, so spawned MCP server processes may persist as orphans. They show up in `ps aux | grep mcp-server-*` after loom is gone. To clean up:

```bash
pkill -f mcp-server-filesystem
pkill -f mcp-server-github
pkill -f mcp-server-sqlite
```

This is harmless — the servers just consume a bit of memory until killed. They will not be re-used by the next loom session (loom spawns fresh).

### API key leakage check

loom does **NOT** pass `os.environ` to MCP servers (M11). If you suspect a leak:

1. Open `harness.toml` and inspect every `[mcp.servers.<name>] env = {…}` block. Only explicitly declared vars are forwarded.
2. Run `loom eval` (the audit runs) — it reports any environment pass-through.
3. For belt-and-suspenders, set a dummy `PATH` in the env block: `env = {PATH = "/usr/bin:/bin"}`. The server will not see your real `$PATH`.

### `MCPError: ... server stderr tail: ...`

The server crashed at startup and loom's stderr-collector (M16) captured the last 2000 chars of the server's stderr. Read the tail — it almost always names the missing dependency, missing env var, or config error. The stderr is appended to the `MCPError` message so the operator doesn't have to dig through logs.

### Stale tools after crash

If an MCP server crashes mid-session, its `mcp__<server>__*` tools are unregistered (M6). The agent sees them disappear from the tool list on the next turn. To re-discover, restart the loom session — there is no mid-session auto-restart (see §6).

## 8. `subagent_access` explained

The `subagent_access` field on `[mcp.servers.<name>]` controls whether the server's tools are visible to subagents launched via `spawn_subagent` (the `task` tool's internal call). Default is **`false`** — explicit per-server opt-in is required.

```toml
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/project"]
subagent_access = true   # safe: read-only filesystem ops
```

Why opt-in (not opt-out)?

A `spawn_subagent` call is a fresh loop with no conversation history. The subagent has its own `SUB_TOOLS` list and its own `SUB_HANDLERS` dict. If MCP tools are auto-exposed, a subagent that you launched to "summarize this file" could silently call `mcp__github__push` if the parent agent has that tool — and the parent's `[mcp.permissions]` gate does *not* know the call came from a subagent, so the deny patterns still apply. The defense-in-depth is **default-false**: the operator must explicitly say "yes, this subagent should have access to this tool surface".

**The 3-state permission gate from PM-2 STILL fires for subagent tool calls.** `spawn_subagent` dispatches `PreToolUse` for every tool the subagent calls, and the hook runs the same `_check_mcp_permissions` check. So a subagent that DOES have access to `mcp__github__*` still has to obey the deny patterns and the auto_approve patterns from the parent's `harness.toml`. Subagents cannot bypass the permission gate.

When `subagent_access=True`, the *same* handler instance is shared between `TOOL_REGISTRY` and `SUB_HANDLERS` — no copy-paste, no drift. The PM-2 3-state gate, the PM-3 output-shape mitigations, and the M6 crash recovery all apply identically to both surfaces.

## 9. Design decisions

Brief notes on why loom is shaped this way. Future contributors, please update this section if you reverse any of these.

- **BYO server, not bundled.** Avoids forcing every loom user to download a dozen Node packages just so one user can have filesystem MCP. The cost is "you must read §2 and install one binary". Tradeoff favors BYO for our audience.
- **Config-driven permission, not LLM-decided.** The 3-state gate is enforced in Python (`_check_mcp_permissions`) — the LLM cannot choose to skip a deny pattern. The `auto_approve` list is also operator-defined, not learned from past approvals. This is defense-in-depth: a misbehaving model that tries to call a `deny`-listed tool gets blocked, period.
- **Background discovery, not blocking.** A typical loom session reads files, edits files, and runs tests. Blocking the REPL for 2-3s waiting for every MCP server to handshake would feel sluggish for the 90% of users who never call an MCP tool. Background threads amortize the cost.
- **No `os.environ` inheritance.** A misconfigured `.env` file or accidentally exported `ANTHROPIC_API_KEY` would leak to every spawned MCP server. Forcing explicit `env = {...}` declarations makes the security boundary visible in the config file and reviewable in a PR. If your server needs `PATH` (most do), add it: `env = {PATH = "/usr/bin:/bin"}`.
- **50KB output cap.** 50KB is enough for ~10K tokens — well under any model's per-tool-output budget. Above that, a truncation footer + trace event is more useful than a flood of text the model will have to summarize away anyway.
- **Per-server lock, not global lock.** JSON-RPC over a single stdin/stdout is fundamentally sequential. A global lock would serialize `mcp__filesystem__*` against `mcp__github__*` — wasteful. Per-server locks allow different-server tools to run concurrently.
- **`subagent_access` defaults to false.** Most MCP servers are too privileged (push, drop_table, network write) to expose to a subagent without explicit per-server opt-in. The 3-state permission gate from PM-2 still applies, but defense-in-depth is to require the operator to flip the bit.
- **No mid-session auto-restart.** A server that crashes on every call would flapping — spawn, crash, spawn, crash — consuming CPU. Re-discover on next session is simpler and predictable.

