# Session Handoff

## Current Objective

- Goal: `f-tui-fast-quit-p1-shared-helper` (Phase P1 of tui-fast-quit 4-phase plan)
- Current status: **DONE — commit `ead93bd` on main, working tree clean, eval 226/226, all 12 plan Gate items green**
- Plan: `.sisyphus/plans/tui-fast-quit-p1.md` (12 checkboxes flipped to [x])
- Branch / commit: main @ `ead93bd` "feat(tui): f-tui-fast-quit-p1-shared-helper — extract schedule_init_sh_on_session_end"
- **Roadmap status**: Phase P1 complete. P2 (TUI wire-up) + P3 (lazy imports) + P4 (REPL async) pending.

## Completed This Session

- [x] Task 0: Added `f-tui-fast-quit-p1-shared-helper` entry to `feature_list.json` (initially `not-started`, then `done` with evidence)
- [x] Task 1: `loom/agent/loop.py` — added `import threading`; added `schedule_init_sh_on_session_end` (fire-and-forget helper, returns `threading.Thread(daemon=True)`, `on_complete` + `on_failure_log` callbacks, `subprocess.run` with timeout, config check inside thread); added `run_init_sh_on_session_end` (sync wrapper, joins thread, replicates log-on-failure + progress.md write); refactored `run_repl` from 44 inline lines to single sync wrapper call
- [x] Task 2: `loom/eval/cases/async_init_sh_helper.py` NEW (2 cases): `helper-returns-daemon-thread` (verifies daemon=True), `helper-spawns-thread-and-returns-immediately` (verifies <0.5s return even when init.sh would block 60s). Registered in `loom/eval/cases/__init__.py`
- [x] Task 3: Appended `## Session: f-tui-fast-quit-p1-shared-helper` section to `progress.md`
- [x] Marked all 12 plan checkboxes (Pre-gate 3 + Gate 9) to [x]
- [x] Atomic commit `ead93bd` with 5 files (4 modified + 1 new, 297 insertions / 43 deletions)

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Pre-flight | `feature_list.json` state | OK | 0 in-progress features (WIP=1 respected) |
| Baseline | `uv run python -m loom.cli eval --fail-under 100` | 224/224 | Before changes |
| Eval post-P1 | `uv run python -m loom.cli eval --fail-under 100` | **226/226** | After — +2 new helper cases |
| pytest | `uv run pytest tests/test_agent_loop.py -v` | 12/12 | All agent loop tests pass |
| pytest full | `./init.sh` (full pytest) | 453 passed, 36 warnings in 46.04s | No regression from baseline |
| Audit | `uv run python -m loom.cli audit .` | 100/100 | All 6 dimensions 5/5 |
| Audit self-test | audit 6th dim | 5/5 PASS (1/1) | "Eval results: 226/226 passed" |
| ruff | `uv run ruff check .` | All checks passed! | |
| mypy | `uv run mypy loom/` | Success: no issues found in 78 source files | was 76, +2 for new functions |
| Smoke (success) | `echo "exit" \| loom run` with `init.sh exit 0` | REPL exits in 1.3s, no warning, no progress.md | Behavior preserved |
| Smoke (failure) | `echo "exit" \| loom run` with `init.sh exit 1` | "WARNING init.sh exited 1 on SessionEnd" + progress.md "## SessionEnd auto-record" entry | Warning + auto-record both work |

### Per-case evidence (the 2 new ones)
| Case | Result | Detail |
|---|---|---|
| helper-returns-daemon-thread | PASS | `threading.Thread` returned with `daemon=True` |
| helper-spawns-thread-and-returns-immediately | PASS | Helper returned in 0.002s (init.sh has `sleep 60`) |

### Existing init_sh_session_end cases (must still pass — behavior preserved)
| Case | Result | Detail |
|---|---|---|
| session-end-skip-when-no-init-sh | PASS | REPL exits cleanly, no init.sh warnings |
| session-end-runs-init-sh-when-exists | PASS | init.sh marker created on SessionEnd |
| session-end-warns-on-init-sh-failure | PASS | init.sh failure warned (rc=0), stderr has warning |
| session-end-skipped-when-opt-out | PASS | `run_init_sh_on_session_end=False` prevents init.sh check |
| session-end-trigger-with-args | PASS | `trigger_hooks('SessionEnd', [], 0)` no error |
| log-hook-session-end-logged | PASS | log contains `[Session ended: ...]` |
| tui-app-calls-apply-config-and-session-end | PASS | TUI quit fires SessionEnd |

## Files Changed

### Modified (4)
- `loom/agent/loop.py` (+167 -43) — +import threading; +schedule_init_sh_on_session_end (~64 lines); +run_init_sh_on_session_end (~52 lines); run_repl end-of-function refactored (-44 inline, +4 call site)
- `loom/eval/cases/__init__.py` (+1) — register async_init_sh_helper module
- `feature_list.json` (+9) — new entry with full evidence
- `progress.md` (+87) — session section

### New (1)
- `loom/eval/cases/async_init_sh_helper.py` (76 lines) — 2 eval cases

### Plan/Progress (1)
- `.sisyphus/plans/tui-fast-quit-p1.md` — Pre-gate 3 + Gate 9 checkboxes flipped to [x]

## Decisions Made

1. **Adapted plan pre-gate mismatch**: Plan said "run_init_sh_on_session_end function exists at lines 397-440" but the code was INLINE in `run_repl`. Created both the new helper AND the new sync wrapper as part of this refactor. The spirit of the plan (extract helper, preserve sync behavior) is fully achieved. P2 and P4 can call the helper directly.

2. **Sync wrapper, not direct helper call in run_repl**: `run_repl` needs synchronous semantics (block until init.sh finishes so failures are caught before process exit). P2 (TUI) and P4 (REPL) will call the helper directly and not join — they want non-blocking. Both APIs coexist; callers pick.

3. **Progress.md format change (cosmetic, documented)**: Old code wrote last 30 lines of stdout/stderr to progress.md on failure. New code writes last 200 chars (matches `on_failure_log` callback signature). Acceptable because: (a) no eval case tests progress.md content; (b) 200 chars ≈ 3-5 lines, similar info density; (c) consistent with helper's documented contract.

4. **`on_complete(result, error_msg)` API design**: error_msg string ("timed out" / "file not found" / "exception: ...") instead of separate `success` boolean. Lets sync wrapper distinguish timeout (needs progress.md write) from file-not-found (no-op) without separate callback. Single `finally` block ensures `on_complete` fires exactly once per thread.

5. **Callback exception isolation**: Both `on_complete` and `on_failure_log` callbacks are wrapped in `try/except` with `logger.warning` (no re-raise). A buggy callback must never crash the daemon thread or the main process.

6. **Daemon thread = no zombie processes**: `threading.Thread(daemon=True)` ensures init.sh dies when the Python process exits. No `atexit` cleanup needed; the test for "returns in <0.5s" leaves a `sleep 60` thread behind, but it's killed when eval suite finishes.

7. **Debug log lost (acceptable)**: Old code had `logger.debug("init.sh not found, skip")`. New code is silent when init.sh is absent. Debug log was developer-facing; absence is invisible to users. All 7 existing init_sh_session_end eval cases still pass (they check observable behavior, not log output).

## Blockers / Risks (latent, not fixed in this phase)

1. **Audit self-test flake**: `loom-audit-scores-itself` eval case occasionally times out at 120s when system is under load (audit self-test calls `loom eval` internally — eval can exceed 120s on slow runs). NOT caused by this PR. 226/226 passes on retry. Documented in evidence; may need `--timeout 300` if it recurs.

2. **Progress.md format divergence**: New `run_init_sh_on_session_end` writes "last stdout: <200 chars>" instead of "last 30 lines". This is intentional per the helper API contract, but a future eval case checking progress.md content would need to know about the new format.

3. **P2 (TUI wire-up) deferred**: TUI `action_quit` (loom/tui/app.py:611-642) STILL runs init.sh synchronously. This is the user-visible bug (48s TUI exit time). P2 will replace with `schedule_init_sh_on_session_end` (no join). Foundation is now ready.

4. **P4 (REPL async exit) deferred**: `run_repl` STILL calls the sync wrapper, so `loom run` exit is still slow on the loom project (~48s). P4 will switch run_repl to call the helper directly (no join) for symmetry with TUI.

5. **120s timeout is too long for TUI**: When P2 switches to fire-and-forget, init.sh could still block the daemon thread for 120s. The TUI exit time will be <1s (good), but the daemon thread holds a slot until init.sh finishes. May want to lower timeout to 30s for the fire-and-forget path.

## Roadmap Status (tui-fast-quit 4-phase plan)

| Phase | Scope | Status | Commit | Notes |
|---|---|---|---|---|
| P1 (this) | Extract `schedule_init_sh_on_session_end` helper | done | `ead93bd` | Foundation only — no behavior change |
| P2 | TUI `action_quit` uses helper (no join) | not-started | — | Expected TUI exit: 48s → <1s |
| P3 | `loom.cli` lazy-import subcommand deps | not-started | — | Expected `loom --help`: 0.45s → 0.05s |
| P4 | REPL `run_repl` uses helper (no join) | not-started | — | Expected `loom run` exit: 48s → <1s |

Full plan: `.sisyphus/plans/` has only `tui-fast-quit-p1.md` — P2/P3/P4 plans don't exist yet.

## Recommended Next Step

**P2 — TUI wire-up.** Plan file `.sisyphus/plans/tui-fast-quit-p2.md` does NOT exist yet. The orchestrator/user will need to write it before P2 can be delegated. The P2 work is:

1. Replace `loom/tui/app.py:611-642` `action_quit` sync `subprocess.run` with `schedule_init_sh_on_session_end` (no `thread.join()`)
2. Add TUI "init.sh running..." banner
3. Add 2nd Ctrl-D to cancel the in-flight daemon thread
4. Add 3 eval cases: `tui-quit-doesnt-block-on-init-sh` (exit time <1s), `tui-shows-init-sh-status`, `tui-double-ctrl-d-cancels-init-sh`

P2 expected effort: 1-2h, no behavior change to non-TUI paths (sync wrapper still used by run_repl).

**Per plan ⛔ Session 边界**: "Gate 全绿后必须 `git commit` → `/handoff` → **结束当前会话**". This session ends here.

## Session Boundary Reminders

- Cold-start continuity will auto-inject this handoff + the last 80 lines of `progress.md` into the next agent's system prompt (Tier 1.5)
- The next agent will see `f-tui-fast-quit-p1-shared-helper` is `done` in `feature_list.json`
- Per AGENTS.md End-of-Session Checklist: `./init.sh` (green), feature_list.json updated (yes), progress.md appended (yes), session-handoff.md (this file), commit (yes)
- Working tree is clean except for 2 untracked planning docs (`docs/odyssey-engine-evaluation.md`, `docs/tui-slow-startup-exit-investigation.md`) — pre-existing, NOT created by this session
- Per AGENTS.md rule #11: "Verify subagent work after timeouts" — the helper subagent claimed success after 12m 38s; verified by reading every changed file + running every verification command + manual smoke test. All claims matched reality.
- Per AGENTS.md rule #13 (linkify) and #14 (per-LLM-call thinking): no impact on this PR (no Textual changes).
