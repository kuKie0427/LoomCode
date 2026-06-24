<p align="center">
  <img src="docs/loom-mark.svg" alt="loom" width="48" height="48">
  <br>
  <em>loom</em>
</p>

<h3 align="center">weaving intent into action</h3>

<p align="center">
  A verifiably reliable coding agent in Python. Wires user intent, tool calls, and model responses into a long loop: message → thinking → tools → response → repeat. Every behavior is locked by a deterministic eval case (harness + agent-quality). Real LLM calls exercise the agent end-to-end on real coding tasks; the result is auditable.
</p>

<h3 align="center">Quick Start</h3>

```bash
uv run python -m loom.cli run      # start the REPL
uv run python -m loom.cli tui      # launch the Textual TUI
uv run python -m loom.cli auth login anthropic   # one-time API key setup
uv run python -m loom.cli models                # list all providers × models
uv run python -m loom.cli eval     # run the full eval suite
uv run python -m loom.cli --help   # see all commands
```

<h3 align="center">What loom does well</h3>

- **Native tools** (read, write, edit with fuzzy + multi-edit + line-range, bash with 32-pattern deny list, glob, grep with ripgrep fallback, web_fetch with HTML extraction, multi-tier memory, subagent delegation, structured trace + 3 LSP tools (goto_definition / find_references / rename_symbol with 2-pass permission gate and journal-backed rollback))
- **6 LLM providers** (Anthropic / OpenAI / DeepSeek / Ollama / OpenRouter + OpenAI-compatible) + `/model` slash command + `loom run --model` CLI flag + secure API key storage in `~/.loom/auth.json` (chmod 600, OS keyring support)
- **Harness engineering** (PreToolUse permission gates, PreCompact/PostCompact hooks, two-tier compaction with raw-truncate fallback, max-turns guard, lazy-cached system prompt, tool-error retry detection, Aider-style stdlib-ast repomap)
- **Two-tier init.sh** (`./init.sh` full / `./init.sh quick` / `scripts/verify-quick.sh`): generated harness ships with dev-cycle fast path (~10s) + closeout full path, matching loom's own Working Rule #17. Auto-detects Python ruff/mypy; injects pytest `slow`/`snapshot` markers if missing.
- **Eval-driven** (300+ harness eval cases + 13 agent-quality cases; 750+ pytest tests; pre-fix bugs promoted to regression tests)
- **Production-ready** (prompt caching, per-turn + cumulative cost telemetry, /export markdown+json with PII redaction, permission persistence with TTL, stdio MCP client with config-driven 3-state permission gate (deny/auto_approve/prompt) and per-server subagent opt-in, generic LSP client with goto_definition / find_references / rename_symbol, cold_archive + cold_load for long-context stability, `loom eval --baseline` regression detection, `loom tdd` test-driven fix workflow, GitHub Action template)

<h3 align="center">What loom does NOT do well (yet)</h3>

- **Hard search bugs**: agent still falls back to bash `cat` when grep is unavailable for a known multi-file task
- **TDD mode is scaffolding only**: `loom tdd <test>` runs pytest and prints a focused prompt, but the agent itself doesn't auto-iterate; the seam is in `loom/agent/tdd.py`
- **Cold archive is storage only**: `cold_archive` + `cold_load` tools work, but no automatic eviction at the agent-loop layer yet
- **Long-context cold storage**: no >1M token sessions with archived rounds

See `feature_list_roadmap.json` for the full Phase 1-4 plan.

<h3 align="center">Size</h3>

| Component | LOC |
|---|---|
| `loom/agent/` (core) | ~5,000 |
| `loom/eval/` (framework + cases) | ~9,000 |
| `loom/tui/` (Textual UI) | ~2,800 |
| `tests/` (pytest) | ~7,500 |
| **Total** | **~17,000** |

<h3 align="center">Verification</h3>

```bash
uv run pytest -m 'not snapshot' -q   # 760+ tests, fast
uv run python -m loom.cli auth login anthropic   # set up an API key
uv run python -m loom.cli models                # verify provider list
uv run python -m loom.cli eval      # full eval suite
./init.sh                          # complete gate (longer, ~2min)
```

<p align="center">
  Setup, working rules, and architecture: <a href="./AGENTS.md">AGENTS.md</a>. Phase plan: <a href="./feature_list_roadmap.json">feature_list_roadmap.json</a>.
</p>
