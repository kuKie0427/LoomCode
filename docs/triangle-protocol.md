# Triangle Protocol

> **Status**: v1 design draft — specification only, no code yet.
> **Audience**: Anyone reviewing or implementing the orchestrator-generator-reviewer protocol.
> **Relationship**: This is the single source of truth for the data contracts shared by the three role prompts (`loom/agent/system_prompt.py`, `loom/agent/tools.py::SUB_SYSTEM`, `loom/agent/review.py::REVIEW_SYSTEM`). All three prompts MUST reference this document.

---

## 1. Why this exists

loom already has the **infrastructure** for a triangular architecture:

- `task` tool (`loom/agent/tools.py`) spawns Generator subagents
- `review` tool + `run_review()` (`loom/agent/review.py`) spawns Reviewer subagents
- SessionEnd hook + PreCompact hook (`loom/agent/loop.py`) auto-fire reviews
- `ReviewVerdict` dataclass (`loom/agent/review.py:18`) carries structured verdicts
- `feature_list.json` tracks feature state machine (`in-progress` → `review-pending` → `done`)

But the **prompts** for the three roles evolved independently:

- Orchestrator's system prompt doesn't mention it can delegate or that a separate Reviewer exists
- Generator's `SUB_SYSTEM` doesn't know its work will be reviewed, so it doesn't leave audit trails
- Reviewer's `REVIEW_SYSTEM` checks generic code quality, not "did Generator stay within scope"
- No shared vocabulary — each prompt uses different terms for the same concepts
- No machine-readable feedback loop — when Reviewer returns `verdict=fail`, Orchestrator has to interpret natural-language recommendations

This document defines **the data contracts** that close those gaps. The three role prompts will be upgraded (in separate phases) to produce and consume the messages defined here.

---

## 2. Glossary

Five terms form the shared ontology. All three role prompts MUST use these terms exactly.

| Term | Definition | Produced by | Consumed by |
|---|---|---|---|
| **Feature** | A single entry in `feature_list.json` with `id`, `name`, `description`, `verification`, `status`. The unit of work in loom. | Human user / Orchestrator | All three roles |
| **Scope** | The boundary declaration for one delegation: which paths may be changed, which are forbidden, what actions are allowed, resource budget. | Orchestrator (when delegating) | Generator (must obey) / Reviewer (must check against) |
| **Delta** | Generator's structured report of what it actually changed: files modified, files created, files considered-but-not-changed, verification run + result, escalations. | Generator (on completion) | Reviewer (reconciles against git diff) / Orchestrator (decision input) |
| **Verdict** | Reviewer's structured judgment of the Delta vs Feature Card: `pass` / `fail` / `scope_creep` / `quality_issue` / `unknown`, with evidence and recommendations. | Reviewer | Orchestrator |
| **Feedback** | The action Orchestrator takes in response to a non-pass Verdict: `none` / `scope_trim` / `fix_bug` / `improve_quality` / `clarify_with_user` / `escalate`. Encoded as `<feedback_directive>` by Reviewer, executed by Orchestrator. | Reviewer (in `<feedback_directive>` block) | Orchestrator (executes) |

**Key relationships**:

```
Scope ⊇ Delta        (Delta must fall within Scope)
Verdict ↔ Delta      (Verdict is a judgment of Delta's correctness & scope)
Feedback ← Verdict   (Feedback is Orchestrator's response to Verdict)
```

---

## 3. Lifecycle

```
            ┌──────────────────────────────────────────┐
            │              Orchestrator                 │
            │  (loom/agent/loop.py::agent_loop)         │
            └────────┬─────────────────────┬────────────┘
                     │ task()              │ review()
                     │ +feature_card       │ +feature_card
                     │ +scope_envelope     │ +delta_report
                     ▼                     ▼
        ┌────────────────────┐    ┌────────────────────┐
        │     Generator      │    │      Reviewer       │
        │  (spawn_subagent   │    │  (run_review with   │
        │   + SUB_SYSTEM)    │    │   REVIEW_SYSTEM)    │
        └─────────┬──────────┘    └──────────┬──────────┘
                  │                          │
                  │ <delta_report>           │ <verdict>
                  │ (in final message)       │ + <feedback_directive>
                  ▼                          ▼
            ┌──────────────────────────────────────────┐
            │              Orchestrator                 │
            │  parses delta_report, stores in history;  │
            │  parses verdict + feedback_directive,     │
            │  executes feedback action                 │
            └──────────────────────────────────────────┘
```

**Typical happy path**:

1. Orchestrator reads `feature_list.json`, finds active feature
2. Orchestrator builds `<feature_card>` from the feature entry
3. Orchestrator builds `<scope_envelope>` based on its understanding of the feature's blast radius
4. Orchestrator calls `task` tool with prompt = `<feature_card>` + `<scope_envelope>` + natural-language instructions
5. Generator parses the two blocks, does the work, outputs a final message ending in `<delta_report>`
6. Orchestrator parses `<delta_report>`, stores the result in conversation history
7. Orchestrator calls `review` tool with prompt = `<feature_card>` + `<delta_report>` + "please review"
8. Reviewer reads both blocks, runs `git diff`, performs three-way reconciliation, outputs `<verdict>` + `<feedback_directive>`
9. Orchestrator parses both blocks; if `action=none`, marks feature `done`; otherwise executes the action and may re-review

---

## 4. Message format: `<feature_card>`

Produced by Orchestrator. Consumed by Generator and Reviewer.

### 4.1 Schema

```
<feature_card>
id: <string, required>                   # matches feature_list.json "id"
name: <string, required>                 # matches feature_list.json "name"
description: <string, required>          # matches feature_list.json "description"
verification: <string, required>         # matches feature_list.json "verification"
acceptance_criteria:                     # optional, list of strings
  - <criterion 1>
  - <criterion 2>
</feature_card>
```

### 4.2 Field rules

- `id` — non-empty string. Matches a `feature_list.json` entry. Generator and Reviewer use it for logging and trace events.
- `name` — human-readable short label.
- `description` — multi-line allowed. This is the "constitution" for the work: all changes must serve this goal.
- `verification` — the command that proves the feature works. Generator runs it; Reviewer checks it was run.
- `acceptance_criteria` — optional but recommended. Bullet-list of testable conditions. Reviewer uses these as hard pass/fail checks.

### 4.3 Edge cases

| Case | Generator behavior | Reviewer behavior |
|---|---|---|
| `<feature_card>` missing entirely | Output `[UNCLEAR: missing feature_card]` and stop | Output `verdict=unknown, summary="missing feature_card"` |
| `id` empty | Treat as malformed; output `[UNCLEAR]` | Same |
| `description` longer than 2000 chars | Truncation handled by Orchestrator before sending; Generator assumes well-formed | Same |
| `acceptance_criteria` missing | Generator works from `description` alone | Reviewer checks only `description`-derived goals |

### 4.4 Example (good)

```
<feature_card>
id: f-multi-model-providers-p4d
name: TUI provider switching keybinding
description: |
  Add Ctrl+M keybinding that opens ModelPicker modal.
  Modal lists all 5 providers with auth status (✓/✗).
  Selecting a connected provider switches LLM client.
  Unconnected provider → AuthInputModal flow.
verification: uv run pytest tests/test_provider_switch_keybind.py -v
acceptance_criteria:
  - Ctrl+M opens ModelPicker from any composer state
  - Each provider row shows ✓ (connected) or ✗ (no key)
  - Cross-provider switch preserves chat history
  - Unconnected provider selection routes to AuthInputModal
</feature_card>
```

### 4.5 Example (malformed — Generator should reject)

```
<feature_card>
id:
name: ???
description: do stuff
</feature_card>
```

Generator outputs: `[UNCLEAR: feature_card.id is empty; cannot proceed without feature identity]`

---

## 5. Message format: `<scope_envelope>`

Produced by Orchestrator. Consumed by Generator. Reviewer infers it from `feature_card.description` (does not see the original).

### 5.1 Schema

```
<scope_envelope>
allow_paths:                              # required, list of gitignore-style globs
  - <glob 1>
  - <glob 2>
deny_paths:                               # required, list of gitignore-style globs
  - <glob 1>
allow_actions: [read, write, edit, run_tests]   # required, fixed vocabulary
deny_actions: [git_commit, git_push, install_deps, modify_pyproject]  # required
budget:                                   # required
  max_turns: <int, default 30>
  max_files_touched: <int, default 10>
</scope_envelope>
```

### 5.2 Field rules

- `allow_paths` — glob patterns. A path is "in scope" if it matches an `allow_paths` entry AND does not match any `deny_paths` entry.
- `deny_paths` — glob patterns. These are hard fences. Generator MUST NOT touch these paths; if the task requires it, Generator must return `[OUT_OF_SCOPE: ...]`.
- `allow_actions` — vocabulary: `read`, `write`, `edit`, `run_tests`, `bash_readonly`, `web_fetch`. Generator may only use these.
- `deny_actions` — vocabulary: `git_commit`, `git_push`, `git_force_push`, `install_deps`, `modify_pyproject`, `modify_lockfile`, `delete_files`, `modify_tests_to_pass`. Generator MUST NOT do these.
- `budget.max_turns` — soft cap. Generator should self-terminate with `status=partial` if approaching.
- `budget.max_files_touched` — soft cap. Generator should warn in `escalations` if approaching.

### 5.3 Glob syntax

Uses Python `pathspec` library (gitignore-style). Examples:

- `loom/tui/app.py` — exact file
- `loom/tui/**` — all files under `loom/tui/`
- `tests/test_*.py` — all test files matching prefix
- `**/*.py` — all Python files anywhere
- `docs/**` — all docs

### 5.4 Edge cases

| Case | Generator behavior |
|---|---|
| Path matches both `allow_paths` and `deny_paths` | `deny_paths` wins. Do not touch. |
| `allow_paths` empty | Generator treats as "no write actions allowed"; if task requires writes, return `[BLOCKED: allow_paths empty]` |
| `deny_paths` empty | No hard fences; Generator relies on `description` to self-limit |
| `budget.max_turns` exceeded | Output `<delta_report>` with `status=partial` and `escalations: ["budget_exhausted: max_turns"]` |
| Generator discovers task requires a `deny_actions` step (e.g., needs `install_deps` to test) | Return `[OUT_OF_SCOPE: task requires install_deps which is in deny_actions]` |

### 5.5 Example

```
<scope_envelope>
allow_paths:
  - loom/tui/app.py
  - loom/tui/model_picker.py
  - loom/tui/messages.py
  - tests/test_provider_switch_keybind.py
deny_paths:
  - loom/agent/**
  - docs/**
  - pyproject.toml
  - loom/agent/loop.py
allow_actions: [read, write, edit, run_tests, bash_readonly]
deny_actions: [git_commit, git_push, install_deps, modify_pyproject, modify_tests_to_pass]
budget:
  max_turns: 20
  max_files_touched: 4
</scope_envelope>
```

---

## 6. Message format: `<delta_report>`

Produced by Generator (always the **last** block in its final message). Consumed by Orchestrator (decision) and Reviewer (reconciliation against git diff).

### 6.1 Schema

```
<delta_report>
status: <complete | partial | blocked>     # required
files_modified:                            # required (may be empty list)
  - path: <string>
    lines_added: <int>
    lines_deleted: <int>
    summary: <string, one sentence>
files_created:                             # required (may be empty list)
  - <path>
files_considered_but_not_changed:          # required (may be empty list)
  - <path>  # <reason: deny_paths | out_of_description | other>
verification_run: <string>                 # required; the actual command, or "skipped: <reason>"
verification_result: <string>              # required; real output excerpt, or "n/a"
escalations:                               # required (may be empty list)
  - <[UNCLEAR|BLOCKED|CANNOT_FIX|OUT_OF_SCOPE]: <details>>
</delta_report>
```

### 6.2 Field rules

- `status`
  - `complete` — Generator finished the task, ran verification, all good
  - `partial` — Generator made progress but didn't finish (budget exhausted, ambiguity, etc.); escalations must be non-empty
  - `blocked` — Generator could not make progress; escalations must be non-empty
- `files_modified` — every file Generator changed. `lines_added` / `lines_deleted` must match `git diff --stat` within ±10% (whitespace tolerant).
- `files_created` — new files. Listed separately from `files_modified`.
- `files_considered_but_not_changed` — **advisory field**. Generator lists files it looked at and deliberately did NOT change, with reason. This is a best-effort "honesty nudge" that encourages scope-consciousness. Reviewer MAY read it as a hint but MUST NOT fail the delta_report if entries are missing or incomplete. The field is unverifiable (Reviewer cannot distinguish "honest report of consideration" from "fabricated list") — the authoritative scope check is `git diff` vs `scope_envelope` (invariant I8). Absence of entries does NOT trigger any protocol violation.
- `verification_run` — the actual command Generator ran (e.g., `uv run pytest tests/test_foo.py -v`), or `skipped: <reason>` if not run.
- `verification_result` — real output excerpt (first / last few lines, or pass count). NOT "tests passed" — must be the actual output. Reviewer checks this for fabrication.
- `escalations` — list of `[MARKER: details]` strings. Empty list = no escalations. Non-empty requires `status` to be `partial` or `blocked`.

### 6.3 Escalation markers

| Marker | When to use | Example |
|---|---|---|
| `[UNCLEAR: ...]` | `feature_card.description` is ambiguous and Generator cannot proceed | `[UNCLEAR: description says "improve the modal" but doesn't specify which modal or what improvement]` |
| `[BLOCKED: ...]` | Environment problem Generator cannot fix | `[BLOCKED: pytest not installed; cannot run verification]` |
| `[CANNOT_FIX: ...]` | Code bug Generator tried to fix but failed; must include "已尝试 N 种方案" | `[CANNOT_FIX: race condition in _tick_spinner; tried (1) lock, (2) deferred schedule, (3) atomic flag — all failed; see commit history]` |
| `[OUT_OF_SCOPE: ...]` | Task requires touching `deny_paths` or doing `deny_actions` | `[OUT_OF_SCOPE: task requires modifying loom/agent/loop.py to preserve cross-provider history, but loom/agent/** is in deny_paths]` |

### 6.4 Example (complete, no escalations)

```
<delta_report>
status: complete
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added ctrl+m binding in BINDINGS list and action_open_model_picker method"
  - path: loom/tui/model_picker.py
    lines_added: 8
    lines_deleted: 0
    summary: "Added status indicator column to ModelPicker rows"
files_created:
  - tests/test_provider_switch_keybind.py
files_considered_but_not_changed:
  - loom/agent/loop.py  # wanted to add cross-provider history preservation, but loom/agent/** is in deny_paths
  - loom/tui/chat_log.py  # looked at for styling consistency, no changes needed
verification_run: "uv run pytest tests/test_provider_switch_keybind.py -v"
verification_result: |
  ============= test session starts =============
  collected 5 items
  tests/test_provider_switch_keybind.py .... [100%]
  ============= 5 passed in 0.42s =============
escalations: []
</delta_report>
```

### 6.5 Example (partial, with escalation)

```
<delta_report>
status: partial
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added ctrl+m binding and action method"
files_created: []
files_considered_but_not_changed:
  - loom/tui/model_picker.py  # needed status indicator column but couldn't figure out the render contract
verification_run: "skipped: tests not yet written for the binding"
verification_result: "n/a"
escalations:
  - "[UNCLEAR: feature_card says 'status indicators correct for each provider' but does not specify whether to use ✓/✗ glyphs or colored badges; awaiting Orchestrator clarification]"
</delta_report>
```

---

## 7. Message format: `<verdict>` + `<feedback_directive>`

Produced by Reviewer (always the **last** two blocks in its final message). Consumed by Orchestrator.

### 7.1 `<verdict>` schema

```
<verdict>
{
  "status": "<pass|fail|scope_creep|quality_issue|unknown>",
  "summary": "<string, 2-5 sentences in Chinese>",
  "evidence": [
    "<file:line: description>",
    ...
  ],
  "recommendations": [
    "<actionable next step>",
    ...
  ]
}
</verdict>
```

This is unchanged from the existing `REVIEW_SYSTEM` contract (`loom/agent/review.py:94-108`). The JSON inside the tag is parsed by `_parse_verdict()`.

### 7.2 `<feedback_directive>` schema (new)

```
<feedback_directive>
action: [<none|scope_trim|fix_bug|improve_quality|clarify_with_user|escalate>, ...]
target_files: [<path>, ...]      # required when action list contains scope_trim, fix_bug, or improve_quality
target_lines: [<range>, ...]     # required when action list contains scope_trim; format "start-end" (1-indexed, inclusive)
retry_review: <true|false>       # required; whether Orchestrator should call review again after acting
notes: <string, optional>        # free-form hints to Orchestrator
</feedback_directive>
```

`action` is a **list** of actions, not a single value. This allows Reviewer to express compound verdicts (e.g., a change that is both `fail` AND `scope_creep` → `action: [fix_bug, scope_trim]`). Orchestrator executes the actions in order, then re-reviews if `retry_review=true`.

The list MUST be non-empty. A pass verdict uses `action: [none]` (list with one element).

### 7.3 Verdict ↔ Action mapping (mandatory)

Reviewer MUST set `feedback_directive.action` (a list) based on `verdict.status` per this table. The list MAY contain multiple actions when the verdict is compound:

| `verdict.status` | Allowed `action` list values | `retry_review` | What Orchestrator does |
|---|---|---|---|
| `pass` | `[none]` | `false` | Mark feature `done`; update `feature_list.json`; update `progress.md` |
| `fail` | `[fix_bug]` or `[fix_bug, scope_trim]` if the bug fix also requires rolling back out-of-scope changes | `true` | Delegate to Generator (or self-fix) the specific bug; if `scope_trim` included, roll back first; re-run verification; call `review` again |
| `scope_creep` | `[scope_trim]` or `[scope_trim, fix_bug]` if the out-of-scope change also introduced a bug | `true` | Use `edit_file` or `git checkout` to roll back `target_files:target_lines`; if `fix_bug` included, fix the bug after trim; call `review` again |
| `quality_issue` | `[improve_quality]` or `[escalate]` if the quality issues are too deep for one round | `true` for `improve_quality`, `false` for `escalate` | Delegate (or self-fix) the quality issues; or escalate to user if too deep |
| `unknown` | `[clarify_with_user]` or `[escalate]` if user is unavailable | `false` | Write verdict to `progress.md`; ask user for clarification; or escalate |

**Rules:**
- The `action` list MUST be non-empty.
- `none` MAY only appear alone (not combined with other actions).
- `escalate` MAY be combined with other actions (Orchestrator executes the others first, then escalates).
- `clarify_with_user` MAY only appear alone (asking the user is terminal — no point doing other actions first).
- `target_files` and `target_lines` are required if the list contains `scope_trim`, `fix_bug`, or `improve_quality`.

### 7.4 Retry safety bound

Orchestrator MUST escalate to user after **3 consecutive non-pass verdicts** on the same feature. Do not loop review→fix→review indefinitely — likely the feature design itself is broken.

On escalation: mark feature `blocked` in `feature_list.json`, append all 3 verdicts to `progress.md`, notify user.

### 7.5 Example (scope_creep with compound action)

```
<verdict>
{
  "status": "scope_creep",
  "summary": "Ctrl+M keybinding 实现正确，但 Generator 额外重写了 model_picker.py 的列宽计算算法，该改动不在 feature_card.description 范围内。且重写引入了空列表边界 bug。",
  "evidence": [
    "loom/tui/app.py:283: 新增 ctrl+m binding（正确，符合 description）",
    "loom/tui/model_picker.py:47-62: 重写了 _compute_column_widths()，不在 description 里",
    "loom/tui/model_picker.py:55: 空列表输入时 IndexError（bug）",
    "git diff --stat: model_picker.py +38/-12，远超 keybinding 所需"
  ],
  "recommendations": [
    "回滚 loom/tui/model_picker.py:47-62 的列宽改动",
    "保留 loom/tui/app.py:283 的 BINDING 注册"
  ]
}
</verdict>

<feedback_directive>
action: [scope_trim, fix_bug]
target_files: [loom/tui/model_picker.py]
target_lines: [47-62]
retry_review: true
notes: "先回滚 47-62 行的列宽改动（scope_trim），然后修空列表 bug（fix_bug）。回滚后 model_picker.py 应只剩 status indicator 列的添加部分（8 行）。"
</feedback_directive>
```

Orchestrator executes `scope_trim` first (roll back lines 47-62), then `fix_bug` (the empty list bug is in the remaining code). Then re-reviews.

### 7.6 Edge cases

| Case | Reviewer behavior | `run_review()` post-processing |
|---|---|---|
| Verdict JSON has wrong-case status (`"PASS"` vs `"pass"`) | `_parse_verdict()` catches `TypeError` from `ReviewVerdict(**data)`, returns `status="unknown"` | Accepts the `unknown` verdict; logs warning |
| `<feedback_directive>` block missing entirely | N/A (Reviewer didn't output it) | Returns `(verdict_str, None)`; Orchestrator sees `fd=None` and treats as `clarify_with_user` |
| `<verdict>` block missing entirely | N/A | Returns `(verdict_str_with_unknown, None)`; verdict_str contains `[review: unknown — no <verdict> tag]` |
| `action` list is empty (`action: []`) | Invalid per §7.2 | `parse_feedback_directive()` returns `None`; treated as missing directive |
| `action` list contains `none` mixed with other actions (e.g., `[none, fix_bug]`) | Invalid per §7.3 rules | `parse_feedback_directive()` returns `None`; treated as missing directive |
| `action` list contains `clarify_with_user` mixed with other actions | Invalid per §7.3 rules | `parse_feedback_directive()` returns `None`; treated as missing directive |
| `retry_review=true` but `action=[none]` | Contradictory (pass should be `retry_review=false`) | `parse_feedback_directive()` returns `None`; treated as missing directive |
| `target_files` missing but `action` contains `scope_trim` | Invalid per §7.3 | `parse_feedback_directive()` returns `None`; treated as missing directive |
| Reviewer outputs multiple `<verdict>` blocks | Take the first; ignore the rest | Log warning about duplicate |
| Reviewer outputs `<verdict>` but no JSON inside | `_parse_verdict()` returns `status="unknown"` | Accepts the `unknown` verdict |

---

## 8. Lifecycle invariants

These are the formal rules that the protocol enforces. Each has an eval case in Phase 7 (TP-7). The **Enforcement type** column distinguishes:

- **hard** — code-enforced, regression-tested, fail-closed. Violation → code rejects/aborts, not just logs.
- **soft** — prompt-enforced or behaviorally tested. Violation → system-reminder or log, LLM may or may not obey. Fail-open.

| # | Invariant | Enforcement type | Enforced by |
|---|---|---|---|
| I1 | Every `task()` call MUST be followed by `run_review()` before the feature's status becomes `done` | hard | `feature_list.json` state machine: `done` transition requires a `pass` verdict in `review_attempts` history |
| I2 | Reviewer's tool list MUST NOT contain `task` or `review` (no self-review, no nested review) | hard | `REVIEW_TOOLS` tuple in `tools.py:1323` |
| I3 | Generator's tool list MUST NOT contain `task` or `review` (no re-delegation) | hard | `SUB_TOOLS` list in `tools.py:1248` |
| I4 | `<delta_report>` MUST be the last block in Generator's final message (closing `</delta_report>` tag followed only by whitespace or EOF) | soft → hard | TP-2: wrapper around `spawn_subagent` checks return string, appends `<system-reminder>` on violation. TP-8: graduates to `HookAbort` (hard) via graduated response (regenerate-once → soft → hard) |
| I5 | `<verdict>` and `<feedback_directive>` MUST both be present in Reviewer's final message | hard | `run_review()` post-processing: `parse_verdict()` + `parse_feedback_directive()`; missing → returns `verdict=unknown` + `fd=None` |
| I6 | `feedback_directive.action` list MUST be non-empty and contain only valid actions per §7.3 combination rules | hard | `run_review()` post-processing: `parse_feedback_directive()` validates list non-empty + combination rules (§7.6 edge cases); invalid → returns `fd=None` |
| I7 | `delta_report.files_modified` MUST match `git diff --numstat` within ±10% per file | hard | `run_review()` pre-processing: calls `validate_delta_against_git_diff()` BEFORE spawning Reviewer; if violations non-empty, returns `verdict=unknown` with summary listing violations — bypasses LLM round-trip |
| I8 | `delta_report.files_modified` paths MUST all fall within `scope_envelope.allow_paths` and not match `deny_paths` | hard | `run_review()` pre-processing: calls `validate_delta_against_scope()` BEFORE spawning Reviewer; if violations non-empty, returns `verdict=unknown` with summary listing violations — bypasses LLM round-trip |
| I9 | Orchestrator MUST escalate after 3 consecutive non-pass verdicts on the same feature | hard | `feature_list.json` per-feature `review_attempts: int` counter: incremented on each `run_review()` non-pass verdict, reset to 0 on `pass`. Orchestrator reads counter; `review_attempts >= 3` → forced `escalate`. Counter survives autocompact and cross-session (persisted in JSON, not prompt context) |
| I10 | `feedback_directive.retry_review=true` → Orchestrator MUST call `review` again after acting | soft | Orchestrator system prompt (TP-4) + trace event `triangle.feedback` records the action; eval case verifies trace event sequence |
| I11 | Each `task()` and `review()` tool call MUST emit a `triangle.delegate` / `triangle.review` trace event with `role: "generator" \| "reviewer"` field | hard | `trace.py` extension: events recorded in outer `task` / `review` tool handlers (NOT inside `spawn_subagent`, which would conflate Generator and Reviewer) |
| I12 | PreCompact review verdict injected as `[system-reminder]` SHOULD be recognized by Orchestrator (not answered as user chat) | soft | Orchestrator system prompt (TP-4 Task 1) instructs the LLM; trace event `triangle.feedback` verifies Orchestrator eventually processed the verdict. Not a code-enforced invariant — LLM may misrecognize on edge cases |

---

## 9. Versioning policy

### 9.1 Current version

This document is **v1**. The `PROTOCOL_VERSION` constant in `loom/agent/triangle_protocol.py` is `"v1"`. All three role prompts reference "Triangle Protocol v1" by name.

### 9.2 Version field on messages

Each protocol block (`<feature_card>`, `<scope_envelope>`, `<delta_report>`, `<feedback_directive>`) MAY include an optional `_protocol: v1` field. When the field is **absent**, the parser assumes the message is v1 (the current version). When the field is **present**, it must match `PROTOCOL_VERSION` exactly.

### 9.3 Parser rejection rules

Parsers (`parse_delta_report`, `parse_feedback_directive`, etc.) accept a `max_version: str = PROTOCOL_VERSION` keyword argument. The rejection logic is:

| `_protocol` field in message | Parser behavior |
|---|---|
| Absent | Accept as `max_version` (default v1) |
| Present and equals `max_version` | Accept |
| Present and is a known older version (e.g., `"v0"`) | Accept with deprecation warning logged |
| Present and is an unknown newer version (e.g., `"v99"`) | **Reject** — return `None` and log error |

This allows forward compatibility (older messages still parse) while preventing silent forward drift (a message claiming v99 is rejected, not misinterpreted).

### 9.4 Evolution rules

- **Adding a field**: bump `PROTOCOL_VERSION` to v2, add the field as optional in the dataclass, update parsers to handle both with-and-without, update prompts, add eval cases. Old messages (no `_protocol` field or `_protocol: v1`) still parse under v2.
- **Renaming a field**: bump to v2, add the new name as an alias in the parser, mark the old name as deprecated (parser accepts both for one version), bump to v3 and drop the old name.
- **Removing a field**: bump to v2, stop emitting the field in serializers, parsers still accept it (ignore), bump to v3 and reject messages that contain it.
- **Changing a field's semantics**: treat as remove + add. The old field is deprecated, the new field is added, both coexist for one version, then the old is removed.

### 9.5 Lockstep update requirement

If the protocol changes, all three prompts (`system_prompt.py`, `tools.py::SUB_SYSTEM`, `review.py::REVIEW_SYSTEM`) must be updated in the same PR. Eval cases in `loom/eval/cases/triangle_protocol.py` must be updated to lock the new version. The `PROTOCOL_VERSION` constant must bump. AGENTS.md Working Rule #18 enforces this.

---

## 10. Changelog

| Version | Date | Changes |
|---|---|---|
| v1 | 2026-06-24 | Initial design draft. Defines FeatureCard, ScopeEnvelope, DeltaReport, Verdict + FeedbackDirective, 12 lifecycle invariants. |
| v1-revised | 2026-06-24 | Third-party review (Oracle + Momus) revisions: `feedback_directive.action` changed from single value to list (§7.2-7.3); `files_considered_but_not_changed` downgraded to advisory (§6.2); §8 added Enforcement type column (hard/soft); I9 counter persisted in `feature_list.json` (not prompt context); I7/I8 enforcement moved to `run_review()` pre-processing; §7.6 edge cases table added; §9 versioning policy made explicit (default behavior, rejection rules, evolution rules); I6 repurposed to action-list validation; I12 labeled soft. |
