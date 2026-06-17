"""Three-tier context loading.

Tier 1 (always loaded, ~500 tokens): feature list summary + memory index
Tier 2 (loaded on activation, ~2000 tokens): AGENTS.md + relevant skill bodies
Tier 3 (on demand via tool call): topic docs (docs/architecture.md, etc.)

The cap constants are enforced by ``truncate_to_tokens`` which uses the
word-based heuristic from ``loop.memory.store.token_count``.
"""

from __future__ import annotations

import json
from pathlib import Path

from loop.memory.paths import DEFAULT_WORKDIR, memory_file
from loop.memory.store import token_count

TIER1_TOKEN_BUDGET = 500
TIER2_TOKEN_BUDGET = 2000
COMBINED_BUDGET = TIER1_TOKEN_BUDGET + TIER2_TOKEN_BUDGET

TIER1_HEADER = "## Tier 1 — Always Loaded"
TIER2_HEADER = "## Tier 2 — Activation"


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    if token_count(text) <= max_tokens:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    used = 0
    for line in lines:
        line_tokens = token_count(line)
        if used + line_tokens > max_tokens:
            break
        kept.append(line)
        used += line_tokens
    kept.append("")
    kept.append(f"... [truncated at {max_tokens} tokens; call memory_search for the rest]")
    return "\n".join(kept)


def _feature_status_summary(workdir: Path) -> str:
    fl = workdir / "feature_list.json"
    if not fl.exists():
        return ""
    try:
        data = json.loads(fl.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    features = data.get("features", [])
    if not isinstance(features, list):
        return ""
    counts: dict[str, int] = {}
    for f in features:
        status = f.get("status", "unknown") if isinstance(f, dict) else "unknown"
        counts[status] = counts.get(status, 0) + 1
    parts = [f"# Feature Status ({len(features)} total)"]
    for status in ("done", "in-progress", "blocked", "not-started"):
        if counts.get(status):
            parts.append(f"- {counts[status]} {status}")
    active = [f for f in features if isinstance(f, dict) and f.get("status") == "in-progress"]
    if active:
        f0 = active[0]
        parts.append(f"- Active: {f0.get('id', '?')} — {f0.get('name', '?')}")
    return "\n".join(parts)


def _memory_index(memory_path: Path, max_lines: int = 30) -> str:
    if not memory_path.exists():
        return "# Memory\n(no MEMORY.md yet)"
    text = memory_path.read_text(encoding="utf-8")
    return "# Memory Index\n" + "\n".join(text.splitlines()[:max_lines])


def load_tier1(workdir: Path = DEFAULT_WORKDIR) -> str:
    sections = [TIER1_HEADER]
    feature_summary = _feature_status_summary(workdir)
    if feature_summary:
        sections.append(feature_summary)
    memory_idx = _memory_index(memory_file(workdir))
    sections.append(memory_idx)
    text = "\n\n".join(sections)
    return truncate_to_tokens(text, TIER1_TOKEN_BUDGET)


def load_tier2(workdir: Path = DEFAULT_WORKDIR) -> str:
    sections = [TIER2_HEADER]
    agents = workdir / "AGENTS.md"
    if agents.exists():
        sections.append(f"# AGENTS.md\n{agents.read_text(encoding='utf-8')}")
    claude = workdir / "CLAUDE.md"
    if claude.exists():
        sections.append(f"# CLAUDE.md\n{claude.read_text(encoding='utf-8')}")
    text = "\n\n".join(sections)
    return truncate_to_tokens(text, TIER2_TOKEN_BUDGET)


def load_tier3(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def combined_tier1_tier2(workdir: Path = DEFAULT_WORKDIR) -> str:
    combined = load_tier1(workdir) + "\n\n" + load_tier2(workdir)
    return truncate_to_tokens(combined, COMBINED_BUDGET)
