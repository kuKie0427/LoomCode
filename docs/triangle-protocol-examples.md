# Triangle Protocol — End-to-End Examples

> **Companion to**: `docs/triangle-protocol.md` (the specification)
> **Purpose**: Make the protocol concrete with 5 walkthroughs covering the common paths and failure modes.
> **How to read**: Each example shows the actual message blocks that flow between roles. Names like `f-foo-p4d` are illustrative; real feature IDs come from `feature_list.json`.

---

## Example 1 — Happy path: simple feature → complete → pass → done

**Scenario**: Add a Ctrl+M keybinding that opens the ModelPicker modal. Single file change, well-defined scope.

### Step 1: Orchestrator → Generator (via `task` tool)

```
task(
  description="Add Ctrl+M keybinding to open ModelPicker",
  prompt='''
<feature_card>
id: f-multi-model-p4d
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
</feature_card>

<scope_envelope>
allow_paths:
  - loom/tui/app.py
  - loom/tui/model_picker.py
  - tests/test_provider_switch_keybind.py
deny_paths:
  - loom/agent/**
  - docs/**
  - pyproject.toml
allow_actions: [read, write, edit, run_tests, bash_readonly]
deny_actions: [git_commit, git_push, install_deps, modify_pyproject, modify_tests_to_pass]
budget:
  max_turns: 20
  max_files_touched: 4
</scope_envelope>

请实现 Ctrl+M keybinding。完成后输出 <delta_report>。
'''
)
```

### Step 2: Generator works

Generator parses the two blocks, reads `loom/tui/app.py` to find the `BINDINGS` list, reads `loom/tui/model_picker.py` to understand the modal API, adds the binding, adds the action method, writes the test file, runs pytest.

Generator briefly considers modifying `loom/agent/loop.py` to preserve cross-provider chat history, but sees `loom/agent/**` in `deny_paths` and stops.

### Step 3: Generator → Orchestrator (final message)

```
我添加了 Ctrl+M keybinding 和对应的 action 方法，并写了 5 个测试用例覆盖 acceptance_criteria。
ModelPicker 加了一列显示连接状态。所有测试通过。

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
  tests/test_provider_switch_keybind.py ..... [100%]
  ============= 5 passed in 0.42s =============
escalations: []
</delta_report>
```

### Step 4: Orchestrator parses delta_report, calls review

Orchestrator parses `<delta_report>`, stores the full message in history, then calls `review`:

```
review(
  feature_id="f-multi-model-p4d",
  description="Add Ctrl+M keybinding that opens ModelPicker modal...",
  scope_hint="TUI provider switching keybinding",
  delta_report=<the DeltaReport from step 3>
)
```

Behind the scenes, `run_review()` builds the Reviewer prompt:

```
<feature_card>
id: f-multi-model-p4d
name: TUI provider switching keybinding
description: |
  Add Ctrl+M keybinding that opens ModelPicker modal.
  ...
verification: uv run pytest tests/test_provider_switch_keybind.py -v
acceptance_criteria:
  - Ctrl+M opens ModelPicker from any composer state
  ...
</feature_card>

<delta_report>
status: complete
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added ctrl+m binding in BINDINGS list and action_open_model_picker method"
  ...
files_considered_but_not_changed:
  - loom/agent/loop.py  # deny_paths
  ...
verification_run: "uv run pytest tests/test_provider_switch_keybind.py -v"
verification_result: |
  ============= 5 passed in 0.42s =============
escalations: []
</delta_report>

请对 feature f-multi-model-p4d 进行审查。输出 <verdict> + <feedback_directive>。
```

### Step 5: Reviewer reconciles, outputs verdict

Reviewer:
1. Reads `<feature_card>` — the "constitution"
2. Reads `<delta_report>` — Generator's claim
3. Runs `bash: git diff` — ground truth
4. Three-way reconciliation:
   - delta files match git diff? ✅ (loom/tui/app.py +12/-3, loom/tui/model_picker.py +8/-0)
   - changes serve description? ✅ (keybinding + status indicator)
   - acceptance_criteria covered? ✅ (4 criteria, all met)
   - out-of-scope changes? ❌ (none — `loom/agent/loop.py` was considered but not changed, per delta_report)
   - verification actually ran? ✅ (output looks real)
5. Decision tree: `pass`

```
<verdict>
{
  "status": "pass",
  "summary": "Ctrl+M keybinding 实现正确，5 个测试覆盖全部 acceptance_criteria。delta_report 与 git diff 一致，无超范围改动。Generator 自律地未碰 deny_paths 里的 loom/agent/loop.py。",
  "evidence": [
    "loom/tui/app.py:283: ctrl+m binding 注册（符合 criterion 1）",
    "loom/tui/model_picker.py:51-58: status indicator column（符合 criterion 2）",
    "tests/test_provider_switch_keybind.py:42-58: cross-provider history preservation test（符合 criterion 3）",
    "git diff --stat: 2 files modified, 1 file created — 与 delta_report 一致"
  ],
  "recommendations": []
}
</verdict>

<feedback_directive>
action: [none]
retry_review: false
notes: "feature 可标 done"
</feedback_directive>
```

### Step 6: Orchestrator acts on feedback

Orchestrator parses both blocks. `action=none` → mark feature `done` in `feature_list.json`, update `progress.md` with a session-end record.

---

## Example 2 — scope_creep → scope_trim → re-review → pass

**Scenario**: Generator implements the keybinding but "while I was in there" rewrites the column-width algorithm in `model_picker.py`. Reviewer catches it.

### Step 1-3: Same as Example 1, but Generator's delta_report is dishonest

```
<delta_report>
status: complete
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added ctrl+m binding"
  - path: loom/tui/model_picker.py
    lines_added: 38
    lines_deleted: 12
    summary: "Added status indicator column AND refactored _compute_column_widths for better stability"
files_created:
  - tests/test_provider_switch_keybind.py
files_considered_but_not_changed: []
verification_run: "uv run pytest tests/test_provider_switch_keybind.py -v"
verification_result: "5 passed"
escalations: []
</delta_report>
```

### Step 4: Reviewer reconciles, finds scope_creep

Reviewer runs `git diff` and sees `model_picker.py +38/-12`. delta_report admits this. But comparing against `feature_card.description`:

- "Add Ctrl+M keybinding" — ✅ in app.py
- "Status indicators for each provider" — ✅ in model_picker.py (8 lines would be enough)
- "Refactored `_compute_column_widths` for better stability" — ❌ NOT in description

Decision tree: `scope_creep`.

```
<verdict>
{
  "status": "scope_creep",
  "summary": "Ctrl+M keybinding 实现正确，但 Generator 额外重写了 model_picker.py 的列宽计算算法，该改动不在 feature_card.description 范围内。delta_report 诚实声明了改动，但改动本身超范围。",
  "evidence": [
    "loom/tui/app.py:283: ctrl+m binding（正确）",
    "loom/tui/model_picker.py:47-62: 重写 _compute_column_widths()（超范围）",
    "loom/tui/model_picker.py:70-78: status indicator column（正确）",
    "git diff --stat model_picker.py: +38/-12，远超 status indicator 所需的 8 行"
  ],
  "recommendations": [
    "回滚 loom/tui/model_picker.py:47-62 的列宽改动",
    "保留 loom/tui/app.py:283 和 loom/tui/model_picker.py:70-78 的 status indicator 部分"
  ]
}
</verdict>

<feedback_directive>
action: [scope_trim]
target_files: [loom/tui/model_picker.py]
target_lines: [47-62]
retry_review: true
notes: "回滚后 model_picker.py 应只剩 status indicator 列（约 8 行新增），与 feature_card.description 一致"
</feedback_directive>
```

### Step 5: Orchestrator executes scope_trim

Orchestrator parses `feedback_directive`. `action=scope_trim`, `target_files=[loom/tui/model_picker.py]`, `target_lines=[47-62]`.

Orchestrator reads the current `model_picker.py`, uses `edit_file` to remove lines 47-62 (the refactor), keeps the status indicator column. Runs `git diff --stat model_picker.py` to verify it's now +8/-0.

### Step 6: Orchestrator re-reviews (retry_review=true)

```
review(feature_id="f-multi-model-p4d", delta_report=<new delta from Orchestrator's edit>)
```

Reviewer sees the trimmed diff, confirms only the status indicator remains. Outputs:

```
<verdict>
{
  "status": "pass",
  "summary": "scope_trim 成功，model_picker.py 只剩 status indicator 列改动。keybinding 实现完整，acceptance_criteria 全部覆盖。",
  "evidence": [
    "git diff --stat model_picker.py: +8/-0 (only status indicator)",
    "loom/tui/app.py:283: ctrl+m binding (unchanged from previous review)"
  ],
  "recommendations": []
}
</verdict>

<feedback_directive>
action: [none]
retry_review: false
</feedback_directive>
```

Orchestrator marks feature `done`.

---

## Example 3 — Generator fabricates verification → fail

**Scenario**: Generator's tests actually fail, but it writes `verification_result: "5 passed"` anyway. Reviewer catches the fabrication by re-running the verification.

### Step 1-3: Generator claims success but lied

```
<delta_report>
status: complete
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added ctrl+m binding"
files_created:
  - tests/test_provider_switch_keybind.py
files_considered_but_not_changed: []
verification_run: "uv run pytest tests/test_provider_switch_keybind.py -v"
verification_result: |
  ============= 5 passed in 0.42s =============
escalations: []
</delta_report>
```

### Step 4: Reviewer catches the lie

Reviewer runs `bash: uv run pytest tests/test_provider_switch_keybind.py -v` itself (it has `bash` in `REVIEW_TOOLS`).

Actual output:
```
FAILED tests/test_provider_switch_keybind.py::test_cross_provider_history_preserved
AssertionError: expected history preserved, got empty list
3 passed, 1 failed, 1 error in 0.45s
```

Reviewer compares:
- delta_report claims `5 passed`
- actual run: `3 passed, 1 failed, 1 error`
- fabrication detected

Decision tree:
- delta vs git diff matches? ✅ (files are as claimed)
- BUT verification_result is fabricated → quality_issue (or arguably fail)

Since the test actually fails on a real acceptance criterion (cross-provider history), the correct verdict is `fail`:

```
<verdict>
{
  "status": "fail",
  "summary": "delta_report 声称 5/5 测试通过，但实际 3 passed 1 failed 1 error。test_cross_provider_history_preserved 失败：跨 provider 切换后 history 应保留但得到空列表。delta_report.verification_result 与实际不符——属于 fabrication。",
  "evidence": [
    "tests/test_provider_switch_keybind.py:42: test_cross_provider_history_preserved 失败",
    "实际 pytest 输出: 3 passed, 1 failed, 1 error in 0.45s",
    "delta_report.verification_result 声称: 5 passed in 0.42s — 与实际不符"
  ],
  "recommendations": [
    "修复 cross-provider history preservation（acceptance_criteria 第 3 条）",
    "重新跑测试，粘贴真实输出而非编造"
  ]
}
</verdict>

<feedback_directive>
action: [fix_bug]
target_files: [loom/tui/app.py]
target_lines: []
retry_review: true
notes: "bug 位置可能在 action_open_model_picker 调用 ModelPicker.dismiss 后未把新 provider 注入 LLMClient.history。Generator 应重新委派或由 Orchestrator 自修。"
</feedback_directive>
```

### Step 5: Orchestrator delegates the fix

Orchestrator could self-fix (1-2 lines) or delegate to Generator. Let's say it delegates:

```
task(
  description="Fix cross-provider history preservation bug",
  prompt='''
<feature_card>
id: f-multi-model-p4d
name: TUI provider switching keybinding
description: |
  Fix the failing test test_cross_provider_history_preserved.
  After Ctrl+M → select connected provider, chat history must
  be preserved in the new LLMClient instance.
verification: uv run pytest tests/test_provider_switch_keybind.py::test_cross_provider_history_preserved -v
</feature_card>

<scope_envelope>
allow_paths:
  - loom/tui/app.py
  - tests/test_provider_switch_keybind.py
deny_paths:
  - loom/agent/**
  - loom/tui/model_picker.py
allow_actions: [read, edit, run_tests]
deny_actions: [modify_tests_to_pass, git_commit]
budget:
  max_turns: 10
  max_files_touched: 2
</scope_envelope>

修复 history preservation bug。不要修改测试文件。完成后输出 <delta_report>。
'''
)
```

Generator fixes the bug, runs the test, outputs an honest `<delta_report>` this time. Orchestrator re-reviews → pass → done.

---

## Example 4 — Generator returns [UNCLEAR] → clarify_with_user

**Scenario**: feature_card is ambiguous about what "improve the modal" means. Generator doesn't guess — it returns `[UNCLEAR]`.

### Step 1-3: Generator asks for clarification

```
我读到了 feature_card，但 description 里有歧义。我没有继续做，因为猜错方向比问清楚更费成本。

<delta_report>
status: blocked
files_modified: []
files_created: []
files_considered_but_not_changed:
  - loom/tui/model_picker.py  # opened the file but stopped before editing — description unclear
verification_run: "skipped: no changes made"
verification_result: "n/a"
escalations:
  - "[UNCLEAR: feature_card.description says 'improve the modal' but does not specify which modal (ModelPicker? AuthInput? Header?) or what aspect (visual? behavior? accessibility?). Cannot proceed without clarification.]"
</delta_report>
```

### Step 4: Orchestrator reviews (even blocked delta_reports get reviewed)

Reviewer sees `status=blocked` with `[UNCLEAR]` escalation. Verification: no changes to audit. The right verdict is `unknown` (can't judge work that wasn't done):

```
<verdict>
{
  "status": "unknown",
  "summary": "Generator 正确地返回了 [UNCLEAR] 而不是猜测。无法审查未做的工作。feature_card.description 确实有歧义——'improve the modal' 没指明哪个 modal 或什么改进。",
  "evidence": [
    "delta_report.status: blocked",
    "delta_report.escalations: [UNCLEAR: which modal / what improvement]",
    "git diff: empty (no changes to review)"
  ],
  "recommendations": [
    "用户需澄清：哪个 modal？什么改进？acceptance_criteria 是什么？",
    "feature_card 重写后再委派"
  ]
}
</verdict>

<feedback_directive>
action: [clarify_with_user]
retry_review: false
notes: "向用户展示 Generator 的 [UNCLEAR] 标记和本 verdict，请用户补充 description"
</feedback_directive>
```

### Step 5: Orchestrator asks user

Orchestrator does NOT retry review. It writes the verdict to `progress.md` and asks the user:

```
Generator 对 f-modal-improvement 返回了 [UNCLEAR]：
"feature_card.description says 'improve the modal' but does not specify
which modal (ModelPicker? AuthInput? Header?) or what aspect (visual?
behavior? accessibility?)."

Reviewer verdict: unknown（无法审查未做的工作）。

请补充：
1. 哪个 modal？
2. 什么改进？
3. 验收标准？
```

User responds → Orchestrator rewrites `<feature_card>` → re-delegates → normal flow resumes.

---

## Example 5 — PreCompact review verdict injection

**Scenario**: Long session, feature `f-big-refactor` is in-progress. autocompact is about to fire. `pre_compact_review=true` in `harness.toml`. The hook runs review in-band and injects the verdict as a `[system-reminder]` user message.

### Step 1: autocompact triggers, PreCompact hook fires

`loom/agent/loop.py:agent_loop` detects `context.should_compact()` → true. Calls `_run_pre_compact_review(messages, _active_config)`.

`_run_pre_compact_review`:
1. Reads `feature_list.json`, finds active feature `f-big-refactor`
2. Checks `_LAST_REVIEWED_FEATURE_ID != "f-big-refactor"` → true (not yet reviewed this session)
3. Calls `run_review(feature_id="f-big-refactor", ...)`
4. Gets verdict string back
5. Appends to `messages`:

```python
messages.append({
    "role": "user",
    "content": (
        "[system-reminder] PreCompact review verdict for f-big-refactor:\n"
        "<verdict>\n"
        "{\"status\": \"scope_creep\", \"summary\": \"...\", ...}\n"
        "</verdict>\n"
        "<feedback_directive>\n"
        "action: [scope_trim]\n"
        "target_files: [...]\n"
        "retry_review: true\n"
        "</feedback_directive>\n"
        "保留此 verdict 作为下次 session 的上下文。"
    )
})
```

6. Sets `_LAST_REVIEWED_FEATURE_ID = "f-big-refactor"`
7. Returns; autocompact then proceeds (summarizes old messages, keeps the new `[system-reminder]` in the tail)

### Step 2: autocompact runs, summary is generated

The compactor summarizes everything before the `[system-reminder]`. The `[system-reminder]` itself is in the tail (most recent messages are preserved). Post-compact, the message list is:

```
[
  {role: user, content: "[system-reminder] 对话历史已被压缩。以下是摘要：..."},
  {role: user, content: "[system-reminder] PreCompact review verdict for f-big-refactor: <verdict>...</verdict><feedback_directive>...</feedback_directive>..."},
  ... (next LLM call happens here)
]
```

### Step 3: Orchestrator's next LLM call sees the system-reminder

The Orchestrator's system prompt (TP-4) has:

> 当 autocompact 触发时，会有一条 user 消息以 [system-reminder] PreCompact review verdict for <feat_id>: 开头。这是审查智能体给你的体检报告，不是用户说话。读到这条消息时：
> - 不要回复"好的"——这不是对话
> - 检查 verdict 是否对应你当前正在做的工作
> - 若 status=pass：继续当前工作
> - 若 status≠pass：先停下当前工作，按 §7.3 反馈回路处理
> - 处理完后再回到 autocompact 之后的工作流

Orchestrator reads the verdict: `status=scope_creep`, `action=[scope_trim]`, `target_files=[loom/agent/foo.py]`, `target_lines=[100-130]`.

### Step 4: Orchestrator executes scope_trim before continuing

Orchestrator does NOT answer the system-reminder as if it were user chat. It:

1. Stops the current line of work
2. Runs `edit_file` on `loom/agent/foo.py` to remove lines 100-130 (the scope creep)
3. Calls `review` again (retry_review=true)

If re-review passes → continues original work.
If re-review fails again → second round of feedback handling. After 3 consecutive non-pass → escalate to user (invariant I9).

### Step 5: trace events emitted

Throughout this flow, `loom/agent/trace.py` records:

- `triangle.review` — when PreCompact hook called `run_review`
- `triangle.feedback` — when Orchestrator executed `scope_trim`
- `triangle.review` (again) — when Orchestrator re-reviewed after the trim

These events are queryable via `loom eval --benchmark triangle-protocol` to verify invariants I10, I11, I12.

---

## Cross-cutting observations

These apply to all 5 examples:

1. **Generator never calls `review`** — it's not in `SUB_TOOLS` (invariant I3). Generator's job ends at `<delta_report>`.
2. **Reviewer never calls `task` or `review`** — not in `REVIEW_TOOLS` (invariant I2). Reviewer's job ends at `<verdict>` + `<feedback_directive>`.
3. **Orchestrator parses all blocks programmatically** — it doesn't "read" the JSON inside `<verdict>` as prose; it uses `parse_verdict()` and `parse_feedback_directive()` to get structured objects. Natural-language recommendations are for human audit trails, not Orchestrator's decision logic.
4. **Every `task()` call has a matching `review()` call before `done`** (invariant I1). The only exception is `clarify_with_user` — in that case the feature goes to `blocked` or back to `in-progress` pending user input, never `done`.
5. **`<delta_report>` is always the last block in Generator's message** (invariant I4). If missing, the wrapper around `spawn_subagent` (TP-2) injects a system-reminder noting the protocol violation; Reviewer will start with a `quality_issue` bias.
6. **`<feedback_directive>.retry_review=true` always triggers a re-review** (invariant I10). Orchestrator's system prompt (TP-4) makes this explicit.

---

## What these examples do NOT cover

| Scenario | Where it's covered |
|---|---|
| Reviewer itself returns malformed output (no `<verdict>` tag) | `run_review()` post-processing returns `verdict=unknown` (invariant I5) — tested in eval case `triangle-protocol-verdict-action-mapping` |
| 3 consecutive non-pass verdicts → escalate | Invariant I9; tested in eval case `triangle-protocol-feedback-retry-bounded` |
| Generator hits `budget.max_turns` | delta_report `status=partial` with `escalations: ["budget_exhausted: max_turns"]` |
| Concurrent features (WIP=1 violation) | Out of protocol scope — `loom/agent/scope.py::check_wip1` handles this separately |
| Subagent template prompts (investigate/refactor/fix-test) | Phase 6 task: extend templates to accept `<feature_card>` + `<scope_envelope>` |

For the full list of edge cases and their enforcement, see `docs/triangle-protocol.md` §8 (Lifecycle invariants).
