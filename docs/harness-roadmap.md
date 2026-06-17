# Harness Engineering Roadmap — loop product

> Strategic document. Last updated: 2026-06-17.
> Reference skill: `harness-creator` (do not commit; gitignored).
> Current state: see `feature_list.json`. Latest session: `progress.md`.

---

## 1. Vision & Philosophy

**Model is smart; harness makes it reliable.** Anthropic's experiment: same Opus 4.5 model, same prompt ("build a 2D game editor"), 20 minutes vs 6 hours, non-functional vs playable. Same model, different harness.

This roadmap evolves the **loop** product from "working agent" (executable scaffold: `main.py` / `context.py` / `hook.py` / `subagent` / tests) into a **harness-engineered coding agent product** that ships the same five-subsystem guarantees to its end users.

## 2. The Harness-Creator Five-Subsystem Framework

| Subsystem | Minimal artifact | loop's status |
|---|---|---|
| **Instructions** | `AGENTS.md` + topic `docs/` (progressive disclosure) | ✅ done (Phase 0) |
| **State** | `feature_list.json` + `progress.md` | ✅ done (Phase 0) |
| **Verification** | `init.sh` + test + lint + type-check | ✅ done (Phase 0) |
| **Scope** | WIP=1 + dependency graph + DoD | ✅ done (Phase 0) |
| **Lifecycle** | `session-handoff.md` + end-of-session routine | ✅ done (Phase 0) |

**Extended layers** (built on top, planned for Phases 1–5):

| Layer | Reference | Planned |
|---|---|---|
| Memory persistence | `references/memory-persistence-pattern.md` | Phase 2 |
| Context engineering (3-tier loading) | `references/context-engineering-pattern.md` | Phase 2 |
| Skill runtime | `references/skill-runtime-pattern.md` | Phase 3 |
| Tool registry & safety | `references/tool-registry-pattern.md` | Phase 3 |
| Multi-agent coordination | `references/multi-agent-pattern.md` | Phase 4 |
| Lifecycle & bootstrap | `references/lifecycle-bootstrap-pattern.md` | Phase 4 |
| Observability & eval | (built from scratch, modeled on `evals/evals.json`) | Phase 5 |

## 3. Dual-Purpose Strategy

The roadmap serves **two purposes simultaneously**:

1. **Internal dogfooding** — `loop`'s own development uses the harness. Every commit must pass `./init.sh`. The product is its own first customer.
2. **Product features** — `loop` ships commands and libraries so that end users (developers using the agent) get the same five-subsystem guarantees in their own projects.

The two purposes share `init.sh` and `feature_list.json` schemas. They diverge on packaging: internal artifacts stay in the repo root; product features are exposed as `loop` subcommands (`loop init`, `loop audit`, `loop eval`).

## 4. Roadmap: Six Phases

### Phase 0 — Minimal Harness Self-Sufficiency ✅ DONE

> Goal: Make `loop`'s own development conform to the harness-creator minimum. Done.

**Deliverables**:

- `AGENTS.md` — 88-line routing file (≤100 line budget)
- `init.sh` — verification runner with smart pass-gate
- `progress.md` — session log (now populated with this phase)
- `session-handoff.md` — cross-session handoff template
- `docs/architecture.md` / `tools.md` / `hooks.md` / `context.md` / `testing.md` — 5 topic docs
- `feature_list.schema.json` — strict schema with `blocked` status and `evidence` field
- `feature_list.json` — migrated 5 test-framework features + 7 new roadmap features (12 total)
- `pyproject.toml` — added `dev` extras (`ruff`, `mypy`)
- `.gitignore` — added `harness-creator/` and tool caches

**Acceptance**: `./init.sh` exits 0 (smart pass-gate tolerates the `f-test-framework-p4` blocker, fails on real regressions). A new session reading only `AGENTS.md` + `feature_list.json` can answer the five cold-start questions in < 3 minutes.

**Known leftovers** (intentional, surfaced by the harness):

1. `f-test-framework-p4` is `blocked` (pre-existing test failure; documented in `blocker` field; not a Phase 0 scope item).
2. Six `mypy` error codes are suppressed via `disable_error_code` (`var-annotated`, `operator`, `return-value`, `name-defined`, `typeddict-item`, `attr-defined`). These come from `loop`'s dynamic typing (HOOKS dict, `CURRENT_TODOS` global, handler dispatch). Tightening these is a future feature, not Phase 0 work.

### Phase 1 — `loop init` + `loop audit` Commands (2–3 weeks)

> Goal: Let end users run `loop init <path>` to generate a minimum harness in their own project. The Python port of `harness-creator/scripts/create-harness.mjs` and `validate-harness.mjs`.

**Deliverables**:

1. **`loop init <path>`** — Python rewrite of `create-harness.mjs`
   - Stack auto-detection (Python / Node / Go / Rust / Maven / Gradle / .NET)
   - Generates the 5-file minimum in the target project
   - `--agent-file CLAUDE.md` for Claude-oriented projects
   - `--commands "cmd1,cmd2"` to override detected verification
   - `--force` to overwrite (explicit user opt-in)
2. **`loop audit <path>`** — Python rewrite of `validate-harness.mjs`
   - Scores each of the 5 subsystems (0–100)
   - `--json` and `--html <file>` outputs
   - Identifies the lowest-scoring subsystem as the candidate bottleneck
3. **Permission pipeline generalization** — the current hardcoded `DENY_LIST` / `PERMISSION_RULES` / `_ask_user` in `hook.py` become configurable from a `harness.toml` in the target project.

**Acceptance**: In an empty Python project, `loop init` produces a 5-file harness that a brand-new agent session can take over and complete a feature in (validated by cold-start test). `loop audit` correctly identifies the lowest-scoring subsystem on a sample of 5 reference projects.

**Reference**: `harness-creator/scripts/create-harness.mjs`, `harness-creator/scripts/validate-harness.mjs`, `harness-creator/scripts/lib/harness-utils.mjs`.

### Phase 2 — Memory Persistence + Three-Tier Context (2–3 weeks)

> Goal: Cross-session continuity. A second session can pick up where the first left off. Three-tier context loading keeps Tier 1+2 under ~2500 tokens.

**Deliverables**:

1. **`.minicode/memory/` directory** (not project root; not in git by default)
   - `MEMORY.md` — long-term cross-session memory (user preferences, project conventions, past mistakes)
   - `<session-id>.jsonl` — per-session event log
2. **Three-tier context loading**
   - **Tier 1 (always, ~500 tokens)**: feature list, memory index, current session status
   - **Tier 2 (on activation, ~2000 tokens)**: `AGENTS.md`, relevant skill bodies, style guides
   - **Tier 3 (on demand, unlimited)**: topic docs (`docs/architecture.md`, etc.) loaded via `read_file`
3. **Three-segment `SystemPrompt` upgrade** (extends existing `prompt.py`)
   - Static segment (cacheable): product identity, behavioral rules
   - Session segment (cacheable): workdir, git context (already exists)
   - Memory segment (rebuilt each turn): relevant memory slice
4. **`memory_read` / `memory_write` / `memory_search` tools** — explicit memory management
5. **`autocompact` upgrade** — mark `MEMORY.md` content as "must preserve" during compaction

**Acceptance**: Kill the agent mid-task, restart it, and it can pick up from `MEMORY.md` state. Token count of Tier 1+2 stays under 2500 even for projects with 50+ features.

**Reference**: `references/memory-persistence-pattern.md`, `references/context-engineering-pattern.md`.

### Phase 3 — Skill Runtime + Tool Registry (3–4 weeks)

> Goal: `loop` becomes a platform. Skills are plug-and-play. Tools are configurable per project. The permission pipeline becomes async.

**Deliverables**:

1. **Skill directory convention** — `.minicode/skills/<skill-name>/SKILL.md` + `scripts/` + `references/`
   - `SKILL.md` frontmatter (`name`, `description`, `triggers`)
   - Scan at startup, build a trigger index, do **not** inject skill bodies into context
   - Available-skills list lives in the system prompt; bodies are loaded on demand
2. **`load_skill` tool** — explicit skill body fetch into the current context
3. **`ToolRegistry` class** — replaces the current `TOOLS = [...]` list literal
   - Each tool: `name + schema + handler + permission_profile`
   - Per-project tool config in `harness.toml` `[tools]` section
   - End users can declare "disable `bash`" or "replace `bash` with restricted version"
4. **Async permission pipeline** — the current synchronous `input()` becomes an event-based interface
   - Implementations: CLI (current), TUI (future), HTTP (future)

**Acceptance**: Drop a `.minicode/skills/run-pytest/SKILL.md` in a user project, and the agent invokes it on "need to run tests" intent. With `bash` disabled, the agent still completes the task by degrading to `read_file` + other tools.

**Reference**: `references/skill-runtime-pattern.md`, `references/tool-registry-pattern.md`.

### Phase 4 — Multi-Agent Coordination + Lifecycle (3–4 weeks)

> Goal: Parallel work without chat-style coordination. Checkpoint-based recovery. Full lifecycle hooks.

**Deliverables**:

1. **Multi-agent patterns** (extends existing `spawn_subagent`)
   - **Coordinator**: workers start with fresh context, complex multi-phase tasks
   - **Fork**: workers inherit parent context, quick parallel splits
   - **Swarm**: workers share a task board, long-running independent work
2. **MessageBus (file-based)** — `.minicode/mailboxes/<agent>.jsonl`
   - Append-only mailbox per agent
   - Teammate threads cycle `WORK → IDLE → SHUTDOWN`
3. **Task board with dependency graph** — `.minicode/board.json` expresses explicit dependencies
4. **Full lifecycle hook events** — extend current 4 events to:
   - `UserPromptSubmit` / `SessionStart` / `SessionEnd` / `PreCompact` / `Notification`
5. **`SessionEnd` mandatory routine** — write `progress.md`, commit, run `./init.sh` (machine-enforced, not agent-self-reported)
6. **WIP=1 machine enforcement** — opening a new feature while one is `in-progress` is blocked at the product level (not just an agent convention)
7. **Checkpoint / resume** — `agent_loop` writes `.minicode/checkpoint.json` every N steps; restart resumes from checkpoint and re-trims pre-checkpoint events

**Acceptance**: Launch 3 subagents (coordinator mode) for independent features; coordinator aggregates results. Kill the process mid-task; restart resumes from checkpoint without losing tool-call state.

**Reference**: `references/multi-agent-pattern.md`, `references/lifecycle-bootstrap-pattern.md`.

### Phase 5 — Observability + Eval + Continuous Improvement (ongoing)

> Goal: Make the harness measurable. Find weak links. Remove constraints that models no longer need.

**Deliverables**:

1. **Structured trace** — every tool call, hook, and compaction event writes to `.minicode/trace.jsonl`
   - Fields: `ts` / `session_id` / `event` / `tool` / `latency_ms` / `tokens_in` / `tokens_out` / `outcome`
2. **Eval suite (≥30 cases)** — extend `harness-creator/evals/evals.json` (10 baseline) with 20 product-specific cases:
   - Permission denial (deny-list, escape, user-decline)
   - Compaction triggers (microcompact, autocompact, fallback)
   - Subagent recursion prevention
   - AGENTS.md load path
   - Cross-session memory recovery
3. **HTML assessment report** — `loop eval --html report.html` (port of `render-assessment-html.mjs`)
4. **Bottleneck dashboard** — auto-flag the lowest-scoring subsystem on each run
5. **Review → Rule feedback loop** — every reviewer catch becomes a new eval case
6. **Periodic ablation** — every two weeks, remove one harness component, measure the impact, restore or commit the simplification

**Acceptance**: `loop eval` runs in CI. Eval pass rate + 5-dimension score + structural smoke all show up in CI. Sub-70 overall score triggers an alert.

## 5. Key Design Decisions

Recorded in `progress.md` for Phase 0; restated here for the full roadmap:

| # | Decision | Rationale |
|---|---|---|
| D1 | **Smart pass-gate** in `init.sh` — when pytest fails, look up the failing test's feature in `feature_list.json`; exit 0 if all failures are in `blocked` features | Dogfooding must work even with known issues. Strict mode would block all development. |
| D2 | **`mypy` `disable_error_code`** (not `ignore_errors`) — suppress only specific codes | Suppresses known dynamic-typing issues surgically; keeps useful checks (syntax, signature-level) active. |
| D3 | **`blocked` (not `in-progress`) for known-bad features** | Honest state. The harness's value is in surfacing problems, not hiding them. |
| D4 | **All 5 roadmap features added to `feature_list.json` upfront** | Establishes the dependency graph; team picks them up in order. |
| D5 | **Two-purpose harness (internal + product)** | Internal dogfooding forces the product to live up to its own claims. |
| D6 | **Project-level 5 files at root, runtime data in `.minicode/`** | Project root stays git-tracked; session state, traces, mailboxes stay gitignored. Aligns with `harness-creator` reference for the 5-file set. |
| D7 | **Status `done` is reserved for verification-passed features** | Enforced in `feature_list.schema.json` (`allOf` branch). The "no self-declared passing" rule is structural, not convention. |

## 6. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Cold-start time (new session → 5 cold-start questions answered) | < 3 min | manual + automated e2e |
| Verification gap (agent confidence vs actual correctness) | approaches 0 | trace cross-reference |
| Verified completion rate (`done` / activated features) | ≥ 95% | eval suite |
| Instruction SNR (relevant instructions / total per task) | ≥ 80% | trace + sampling |
| Cross-session resume success rate | ≥ 90% | 10× kill-and-restart |
| Eval cases | ≥ 30 (10 baseline + 20 product) | `evals.json` count |
| 5-dimension overall score (per `loop audit`) | ≥ 70 | CI gate |

## 7. Dependency Graph

```
f-test-framework-p0 ─→ p1 ─→ p2 ─→ p3 ─→ p4 (BLOCKED)
                      │
                      └─→ f-harness-scaffold (DONE)
                              │
                              ├─→ f-product-init-cmd ─→ f-product-audit-cmd
                              │
                              └─→ f-memory-persistence
                                          │
                                          └─→ f-skill-runtime
                                                      │
                                                      └─→ f-multi-agent
                                                                  │
                                                                  └─→ f-observability
```

`f-harness-scaffold` is the only Phase 0 deliverable. Phases 1–5 each depend on the previous (except `f-product-init-cmd` and `f-memory-persistence` which both branch from `f-harness-scaffold` and can run in parallel).

## 8. Status Snapshot (2026-06-17)

| Status | Count | Features |
|---|---|---|
| `done` | 7 | `f-test-framework-p0` / `p1` / `p2` / `p3`, `f-harness-scaffold`, `f-product-init-cmd`, `f-product-audit-cmd` |
| `blocked` | 1 | `f-test-framework-p4` (LLM-failure fallback test) |
| `not-started` | 4 | `f-memory-persistence` / `f-skill-runtime` / `f-multi-agent` / `f-observability` |

Current `./init.sh` exit code: **0** (smart pass-gate tolerates the one blocked feature).

## 9. References

- `harness-creator/` (gitignored) — reference skill by walkinglabs. SKILL.md, templates, scripts.
- `AGENTS.md` — routing file (read first).
- `feature_list.json` — current feature state (source of truth).
- `progress.md` — latest session log.
- `session-handoff.md` — cross-session handoff template.
- `docs/architecture.md` / `tools.md` / `hooks.md` / `context.md` / `testing.md` — topic docs.
- `init.sh` — verification runner.
- `feature_list.schema.json` — strict schema for `feature_list.json`.

## 10. Decisions (resolved)

All four open questions are now resolved. They are recorded here so the
implementing agent of each phase can build to a stable contract instead
of re-asking.

### Q1 — Phase 1 packaging: **RESOLVED 2026-06-17** (Phase 1)

**Decision**: `loop init` and `loop audit` are CLI subcommands of the existing `loop` project. Single `[project.scripts]` entry point, single package.

**Rationale**: Discovery is simpler when project name, package name, and CLI name all match. Avoids PyPI packaging complications during a phase that is otherwise pure in-repo work.

**Implementation**: `[project.scripts] loop = "loop.cli:main"` + `[build-system] hatchling` + `[tool.hatch.build.targets.wheel] packages = ["loop"]`. `uv sync` installs the entry point. `python -m loop.cli` works as a fallback.

### Q2 — Skill distribution: **RESOLVED 2026-06-17**

**Decision**: Both project-local `.minicode/skills/<name>/` and user-global `~/.minicode/skills/<name>/` are supported. Project-local takes priority (Python-import style: project overrides user).

**Rationale**: Project-local skills are explicit, version-controlled, and shareable. User-global skills reduce duplication for personal preferences. Project-wins precedence avoids ambiguity. Mirrors how Python imports work.

**Implementation contract (Phase 3)**:
- Scan order: `.minicode/skills/` (project) → `~/.minicode/skills/` (user)
- On name collision, project wins; log a debug message that the user-global is shadowed
- Skill `SKILL.md` frontmatter is the metadata contract (`name` / `description` / `triggers`)

### Q3 — Memory privacy: **RESOLVED 2026-06-17**

**Decision**: Within the user's own project (the project containing the running agent), reading `MEMORY.md` is implicit — no consent prompt. When the agent is asked to read `MEMORY.md` in a foreign project (a project other than its active workdir), explicit user consent is required.

**Rationale**: This is the default behavior humans apply to physical desks. You read your own notes freely; you knock before opening someone else's. The cost of asking once per foreign project is negligible; the cost of leaking preferences is high.

**Implementation contract (Phase 2)**:
- Detect "own project" by `Path(MEMORY.md).parent.parent` being inside `WORKDIR` (or its `.minicode/` subdirectory)
- For foreign reads: inject a one-time `PermissionRequest` hook that pauses for explicit y/N
- For own reads: the existing permission pipeline applies (`read_file` may still be blocked by `PreToolUse` hooks if the user added a custom rule)

### Q4 — Checkpoint granularity: **RESOLVED 2026-06-17**

**Decision**: Hybrid default (whichever fires first: 10 tool calls OR 5k new tokens), tunable per-project via `harness.toml`.

**Rationale**: A pure tool-call count over-checks for long tool outputs and under-checks for compact, fast loops. A pure token count has the awkward property that writing the checkpoint itself consumes tokens that count toward the next checkpoint trigger. A hybrid breaks the recursion and matches the two natural axes (latency vs. context growth). Per-project tuning lets heavy-compute projects use a larger N while chatty projects use a smaller one.

**Implementation contract (Phase 4)**:
- Default in code: `CHECKPOINT_EVERY_TOOL_CALLS = 10`, `CHECKPOINT_EVERY_TOKENS = 5000`
- `harness.toml` `[checkpoint]` section overrides: `every_tool_calls = N` and `every_tokens = K`
- Checkpoint writes `.minicode/checkpoint.json` atomically (write to `.tmp`, rename)
- `SessionStart` hook reads checkpoint, restores message history and tool-call ledger, then re-validates against the new session's hook set

### What this section used to be

When this section was titled "Open Questions", it listed the four decision points above without resolution. The decision dates, rationale, and implementation contracts now lock in the answers so the agent implementing each phase has a stable contract.
