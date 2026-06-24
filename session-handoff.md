# Session Handoff — PI-2 Complete

**Date**: 2026-06-24
**Session IDs**: ses_107a01a3cffesYkSSbMPBUzjR1
**Completed**: Phase PI-2 — `f-init-sh-two-tier-polish`

## What was done

1. **`loom/detect.py`** — Python `verification_plan()` detects `[tool.ruff]`/`[tool.mypy]` in pyproject.toml via string containment, prepends `ruff check .`/`mypy .` to both quick/full tiers. Generic stack returns 3-step skeleton (tests/lint/build) with TODO placeholders.

2. **`loom/init_cmd.py`** — `_maybe_inject_pytest_markers()` appends slow/snapshot/integration markers to pyproject.toml when `[tool.pytest.ini_options]` absent. Conservative skip when section exists.

3. **`docs/init-sh.md`** — ~120 lines, 7 sections covering two-tier usage, customization, markers, troubleshooting.

4. **`tests/test_init_sh_polish.py`** — 13 tests covering detection, skeleton, injection, docs.

5. **`loom/eval/cases/init_sh_polish.py`** — 3 eval cases (ruff detection, skeleton, marker skip), registered in `__init__.py`.

6. **README** — "Two-tier init.sh" bullet in "What loom does well".

## Verification

- `uv run pytest tests/test_init_sh_polish.py` → 13/13 ✅
- `uv run python -m loom.cli eval --filter init-sh-polish --fail-under 100` → 3/3 ✅
- `uv run python -m loom.cli eval --filter init-sh --fail-under 100` → 16/16 ✅
- `ruff check .` + `mypy loom/` → clean ✅

## Next Steps (per plan)

- Run `loom init` in a real downstream project to validate
- Run `loom audit .` to check score doesn't drop
- The umbrella feature `f-init-sh-two-tier` can be marked done in a separate commit

## Working Tree Note

The working tree has ~50 dirty files from other feature work (multi-model, TUI, review tool, etc.) — these are unrelated to PI-2.
