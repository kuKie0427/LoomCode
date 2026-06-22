# LSP (Language Server Protocol) Integration

loom ships a generic LSP client that lets the agent use any standards-compliant language server as a tool. Three tools are exposed to both the main agent and subagents: `lsp_goto_definition`, `lsp_find_references`, and `lsp_rename_symbol`. The implementation lives in `loom/agent/lsp_client.py`, `loom/agent/lsp_manager.py`, and `loom/agent/lsp_apply.py`.

## 1. What & Why

The **Language Server Protocol** is a JSON-RPC standard originally designed by Microsoft for editor tooling (LSP spec lives at <https://microsoft.github.io/language-server-protocol/>). A *language server* is a long-running process that watches source files in a project and answers structured queries — "where is this symbol defined?", "who calls this function?", "rename this identifier across the workspace". Most languages have at least one well-maintained server:

- Python → `python-lsp-server` (a.k.a. pylsp)
- TypeScript / JavaScript → `typescript-language-server`
- Go → `gopls`
- Rust → `rust-analyzer`
- Rust → `rust-analyzer`
- Ruby → `solargraph`
- etc.

loom's **BYO (Bring Your Own) server** philosophy: we don't bundle language runtimes. You install the servers you care about, point `harness.toml` at the binaries, and loom spawns them lazily on first use. This keeps the loom install lean (no Python 3.12 + Node 20 + Go 1.22 all shipping just so one user can have TypeScript autocomplete).

loom wraps the raw JSON-RPC layer in three thin tools and gives them the same `safe_path`, trace-event, and per-tool permission gates that the file tools get. There is no proprietary code generation or symbol DB — when you ask "where is `hello` defined?", loom literally sends `textDocument/definition` to your LSP server and formats the response.

## 2. Install hints

Pick whichever servers match the languages in your workspace. The agent will only spawn servers that match a file extension you configured, so installing extras has no runtime cost.

### Python

```bash
pip install 'python-lsp-server[all]'
# Verify:
which pylsp
# /Users/you/.local/bin/pylsp  (or wherever pip installs it)
```

The `[all]` extra pulls in rope (refactor), pyflakes + flake8 (lint), pylint (deep analysis), yapf + autopep8 (format). If you only want goto/references, `pip install python-lsp-server` is enough.

### TypeScript / JavaScript

```bash
# Option A — global install
npm install -g typescript-language-server typescript

# Option B — zero-install via npx (slow first call, then cached)
# harness.toml uses: command = "npx", args = ["typescript-language-server", "--stdio"]
```

Option B is recommended for CI / ephemeral environments because it avoids polluting global state.

### Go

```bash
go install golang.org/x/tools/gopls@latest
# Verify:
which gopls
# ~/go/bin/gopls
```

`$GOPATH/bin` must be in your `$PATH` for loom's `shutil.which(command)` lookup to find `gopls`.

## 3. `harness.toml` example

Drop this at the root of your workspace (or in `.loom/harness.toml`). Each `[lsp.<name>]` table declares one server; `<name>` is arbitrary and only used for trace events.

```toml
[lsp.python]
command = "pylsp"
extensions = [".py"]

[lsp.typescript]
command = "npx"
args = ["typescript-language-server", "--stdio"]
extensions = [".ts", ".tsx", ".js", ".jsx"]

[lsp.go]
command = "gopls"
extensions = [".go"]
```

Rules of the road:

- `command` is required; resolved via `shutil.which` against `$PATH`.
- `args` is optional; defaults to `()`.
- `extensions` is required and is the routing key — loom looks up the server by file suffix. List every extension your server handles; missing extensions = "No LSP server configured for .ts files".
- Multiple servers may claim the same extension (rare). First match wins.

## 4. Usage patterns

### The standard 2-step: `grep` → `lsp_goto_definition`

LSP servers expect a *position* (`file:line:character`), not a *name*. loom deliberately does NOT ship a high-level `find_symbol_by_name` tool — the agent is expected to do the conversion itself:

```text
# Step 1 — find the candidate position by name
grep(pattern="def hello", path="src/")

# Step 2 — given the result "src/main.py:4:5" (1-indexed from grep),
#          call lsp_goto_definition with line=3, character=4 (0-indexed for LSP)
lsp_goto_definition(path="src/main.py", line=3, character=4)
```

The `description` on every LSP tool says **"Use AFTER grep to find the candidate position"** for exactly this reason — agents that try to call `lsp_goto_definition` with a name get confused; the explicit hint nudges them toward the 2-step pattern.

### R6: silent 1-indexed → 0-indexed correction

In practice, agents (and humans) frequently pass a `line` from `grep` output directly to an LSP tool, even though `grep` is 1-indexed and LSP is 0-indexed. The leading underscore on `_coerce_lsp_line` is a tell: PL-1 introduced it as an internal helper that detects the common confusion pattern (line is past end-of-file) and auto-decrements by 1. So `line=10` on a 10-line file silently becomes `line=9`. This is intentionally hidden from the agent — surfacing it would force every call site to subtract 1 by hand and invite new bugs. If you ever wonder why your LSP call returns a "no definition found" for a line you can clearly see in your editor, R6 is probably the reason; pass `line-1` to disable the auto-correction.

### `lsp_find_references` with `include_declaration`

By default the tool returns both the declaration and every reference (the LSP spec default is `false` for `includeDeclaration`; loom overrides to `true` because for refactor planning you almost always want both). Pass `include_declaration=false` if you want a pure call-site list.

### `lsp_rename_symbol` — destructive, gated twice

`lsp_rename_symbol` is the only LSP tool that writes to disk. It runs through two permission gates (see `docs/hooks.md` for the generic pipeline):

1. **PreToolUse** — automatic, checks the *entry* `path` is inside the workspace.
2. **Post-LSP** — manual inside the handler, after the LSP server has returned the full `WorkspaceEdit`. Every file the rename will touch is checked against the workspace boundary. The handler invokes `DEFAULT_POLICY.find_rule` directly — no second hook trigger — to reuse the same rule without duplication.

If any file in the plan resolves outside `WORKDIR`, the rename is **blocked** with `"Rename blocked by permission policy"` and no files are touched.

## 5. Limitations

These are explicit non-goals for loom's LSP integration. Knowing them prevents wasted debugging time.

- **0-indexed `line` / `character`** — LSP spec. loom's `_coerce_lsp_line` mitigates the common 1-indexed confusion but cannot fix every case.
- **`documentChanges` format `WorkspaceEdit` is NOT supported** — only `changes` (the older, file-by-file map) format. Servers that return `documentChanges` (e.g. newer rust-analyzer) will fail with `LSP error: unsupported WorkspaceEdit format`. Workaround: server-side flag to force `changes` only, or accept the limitation for that language.
- **Lazy spawn** — server processes start on the first call to a matching file, NOT on session start. Most sessions never use LSP, so we save the startup cost.
- **`SessionEnd` shutdown** — `SessionEnd` hook (PL-2) calls `lsp_manager.shutdown_all()` which sends `shutdown` + `exit` JSON-RPC notifications. If the session is killed before `SessionEnd` fires (SIGKILL, OOM, Ctrl-C in a tail of a pipe), the server processes may persist — see Troubleshooting § R4.
- **Per-server `threading.Lock`** — concurrent calls to the *same* server serialize on a per-server lock (JSON-RPC has no request multiplexing on a single stdin/stdout). Calls to *different* servers run in parallel.
- **Unix-only `file://` URI parsing** — R8 regression: `pathlib.Path("file:///C:/Users/x").name` returns the literal string `file:/C:/Users/x` on POSIX, which is unparseable. loom assumes POSIX paths in `WorkspaceEdit`. On Windows, paths will round-trip incorrectly. Workaround: run on WSL or use a server that returns POSIX-style paths.

## 6. Troubleshooting

### "Server not found"

`shutil.which(command)` returned `None`. Check:

```bash
which pylsp    # or gopls, or typescript-language-server
echo $PATH
```

If `which` returns empty, your install didn't land on `$PATH` (common for `go install` → `~/go/bin` and `npm install -g` → `~/.npm-global/bin`). Either add the directory to `$PATH` or use the full absolute path in `harness.toml`.

### "Startup failure"

The server process spawned but exited before responding. loom raises `LSPError` with the captured stderr. Typical causes:

- Missing runtime (e.g. `gopls` requires Go 1.20+).
- Conflicting config in `$XDG_CONFIG_HOME/<server>/` — try `mv $XDG_CONFIG_HOME/<server> $XDG_CONFIG_HOME/<server>.bak`.
- The server wants initialization options loom doesn't pass (rare — please file an issue).

The full trace is in `.minicode/trace.jsonl` under the `lsp_request` event. Pipe it through `jq` to see the JSON-RPC exchange.

### "Timeout"

Default 30 seconds per LSP request (`LSP_REQUEST_TIMEOUT_S`). If your server is genuinely slow (large Rust workspace first call), bump it in `loom/agent/lsp_client.py`. Don't increase it globally — a hang is more recoverable than a 10-minute wait.

### "Permission denied" on `lsp_rename_symbol`

The `WorkspaceEdit` the server returned touches a file outside `WORKDIR` (e.g. a generated file symlinked to `/tmp`, or a language server that mistakenly includes `/dev/null`). loom's `_lsp_rename_outside_workspace` check blocks this. To debug, run `lsp_rename_symbol` with the same `path/line/character/new_name` and inspect the LSP server's full response in trace.

### R4 — SIGKILL cleanup leaves orphan LSP servers

If you `kill -9` the loom process, the `SessionEnd` shutdown hook never fires, so spawned language servers may persist as orphans. They show up in `ps aux | grep -E 'pylsp|typescript-language-server|gopls'` after loom is gone. To clean up:

```bash
pkill -f pylsp                      # or whatever server you spawned
pkill -f typescript-language-server
pkill -f gopls
```

This is harmless — the servers just consume a bit of memory until killed.

### R2 — stale `/tmp/loom-lsp-rollback-<PID>.json` journals

When a rename begins writing files, loom writes a small journal to `/tmp/loom-lsp-rollback-<PID>.json` listing the files-to-write. If loom crashes mid-rename, the journal is left behind. On the **next** `SessionStart`, the `recover_stale_journals` hook fires and logs a warning:

```text
WARN — Recovered stale LSP rename journal: /tmp/loom-lsp-rollback-12345.json
       Files may be in an inconsistent state. Inspect with `git diff`.
```

loom does **NOT** auto-restore. The journal is a hint, not a transaction log; you decide whether to `git checkout -- <file>` or `git restore <file>`. The journal is safe to delete once you've decided.

### "LSP unavailable" vs "No LSP server configured"

Two distinct fail-closed strings, returned from the same handler:

- `"LSP unavailable: <exc>"` — the manager was reached but `get_or_start` raised (server binary missing from `$PATH`, server crashed during startup, etc.). The exception is included.
- `"No LSP server configured for .<ext> files"` — the manager ran cleanly and returned `None`. Either `[lsp]` is missing from `harness.toml` or no server's `extensions` list matches the file. This is *not* an error — it just means LSP isn't set up for this language and the agent should fall back to `grep` + `read_file`.

If you see the second one and expected a server to handle it, add the extension to the appropriate `[lsp.<name>]` table.

### Trace inspection

Every LSP call records a structured event in `.minicode/trace.jsonl`:

```bash
jq 'select(.event == "lsp_request")' .minicode/trace.jsonl
```

The fields are `server` (e.g. `pylsp`), `method` (e.g. `textDocument/definition`), and `duration_ms`. If a call hangs, the duration_ms field tells you exactly how long the server took to give up.

## 7. Design decisions

Brief notes on why loom is shaped this way. Future contributors, please update this section if you reverse any of these.

- **BYO server, not bundled.** Avoids forcing every loom user to download Python 3 + Node + Go + Rust toolchains. The cost is "you must read §2 and install one binary". Tradeoff favors BYO for our audience.
- **Lazy spawn, not session-start.** A typical loom session reads files, edits files, and runs tests — none of which need an LSP server. Spawning eagerly would add 1-3 seconds to every session for a tool most sessions never use.
- **Per-server lock, not global lock.** JSON-RPC over a single stdin/stdout is fundamentally sequential. A global lock would also serialize `python-lsp-server` against `gopls`, which is wasteful. Per-server locks allow different-language tools to run concurrently.
- **2-pass permission on `lsp_rename_symbol`.** Defense-in-depth. The PreToolUse hook only sees the entry path; the post-LSP pass sees the *expanded* file list. A buggy or malicious LSP server that returns a `WorkspaceEdit` touching `/etc/passwd` is caught by the second pass even though the first pass would have approved. PL-3 promoted this from "nice-to-have" to "mandatory" after R3 (a fake-permission-block bug shipped in PL-1).
- **Journal, not full-content backup.** A pre-rename snapshot of every file in `WorkspaceEdit` would be unbounded (a Go rename can touch hundreds of files). A journal of "files-to-write" + `os.replace` (atomic swap) is enough to roll back within a single rename. Cross-session recovery uses git, not loom — git is the real source of truth for "what did the workspace look like before".
- **Thin tools, no `find_symbol_by_name` wrapper.** Considered a high-level tool that takes a symbol *name* and internally does the grep → position → LSP dance. Rejected: it would (a) hide the position semantics from the agent and make debugging impossible, (b) duplicate `grep`'s existing capability, and (c) require the agent to pass an additional search-path/glob hint to disambiguate. The current 2-step pattern is verbose but each step is inspectable, and the LSP tool's description explicitly nudges toward it.
- **Subagent access granted in PL-4 (no separate prompt).** Considered giving subagents a more restricted LSP surface (e.g. read-only tools only). Rejected because (a) `task_refactor_across_files` legitimately needs `lsp_rename_symbol` to do its job, (b) the 2-pass permission gate already protects the filesystem, (c) hiding tools from subagents fragments the agent's mental model of "what I can do". The three LSP tools are in `SUB_TOOLS` next to `grep` / `read_file` / `edit_file` — same handlers, same trace events, same error semantics.
