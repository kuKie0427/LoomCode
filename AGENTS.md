# AGENTS.md

> Routing file for AI coding agents working on the **loop** project. Keep this short (≤ 100 lines). Project-specific details live in `docs/`.

## Project

**loop** — minimal Claude-Code-like coding agent in Python (≈ 300 LOC). Provides an `agent_loop` with tool use, hooks, context compression, and subagent delegation.

## Quick Start

```bash
./init.sh                    # Install deps + run full verification (ruff + mypy + pytest)
uv run pytest -v             # Run tests only
uv run python -m loop.cli run # Start the agent REPL
uv run python -m loop.cli eval               # Run the eval suite (text)
uv run python -m loop.cli eval --html r.html # Run eval + write HTML report
uv run python -m loop.cli eval --benchmark resume # Run resume canary (Phase 5 §6)
uv run python -m loop.cli trace show         # Show recent trace events
uv run python -m loop.cli audit              # Audit this project's harness
uv run python -m loop.cli init .             # Scaffold harness in target dir
```

## Layout

| Path | Purpose |
|---|---|
| `loop/agent/loop.py` | `agent_loop()`, message flow, checkpoint, trace |
| `loop/agent/context.py` | Token estimation, `microcompact`, `autocompact` |
| `loop/agent/hooks.py` | `Hooks` registry, deny list, permission rules |
| `loop/agent/llm.py` | `LLMClient` (Anthropic SDK wrapper) |
| `loop/agent/prompt.py` | `SystemPrompt` (static + dynamic, cache boundary) |
| `loop/agent/tools.py` | `run_*` tool handlers, `spawn_subagent` |
| `loop/agent/trace.py` | Append-only JSONL trace (`.minicode/trace.jsonl`) |
| `loop/agent/checkpoint.py` | Atomic save of conversation state |
| `loop/eval/` | EvalCase framework + 32 cases (init/audit/detect/memory/skills/integration) |
| `loop/cli.py` | Argparse + subcommand routing |
| `tests/` | pytest suite (225 tests) |
| `harness-creator/` | Reference skill (gitignored, do NOT commit) |

Topic docs (load **on demand**, never pre-load):

- `docs/architecture.md` — agent loop, message flow, hooks
- `docs/tools.md` — tool definitions and safety guarantees
- `docs/hooks.md` — PreToolUse / PostToolUse / AgentStart / AgentStop
- `docs/context.md` — microcompact vs autocompact, thresholds, constants
- `docs/testing.md` — fixtures, mocking LLM calls, adding tests
- `docs/tui-scrolling.md` — TUI mouse-wheel scroll: why the three-layer fix, debugging history, what each test guards
- `docs/harness-roadmap.md` — strategic roadmap for the 5-phase harness plan

## Working Rules

1. **WIP=1 (one feature at a time)**: Work on exactly one feature from `feature_list.json` at a time.
2. **Verification required**: A feature is `done` only after `./init.sh` exits 0.
3. **Real evidence only**: The `evidence` field must be a real command + output snippet. Never "looks correct".
4. **Update artifacts**: After every change, update `feature_list.json`. Append a section to `progress.md` at end of session.
5. **Stay in scope**: Don't modify files unrelated to the active feature.
6. **No self-declared passing**: Agents may mark `not-started → in-progress → blocked`. Only `./init.sh` (or its sub-commands) marks `done`.
7. **Review→Rule**: When a review or post-mortem surfaces a recurring failure pattern, promote it to a numbered Working Rule here AND, when cheap, encode it as an eval case under `loop/eval/cases/`. One-off fixes don't compound; rules do.
8. **Eval cases are first-class tests**: New features that change observable behavior should ship with at least one eval case in `loop/eval/cases/`. The eval runner is the regression net for product behavior; pytest is for unit correctness.
9. **Monkey-patches need explicit import wiring**: A `monkey-patch = X` assignment in a module body fires **only when that module is imported**. If a patch file (e.g. `loop/tui/kitty_patch.py`) patches a class but is never imported from the application entry point, the patch is dead code — unit tests pass (because they import the patch module directly) but production behavior is unchanged. Verify with a startup print or `pid` log written from the patch module itself. The fix is one `import` line, but the diagnosis can take hours.
10. **Snapshot tests can be flaky due to randomized CSS class hash IDs** in the SVG output (e.g. `terminal-2345353881-matrix` vs `terminal-3916823283-matrix` per Python run). Before assuming a snapshot regression, extract `<text>` segments and normalize random IDs (`terminal-X`); if content matches, it's just the hash, not a real visual change. If only IDs differ, re-baseline with `pytest --snapshot-update`. Real visual regressions show different text content (missing/added characters or rows), not just ID changes.
11. **Verify subagent work after timeouts**: a subagent that reports "done" after a long timeout (especially 30min+ on a small task) likely did something other than the requested work. Run `git status --short` + targeted grep on the actual task scope. Common failure modes: (a) re-applied existing uncommitted work without doing new work, (b) modified out-of-scope files, (c) deleted/created unexpected files. Always `git diff --stat` first; revert anything outside the declared scope before evaluating the actual changes.
12. **Textual mouse-event routing has three gotchas that look like the same bug**: (1) the App is NOT in the widget parent chain, so `App.on_mouse_scroll_*` is dead code for real driver input — only `App.on_event` fires; (2) `Widget.scroll_to(animate=False)` schedules a single repaint via `check_idle()` that can be deferred past the user's wheel release in real terminals; (3) tests that call `widget.post_message(event)` directly bypass the driver path, so they pass even when the real-driver path is broken. Any "scroll doesn't work in my terminal" bug must add a test that exercises `App.on_event` AND verifies `Update`/`UpdateScroll` messages are posted to the screen — see `docs/tui-scrolling.md` for the full history.
13. **textual.widgets.Markdown's `gfm-like` parser turns file names into bogus URLs**: it enables `linkify-it`, which matches `domain.tld` against the public-suffix list. Any file name whose extension is a real TLD (`conftest.py`, `setup.sh`, `README.md`, `a.py`, anything ending in `.dev`, `.io`, `.ai`, etc.) is silently re-rendered as `http://conftest.py` and becomes clickable — clicking opens the default browser at a non-existent domain. Fix: pass a `parser_factory` to every `Markdown` subclass that builds `MarkdownIt("gfm-like")` and sets `parser.options["linkify"] = False`. The chat log does this in `_markdown_parser_factory` and threads it through `UserMessage`, `AssistantMessage`, `StreamingOverlay`, `ThinkingDisplay`, and the `CollapsibleToolOutput` / `ToolCallModal` Markdown instances. `tests/test_markdown_linkify.py` guards the factory, the wiring, and the end-to-end "no link" invariant.
14. **TUI thinking display only updates for the first LLM call in a session, not subsequent ones** (after tool_use rounds). The agent loop fires `on_message_start` exactly **once** per `agent_loop()` invocation (i.e. once per user turn), but a single user turn can contain many LLM calls — the first LLM call's thinking was set up correctly, but the 2nd, 3rd, ... Nth LLM call's thinking was silently appended to the now-hidden `ThinkingDisplay` from the first round, so the user never saw it. Fix: add a new `on_assistant_message_start` callback that fires **before each LLM call** within the agent loop's while loop. The TUI wires it to the same `AssistantTurnStart` → `show_thinking_spinner` flow that resets `_thinking_reasoning` and creates a fresh `ThinkingDisplay`. The existing `on_message_start` keeps its once-per-session semantic (still guarded by the eval case in `loop/eval/cases/async_streaming.py`); the new `on_assistant_message_start` is per-LLM-call. `tests/test_thinking_per_llm_call.py` guards the callback wiring and the per-round fresh-display invariant.

## Definition of Done

A feature is done ONLY when ALL of the following hold:

- [ ] Target behavior is implemented
- [ ] `./init.sh` exits 0 with the feature's `verification` command green
- [ ] `feature_list.json` updated: `status: "done"` + `evidence: "real command + output"`
- [ ] No unrelated files modified
- [ ] Working tree clean (or only intended changes)

## End-of-Session Checklist

1. Run `./init.sh` — must exit 0
2. Run `loop eval` if any eval case was touched — must be 32/32 (or more)
3. Update `feature_list.json` for the active feature (status, evidence, blocker)
4. Append a section to `progress.md` (what was done, blockers, next step)
5. If multi-session: write `session-handoff.md`
6. Commit with a message naming the feature (e.g. `feat: f-observability structured trace + eval runner`)

## Verification Commands

```bash
# Full verification (recommended — what ./init.sh runs)
./init.sh

# Layer 1 — static
uv run ruff check .
uv run mypy loop/

# Layer 2 — unit + integration tests
uv run pytest -v

# Layer 3 — eval suite (product behavior)
uv run python -m loop.cli eval --fail-under 100

# Layer 4 — smoke (manual)
uv run python -m loop.cli run    # type a question, expect streamed response
```

## Escalation

- **Architecture decision**: Read `docs/architecture.md` first. If still unclear, ask user.
- **Tool behavior unclear**: Read `docs/tools.md` + the `run_*` functions in `loop/agent/tools.py`.
- **Hook ordering / events**: Read `docs/hooks.md` and `loop/agent/hooks.py`.
- **Compression heuristics**: Read `docs/context.md` and the constants in `loop/agent/context.py`.
- **Trace schema or eval case design**: Read `loop/agent/trace.py` and `loop/eval/runner.py` before adding cases.
- **Test failure repeats 3+ times**: Update `progress.md` with diagnosis, mark feature `blocked`, ask user.
- **New verification command needed**: Choose from existing patterns in `feature_list.json` first; only add a new command if none fit.
- **Recurring reviewer finding**: Promote to a Working Rule here AND/OR add an eval case — don't just patch and move on.