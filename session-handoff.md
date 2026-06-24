HANDOFF CONTEXT
===============

USER REQUESTS (AS-IS)
---------------------
- Phase PI-1: f-init-sh-two-tier-core — init.sh 两档核心.

GOAL
----
Complete Phase PI-1 VerificationPlan + MODE flag + verify-quick.sh. Then start new session for Phase PI-2.

WORK COMPLETED
--------------
- Added `VerificationPlan` frozen dataclass to `detect.py` (quick/full tuples + all_commands property for back-compat)
- Added `verification_plan()` per-stack two-tier plan function (python/go/rust/java-maven/java-gradle/dotnet/node/generic)
- Extracted `_node_or_generic_commands()` helper from verification_commands()
- Modified `init_script_content()` to accept VerificationPlan and generate MODE-flag two-tier bash script (quick|full case dispatch)
- Modified `_render_init_sh()` to use verification_plan()
- Added `_render_verify_quick_sh()` generating `scripts/verify-quick.sh` with git-diff auto-scope
- `init()` now writes scripts/verify-quick.sh (executable, skip-if-exists, force flag)
- Created `tests/test_init_sh_two_tier.py` (145 lines, 20 tests, 5 classes: VerificationPlan, per-stack plans, init_script_content, init cmd, back-compat)
- Created `loom/eval/cases/init_sh_two_tier.py` (121 lines, 4 eval cases)
- Registered eval cases in __init__.py
- Updated `feature_list.json` with feature as `done` + evidence
- Updated `progress.md` with session summary

CURRENT STATE
-------------
- All gates passed: 64/64 pytest (detect + init_cmd + init_sh_two_tier)
- `loom eval --filter init-sh-two-tier --fail-under 100` -> 4/4 PASS
- `ruff check` 0 errors, `mypy loom/` 0 issues
- Manual smoke: `loom init /tmp/pi1-test` -> init.sh with MODE flag + scripts/verify-quick.sh both created and functional
- `feature_list.json`: f-init-sh-two-tier-core = done with evidence
- `./init.sh` — 18 pre-existing failures from OTHER features' uncommitted changes (provider/MCP/model/TUI). Not caused by this feature.
- Commit: 4f75d51 on main — `feat(init): PI-1 init.sh 两档核心 — VerificationPlan + MODE flag + verify-quick.sh`

PENDING TASKS
-------------
- Phase PI-1 is 100% complete. All gates green.
- Next: Open new session and load `.sisyphus/plans/loop-init-sh-p2.md` (create from scratch if not present)
- The plan explicitly does NOT forbid loading P2 in the same session (loom convention), but a clean session is recommended

KEY FILES
---------
- `loom/detect.py` — MODIFIED: VerificationPlan frozen dataclass + verification_plan() + _node_or_generic_commands() + init_script_content(plan)
- `loom/init_cmd.py` — MODIFIED: _render_verify_quick_sh() + _render_init_sh() uses verification_plan() + init() writes verify-quick.sh
- `tests/test_init_sh_two_tier.py` — NEW: 20 tests in 5 classes
- `loom/eval/cases/init_sh_two_tier.py` — NEW: 4 eval cases
- `loom/eval/cases/__init__.py` — MODIFIED: registered init_sh_two_tier

IMPORTANT DECISIONS
-------------------
- `VerificationPlan` uses `frozen=True` + tuple fields (matches existing HarnessConfig style)
- `verification_commands()` preserved unchanged signature — delegates to `verification_plan().all_commands` for full backward compat
- Python quick tier uses `-x` (fail-fast) + `-q` (quiet) + `-m 'not slow and not snapshot'` (marker filter, pytest warns but doesn't crash if markers absent) + `--tb=short`
- Go quick tier uses `go test -count=1 -run Unit ./...`, Rust uses `cargo test --lib --quiet`
- MODE dispatch: positional arg `$1` overrides env var `MODE`; default `full` (backward compat)
- `scripts/verify-quick.sh` uses `git diff --name-only HEAD 2>/dev/null || true` to avoid crash in non-git repos
- `_write()` handles `parent.mkdir(parents=True)` for scripts/ directory creation
- Scripts chmod 755 (executable)

EXPLICIT CONSTRAINTS
--------------------
- Phase PI-2 will add Python ruff/mypy auto-detection + marker config injection (NOT done in PI-1)
- `_node_or_generic_commands()` generic placeholder logic still needs PI-2 refinement
- No Windows .bat/.ps1 equivalents created (Unix-only, documented in comments)
- The verify-quick.sh does NOT embed loom eval filter (loom-specific, downstream projects don't have loom eval)

CONTEXT FOR CONTINUATION
------------------------
- `verification_commands()` is now a thin wrapper around `verification_plan().all_commands`
- `init_script_content()` takes `VerificationPlan` instead of `list[str]`
- `_node_or_generic_commands()` extracts node/generic logic (used by both old and new paths)
- PI-2 planned scope: Python ruff/mypy auto-detection in detect.py + marker config injection in init.sh templates
- Pre-existing uncommitted changes: 40+ modified files from other in-progress features (provider/MCP/credential/TUI changes, plus 5 untracked files) remain in working tree. Next session should be careful about this.
- To run verification: `uv run pytest tests/test_detect.py tests/test_init_cmd.py tests/test_init_sh_two_tier.py -v` (64/64) and `uv run python -m loom.cli eval --filter init-sh-two-tier --fail-under 100` (4/4)
