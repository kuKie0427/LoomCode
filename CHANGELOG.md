# Changelog

All notable changes to **loom** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Project rename: `loop` → `loom`**. The package was renamed from `loop/` to `loom/` across all 65 Python files (preserved as git renames, 82-100% similarity). The TUI design language doc and brand-identity sheet drove the new name: `loom` captures the agent's role of *weaving* user intent, tool calls, and model responses into coherent output, where `loop` was a generic iteration noun. The name matches the project's "长时循环美感" (long-loop aesthetic) — the agent in sustained, quiet, craft motion.
  - Package directory: `loop/` → `loom/` (`git mv` preserves history)
  - All Python imports: `from loop.X` → `from loom.X`, `import loop.X` → `import loom.X`
  - CLI program name: `python -m loop.cli` → `python -m loom.cli`
  - `pyproject.toml`: `name = "loom"`, entry point `loom = "loom.cli:main"`, `packages = ["loom"]`
  - Version bump: `0.1.0` → `0.2.0`
- **CI workflow (`.github/workflows/ci.yml`)**: eval and audit invocations updated to `loom.cli`. Eval case assertions in `loom/eval/cases/ci.py` updated to match.
- **Documentation**:
  - `AGENTS.md`: Project name, Quick Start commands, Layout table paths — all updated to `loom`
  - `README.md`: brand header now shows the loom logo (SVG), italic wordmark in Cormorant Garamond, tagline "weaving intent into action", and a 1-paragraph project description; Quick Start section added with install/run commands and a link to AGENTS.md
  - `tui-design.html` (7 state mockups): terminal title bars `loop —` → `loom —`; meta title `loop TUI` → `loom TUI`
  - `init.sh`: mypy path `loop/` → `loom/`, log path `/tmp/loop-pytest.log` → `/tmp/loom-pytest.log`, banner string
- **Audit fix (pre-existing, not rename-related)**: `loom/audit_cmd.py:145` was checking for `"Startup Workflow"` / `"Before writing code"` — strings that have never been in the project's `AGENTS.md` (which uses `## Quick Start` instead). This caused `audit .` to score 97/100 since the audit was first added (commit 7dd587e), not from the rename. Updated check to verify `## Quick Start` (which AGENTS.md has). Audit now reports **100/100**.

### Added

- **Brand identity sheet** at `docs/loom-logo.html` (1443 lines, single-file HTML):
  - 10 sections covering primary mark (5-warp × 5-weft weaving apparatus with shuttle + shed indicator + extending trail), wordmark in Cormorant Garamond italic, horizontal + vertical lockups, icon variant (3×3 hash, 16/32/64px), 6 color treatments, construction grid (200×200 unit square, 25-unit spacing), pattern tile (80×80 units, edge-to-edge), 4 real-world mockups (README, terminal title bar, CLI startup banner, browser-tab favicons), don't/do anti-patterns
  - Tagline: **"weaving intent into action"** — chosen over 4 alternatives because "weaving" operates on two levels (literal loom = weaving apparatus, metaphorical agent weaves inputs into outputs)
- **Design language artifact** at `docs/tui-design.html` (1443 lines, 7 state mockups): Empty Layout Grid, Populated Idle State, Active Loop Iteration, PermissionScreen Overlay, ToolCallModal Overlay, Header Collapsed, Header Expanded. Each mockup has annotations citing specific §N rules from the design language doc.
- **Design language spec** at `docs/tui-design-language.md` (318 lines, §0-§7): the long-loop aesthetic (6 rules: bounded re-layout, quiet by default, one anchor per iteration, monotonic scroll, indentation encodes nesting, hard interrupts fill screen). Includes §4.3 Header (summary rail) sub-section defining collapsed/expanded states with click-to-expand overlay panel pattern.
- **Brand assets** at `docs/`: `loom-mark.svg` (2.5KB, 200×200 viewBox, full mark with 6 §L-1 elements), `loom-icon.svg` (929 bytes, simplified 3×3 hash for favicon), `favicon-{16,32,64}.png` (PNG, RGB non-interlaced, correct dimensions).

### Removed

- (none — rename is purely additive in scope; the previous product behavior is preserved)

### Fixed

- (covered under "Changed" — audit pre-existing bug + CI workflow loop.cli → loom.cli)

## [0.1.0] - 2026-06-19

Initial public release as `loop`. (Pre-rename baseline; the project was first published under this name before the design language and brand identity work led to the rename.)

[Unreleased]: https://github.com/user/loom/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/user/loom/releases/tag/v0.1.0
