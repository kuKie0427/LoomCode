# AGENTS.md

> Routing file for AI coding agents working on the **loop** project. Keep this short (≤ 100 lines). Project-specific details live in `docs/`.

## Project

**loop** — minimal Claude-Code-like coding agent in Python (≈ 300 LOC). Provides an `agent_loop` with tool use, hooks, context compression, and subagent delegation.

## Quick Start

```bash
./init.sh                    # Install deps + run full verification (pytest + ruff + mypy)
uv run pytest -v             # Run tests only
uv run python main.py        # Start the agent REPL
```

## Layout

| File | Purpose |
|---|---|
| `main.py` | `agent_loop()`, tool definitions, REPL entry point |
| `context.py` | Token estimation, `microcompact`, `autocompact` |
| `hook.py` | `Hooks` registry, deny list, permission rules, user approval |
| `models.py` | `LLMClient` (Anthropic SDK wrapper) |
| `prompt.py` | `SystemPrompt` (static + dynamic with cache boundary) |
| `tests/` | pytest suite (71 tests across 6 files) |
| `harness-creator/` | Reference skill (do NOT commit; gitignored) |

Topic docs (load **on demand**, never pre-load):

- `docs/architecture.md` — agent loop, message flow, hooks
- `docs/tools.md` — tool definitions and safety guarantees
- `docs/hooks.md` — PreToolUse / PostToolUse / AgentStart / AgentStop
- `docs/context.md` — microcompact vs autocompact, thresholds, constants
- `docs/testing.md` — fixtures, mocking LLM calls, adding tests
- `docs/harness-roadmap.md` — strategic roadmap for the 5-phase harness plan

## Working Rules

1. **WIP=1**: Work on exactly one feature from `feature_list.json` at a time.
2. **Verification required**: A feature is `done` only after `./init.sh` exits 0.
3. **Real evidence only**: The `evidence` field must be a real command + output snippet. Never "looks correct".
4. **Update artifacts**: After every change, update `feature_list.json`. Append a section to `progress.md` at end of session.
5. **Stay in scope**: Don't modify files unrelated to the active feature.
6. **No self-declared passing**: Agents may mark `not-started → in-progress → blocked`. Only `./init.sh` (or its sub-commands) marks `done`.

## Definition of Done

A feature is done ONLY when ALL of the following hold:

- [ ] Target behavior is implemented
- [ ] `./init.sh` exits 0 with the feature's `verification` command green
- [ ] `feature_list.json` updated: `status: "done"` + `evidence: "real command + output"`
- [ ] No unrelated files modified
- [ ] Working tree clean (or only intended changes)

## End-of-Session Checklist

1. Run `./init.sh` — must exit 0
2. Update `feature_list.json` for the active feature (status, evidence, blocker)
3. Append a section to `progress.md` (what was done, blockers, next step)
4. If multi-session: write `session-handoff.md`
5. Commit with a message naming the feature (e.g. `feat: f-test-framework-p4 compress failure path`)

## Verification Commands

```bash
# Full verification (recommended — what ./init.sh runs)
./init.sh

# Layer 1 — static
uv run ruff check .
uv run mypy main.py context.py hook.py models.py prompt.py

# Layer 2 — unit + integration tests
uv run pytest -v

# Layer 3 — smoke (manual)
uv run python main.py    # type a question, expect streamed response
```

## Escalation

- **Architecture decision**: Read `docs/architecture.md` first. If still unclear, ask user.
- **Tool behavior unclear**: Read `docs/tools.md` + the `run_*` functions in `main.py`.
- **Hook ordering / events**: Read `docs/hooks.md` and `hook.py`.
- **Compression heuristics**: Read `docs/context.md` and the constants in `context.py` (`KEEP_RECENT`, `THRESHOLD`, etc.).
- **Test failure repeats 3+ times**: Update `progress.md` with diagnosis, mark feature `blocked`, ask user.
- **New verification command needed**: Choose from existing patterns in `feature_list.json` first; only add a new command if none fit.
