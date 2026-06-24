# Review Agent

loom ships a read-only review subagent that inspects code changes against the original feature description. It answers the question "did we build what we said we would build?" The review agent lives in `loom/agent/review.py` and uses the same subagent infrastructure as `spawn_subagent` — it talks to an LLM with a focused prompt, a limited tool set, and a fixed turn budget.

## 1. What & Why

The review agent is a **semantic quality gate** that runs alongside generation. It forms the third corner of a triangle:

```
orchestrator (main agent loop)
  ├── generate (task subagent, writes code)
  └── review (review subagent, inspects intent vs. output)
```

The orchestrator spawns both. The generate subagent produces code; the review subagent reads the code and the feature description and produces a verdict. Neither can review itself, and the review subagent has no write tools — it can only read files and run `grep`/`glob`.

When to use the review agent:

- **Long-running sessions** where the orchestrator may drift from the original task description.
- **Autonomous mode** where a human is not watching every turn.
- **Critical features** that need a second pair of eyes before the feature is marked done.

## 2. Quick Start

The review agent runs automatically at the end of every session. This is **SessionEnd auto-review** and it is ON by default.

```toml
# Default behavior — no config needed.
# SessionEnd auto-review fires after the
# last turn, as a non-blocking daemon thread.
```

To disable SessionEnd review:

```toml
[review]
session_end_review = false
```

**PreCompact review** (review before context compression) is opt-in and OFF by default. See §3 for configuration.

## 3. Configuration

Four fields live under `[review]` in `harness.toml`:

| Field | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. `false` disables the entire review loop. |
| `session_end_review` | bool | `true` | Auto-call review agent at `SessionEnd`. |
| `pre_compact_review` | bool | `false` | Call review agent before autocompact. |
| `max_turns` | int | `15` | Max turns for the review subagent. |

### Dev session (review off entirely)

```toml
[review]
enabled = false
```

Use this during rapid prototyping where a review would just slow the loop.

### Production autonomous (SessionEnd only)

```toml
# Defaults. No [review] block needed,
# but shown here for clarity:
[review]
enabled = true
session_end_review = true
pre_compact_review = false
max_turns = 15
```

SessionEnd fires after the last tool use. It runs in a daemon thread so the session closes quickly even if the review call takes time.

### Critical feature (SessionEnd + PreCompact)

```toml
[review]
session_end_review = true
pre_compact_review = true
max_turns = 25
```

PreCompact review runs before autocompact discards old context. The verdict is injected as a `system-reminder` so the agent can self-correct before the next LLM call.

## 4. Usage Patterns

There are three ways to trigger a review:

### Explicit tool call

The main agent calls the `review` tool directly with a target feature description and a scope hint. This is the most flexible path — the agent chooses when and what to review.

```text
tool: review
args: { description: "f-session-end-review: auto-review at SessionEnd" }
```

### SessionEnd auto-review (default)

After the session's last turn, the `SessionEnd` hook spawns a review in a daemon thread. The verdict is logged to the trace and written to the project's `progress.md` (appended as `Final Review: ...`). Because it runs in a daemon thread, it does not block session close.

```bash
grep "Final Review" progress.md
```

### PreCompact auto-review (opt-in)

If `pre_compact_review = true`, the review agent runs before autocompact truncates the message list. The verdict is injected as a `system-reminder` message so the next LLM turn sees the assessment. This is useful for catching intent drift before old context is summarized away.

## 5. Verdict Format

The review agent returns a structured verdict with five possible statuses:

| Status | Meaning | Agent response |
|---|---|---|
| `pass` | Implementation matches the feature description. | Feature can proceed. |
| `fail` | Implementation does not match the feature description. | Agent should re-read the description and fix the gap. |
| `scope_creep` | Implementation does more than the feature description asked for. | Agent should trim unrelated changes. |
| `quality_issue` | Implementation matches the description but has code quality problems (missing tests, hardcoded values, fragile patterns). | Agent should address the concerns before marking done. |
| `unknown` | Review agent could not form a judgment. | Agent should provide more context or a narrower scope. |

Sample verdict JSON (matching the ``ReviewVerdict`` dataclass in ``loom/agent/review.py``):

```json
{
  "status": "pass",
  "summary": "Implementation matches f-repo-management scope. All 5 acceptance criteria are met.",
  "evidence": ["src/loom/repo/manager.py: +220 lines, all covered by tests in tests/test_repo_manager.py."],
  "recommendations": ["Add error handling for network timeout in fetch_upstream()."]
}
```

The verdict is also written to the trace event log (see `loom eval trace show`). You can search by verdict status:

```bash
loom eval --filter review
```

## 6. Division with `verify` tool

loom has two independent verification paths.

| Tool | What it checks | How |
|---|---|---|
| `verify` | Machine correctness | Runs `./init.sh` or a user-specified command. Does the build pass? Do tests pass? |
| `review` | Semantic correctness | Reads the code and the feature description. Did we build the right thing? |

Both must pass before a feature can be marked done. A `verify` pass with a `review` fail means the implementation is correct but wrong — it passes tests for the wrong feature. A `review` pass with a `verify` fail means the implementation is on-intent but broken — the tests don't pass yet.

During the eval suite, every eval case can assert against review verdicts:

```bash
uv run python -m loom.cli eval --filter review
```

## 7. Troubleshooting

### Review timeout

The review agent has a fixed turn budget (`max_turns`, default 15). If the review times out, either the feature scope is too large for the review subagent to assess in 15 turns, or the review agent is stuck on a tool call. Increase `max_turns` or split the feature into smaller units.

### LLM failure

The review subagent uses the same LLM client as the main agent. If the main loop is working but the review fails, check that your API key has quota for concurrent calls — the SessionEnd daemon thread competes with the main thread.

### `verdict=unknown`

The review agent could not form a judgment. This usually means the feature description is too vague or the code changes are too scattered. Provide a more specific description or pass a narrower scope hint.

### Too expensive

Each review call is an LLM round trip. The SessionEnd auto-review is a single call per session (one LLM hit). PreCompact review fires before every autocompact, which can be several times per session. If cost is a concern, disable PreCompact:

```toml
[review]
pre_compact_review = false
```

This is already the default.

## 8. Triangular Architecture

The review agent is part of a three-role architecture:

- **Orchestrator** — the main agent loop. Spawns generate and review subagents. Owns the session lifecycle.
- **Generate** — a task subagent (via `spawn_subagent`). Writes code, runs tests, makes changes.
- **Review** — a review subagent (via `run_review`). Reads code, compares to feature description, produces a verdict.

Fork parallelism lives inside the orchestrator layer. The orchestrator can spawn a generate subagent and a review subagent concurrently, though in practice review starts after generation produces a diff.

The `review` tool is a **SUB_TOOL** — task subagents can call it, but review subagents cannot spawn further reviews (no nested review-inception). The SessionEnd and PreCompact auto-reviews bypass the tool entirely and call `run_review()` directly from the hook system.

## 9. Design Decisions

Brief notes on why loom's review agent is shaped this way. Future contributors, please update this section if you reverse any of these.

- **Subagents don't self-review.** A subagent that reviews its own work has no incentive to report failures — it would be reviewing its own output against its own interpretation of the description. The review agent is always a different LLM call with a different prompt, and its tool set excludes write operations. This is a "separation of concerns" principle, not a technical limitation.

- **Review is not in SUB_TOOLS for subagents.** Preventing review-inception (a review subagent spawning a review of its own review) avoids infinite regression and cost blowup. If a subagent needs clarification, it returns a structured failure and the orchestrator decides the next step.

- **SessionEnd is opt-out, not opt-in.** A new loom user who runs a long session should get a safety net by default. Opt-out means the session produces a review artifact in `progress.md` without any config work. Users who find the post-session LLM call expensive or noisy can disable it in one line.

- **PreCompact is opt-in, not opt-out.** Every PreCompact review consumes an LLM call, and autocompact can fire multiple times per session. For a typical development session (fast iterations, small changes), the SessionEnd review is sufficient. PreCompact is reserved for sessions where context compression risks losing intent — long-running autonomous features with many rounds.
