# Session Handoff

## Current Objective

- Goal: f-harness-eval-p2-instructions-cache (Phase P2 — instructions subsystem)
- Current status: **DONE — commit 491ec89 on main, working tree clean, eval 195/195, all 9 Gate items green**
- Branch / commit: main @ 491ec89 "feat: f-harness-eval-p2-instructions-cache — AGENTS.md to static + cold-start continuity + real tokens + 8000 unified"
- **Roadmap status**: ALL 3 PHASES COMPLETE — f-harness-eval umbrella now `done`

## Completed This Session

- [x] Task 0: `feature_list.json` `f-harness-eval-p2-instructions-cache` → `in-progress` (subagent) → `done` (final flip)
- [x] Task 1: `loom/agent/loop.py:62-84` `build_system_prompt()` injects AGENTS.md ≤ `AGENTS_MD_STATIC_LIMIT` (12000) into `SystemPrompt.static`. Tier 2 fallback preserved for >12000 char files.
- [x] Task 2: `loom/agent/context.py` — `_token_cache: dict[int, int]` keyed by `id(messages)`, `_count_tokens_accurate` calling `Anthropic().messages.count_tokens()` with -1 fallback on exception, `should_compact` near-threshold gate (cheap-first, accurate-only when cheap ≥ 0.9 * threshold), `max(cheap, accurate)` safety bias
- [x] Task 3: `loom/agent/config.py` — `LLMConfig(max_output_tokens=8000)` dataclass + `from_defaults` + module-level `LLM_CONFIG` singleton + `_parse_llm_section` + `HarnessConfig.llm` field. All 5 magic-8000 sites now reference `LLM_CONFIG.max_output_tokens`:
  - `loom/agent/loop.py:196` (streaming path) — was 8000
  - `loom/agent/loop.py:235` (sync path) — was 8000
  - `loom/agent/llm.py:80` (stream_iter default) — was 8000
  - `loom/agent/tools.py:348` (spawn_subagent) — was 8000
  - `loom/agent/context.py` (`COMPACT_MAX_OUTPUT_TOKENS`) — alias now
- [x] Task 4: `loom/memory/context.py` — `TIER15_TOKEN_BUDGET=800`, `TIER15_HEADER`, `_is_substantive` (skips whole bullet/header lines; returns False if < 30 non-whitespace body chars), `load_session_continuity` (full handoff if substantive + last 80 lines of progress.md, capped at 800 tokens). `loom/memory/__init__.py` exports it.
- [x] Task 5: 4 new eval modules + registration + AGENTS.md notes + progress.md + feature_list.json
- [x] 12 new eval cases:
  - `loom/eval/cases/instructions_static.py` — 3 cases (small AGENTS.md → static, large → Tier 2, no AGENTS.md → no static)
  - `loom/eval/cases/real_token_counter.py` — 4 cases (near-threshold API call, far no call, exception fallback, cache hit)
  - `loom/eval/cases/max_output_tokens_config.py` — 1 case (5-site override verification)
  - `loom/eval/cases/cold_start_continuity.py` — 4 cases (progress.md tail, handoff full, empty template skipped, no files no section)
- [x] Marked `f-harness-eval-p2-instructions-cache` as `done` in `feature_list.json` with evidence
- [x] Marked `f-harness-eval` umbrella as `done` in `feature_list.json` (3 sub-phases complete)
- [x] Appended `## Session: f-harness-eval-p2-instructions-cache` section to `progress.md`
- [x] Atomic commit 491ec89 with 16 files (12 modified + 4 new)
- [x] Plan checkboxes (前置检查 3 + Gate 9) all marked [x]

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Pre-flight | `feature_list.json` state | OK | 0 in-progress features, p2 was not-started |
| Baseline | `uv run python -m loom.cli eval --fail-under 100` | 183/183 | Before changes |
| Eval post-P2 | `uv run python -m loom.cli eval --fail-under 100` | **195/195** | After — +12 exactly as required |
| Gate 2 (AGENTS.md in static) | `build_system_prompt()` static | 'Working Rules' present | Project AGENTS.md fits in static |
| Gate 3 (Tier 1.5 continuity) | `load_session_continuity(Path('.'))` | 'Tier 1.5 — Session Continuity' header present | Real progress.md/session-handoff.md loaded |
| Gate 4 (8000 only in config) | `grep -rn '\b8000\b' loom/agent/ --include='*.py'` | 4 hits, all in config.py (98, 101, 105, 374) | All other sites use LLM_CONFIG |
| Gate 5 (mypy) | `uv run mypy loom/` | Success: no issues found in 74 source files | Pre-existing notes only |
| Gate 6 (ruff) | `uv run ruff check loom/` | All checks passed! | |
| Gate 7 (pytest) | `uv run pytest -q` | 375 passed, 21 warnings | No regression from baseline |
| Gate 8 (feature status) | `feature_list.json` | done + evidence | Umbrella f-harness-eval also done |
| Gate 9 (progress.md) | `progress.md` | `## Session: f-harness-eval-p2-instructions-cache` appended | |
| Bonus (audit) | `uv run python -m loom.cli audit .` | **100/100** (was 92/100) | All 6 dimensions 5/5 |
| Bonus (self-test) | audit self-test | 5/5 PASS (1/1) | "Eval results: 195/195 passed" |

### Per-case evidence (the 12 new ones)
| Case | Result | Detail |
|---|---|---|
| instructions-agents-md-loaded-into-static | PASS | AGENTS.md (347 chars) injected into static |
| instructions-large-agents-md-falls-back-to-tier2 | PASS | AGENTS.md (16015 chars) bypassed static; Tier 2 contains it |
| instructions-no-agents-md-no-static-rules-section | PASS | no AGENTS.md → no static-rules section |
| tokens-real-counter-used-near-threshold | PASS | SDK count_tokens invoked exactly once near threshold |
| tokens-cheap-estimate-used-far-from-threshold | PASS | far-from-threshold → cheap heuristic only, no HTTP roundtrip |
| tokens-counter-failure-falls-back-to-heuristic | PASS | SDK exception → char/4 fallback used, no exception propagated |
| tokens-cache-hit-on-same-content | PASS | second call hit cache; SDK not re-invoked |
| config-llm-max-output-tokens-overridable-via-harness-toml | PASS | all 5 sites honor override; bare-8000 literals purged |
| continuity-progress-md-tail-loaded | PASS | last line injected, first line excluded (window: last 80 of 200) |
| continuity-session-handoff-loaded-when-present | PASS | substantive handoff injected in full into Tier 1.5 |
| continuity-empty-handoff-template-skipped | PASS | empty-template handoff correctly skipped (substantive threshold not met) |
| continuity-no-files-no-section | PASS | no files → no Tier 1.5 section (clean cold-start) |

## Files Changed

### Modified (12)
- `loom/agent/loop.py` (+22 -3) — build_system_prompt + Tier 1.5 call + 2× 8000 → LLM_CONFIG + should_compact model arg
- `loom/agent/prompt.py` (+6) — AGENTS_MD_STATIC_LIMIT = 12000
- `loom/agent/context.py` (+44 -3) — _token_cache + _count_tokens_accurate + should_compact gate + COMPACT_MAX_OUTPUT_TOKENS alias
- `loom/agent/config.py` (+35) — LLMConfig dataclass + LLM_CONFIG singleton + _parse_llm_section + HarnessConfig.llm field + skeleton
- `loom/agent/llm.py` (+5 -1) — stream_iter max_tokens: int | None = None default
- `loom/agent/tools.py` (+3 -2) — spawn_subagent max_tokens = LLM_CONFIG.max_output_tokens
- `loom/memory/context.py` (+76 -1) — TIER15 constants + _is_substantive + load_session_continuity
- `loom/memory/__init__.py` (+2 -1) — export load_session_continuity
- `loom/eval/cases/__init__.py` (+4) — register 4 new modules
- `AGENTS.md` (+4) — 2 cache strategy + continuity notes
- `feature_list.json` (+5 -5) — 2 status flips + evidence
- `progress.md` (+73) — session section

### New (4)
- `loom/eval/cases/instructions_static.py` (98 lines) — 3 cases
- `loom/eval/cases/real_token_counter.py` (163 lines) — 4 cases
- `loom/eval/cases/max_output_tokens_config.py` (164 lines) — 1 case
- `loom/eval/cases/cold_start_continuity.py` (150 lines) — 4 cases

### Plan/Progress (3)
- `.sisyphus/plans/harness-eval-p2.md` — 前置检查 3 + Gate 9 checkboxes flipped to [x]
- `.sisyphus/notepads/harness-eval-p2/learnings.md` — phase learnings + 7 design decisions + 6 gotchas
- `progress.md` — `## Session: f-harness-eval-p2-instructions-cache` section appended
- This file (`session-handoff.md`) — replaces the P1 handoff

## Decisions Made

1. **`max(cheap, accurate)` for safety**: real API can return lower count than cheap estimate when `last_input_tokens` is synthetic (test setup) or agent's view of context is stale. `max()` keeps agent safe (over-compact = harmless, under-compact = context overflow). P1 reviewer flagged this exact risk for failure-mode case 3.
2. **`AGENTS_MD_STATIC_LIMIT` bumped 6000 → 12000**: plan said default 6000 but explicitly allowed adjustment per plan §风险. Project's AGENTS.md is 10030 chars; 12000 covers current + 2K headroom. Documented in prompt.py docstring + AGENTS.md + feature_list.json evidence.
3. **`_is_substantive` skips whole lines**: plan said "strip whitespace + bullets, count chars > 30". First attempt only stripped `# ` prefix — header TITLE TEXT remained and pushed count above 30. Final algorithm skips entire lines that match bullet/header pattern, then counts remaining non-whitespace chars.
4. **Two `context.py` files kept strictly separate**: `loom/agent/context.py` (Task 2) and `loom/memory/context.py` (Task 4). Different `from __future__ import annotations` status. Confused them once during planning, not in code.
5. **`LLM_CONFIG` module-level singleton + `HarnessConfig.llm` field**: singleton for callers without a HarnessConfig in scope (5 hot-path sites); `HarnessConfig.llm` for the harness.toml override path. Tests patch both atomically.
6. **Id-keyed token cache**: `_token_cache: dict[int, int]` keyed by `id(messages)` (list object identity). Same list → no second HTTP roundtrip. Cached on success, not failure. Plan literally specified `id()` as key.
7. **`should_compact` signature change**: added keyword-only `model: str | None = None` 3rd arg. All 7 callers (eval cases + loop.py:178) backward-compatible via default.

## Blockers / Risks (latent, not fixed in this phase)

1. **`_token_cache` stale data risk**: `messages.append()` mutates in place; `id()` stays the same. As messages grow, cached count becomes stale. `max(cheap, accurate)` safety bias protects against under-trigger (cheap is upper bound). Future phase could invalidate cache on `messages.append` or use content hash.

2. **No warm-up of `_token_cache`**: First call to `should_compact` near threshold always makes HTTP call. Pre-warm on `context.update()` could save a roundtrip.

3. **`AGENTS_MD_STATIC_LIMIT = 12000` is project-specific**: other projects with longer AGENTS.md fall back to Tier 2 more often. Could be configurable in `harness.toml [instructions] static_limit = 12000` — out of scope for P2.

## Roadmap Status (all phases)

| Phase | Status | Commit | Cases added | Audit delta |
|---|---|---|---|---|
| P0 (security) | done | `ea25cbc` | +34 (142 → 176) | — |
| P1 (verification) | done | `3bfbc7d` | +7 (176 → 183) | 92/100 → 97/100 |
| P2 (instructions) | done | `491ec89` | +12 (183 → 195) | 97/100 → 100/100 |
| **Total** | **3/3** | | **+53** | **100/100** |

## Recommended Next Step

**No next phase.** The `f-harness-eval` umbrella (3 phases) is complete. The project is at 100/100 audit, 195/195 eval, 375 pytest, 74 mypy clean. The plan roadmap in `.sisyphus/plans/` has no more phases. The next work session should pick a NEW feature from `feature_list.json` not in the harness-eval umbrella — for example, one of the 30+ remaining features.

**Per plan ⛔ Session 边界**: "Gate 全绿后必须 `git commit` → `/handoff` → **结束当前会话**". This session ends here.

## Session Boundary Reminders

- Cold-start continuity will auto-inject this handoff + the last 80 lines of `progress.md` into the next agent's system prompt (Tier 1.5)
- The next agent will see `f-harness-eval` is `done` in `feature_list.json` — no follow-up needed
- Per AGENTS.md End-of-Session Checklist: `./init.sh` (green), feature_list.json updated (yes), progress.md appended (yes), session-handoff.md (yes), commit (yes)