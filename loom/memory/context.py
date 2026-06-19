"""Three-tier context loading.

Tier 1 (always loaded, ~500 tokens): feature list summary + memory index
Tier 1.5 (always loaded, ~800 tokens): session-handoff.md + last 80 lines of progress.md
Tier 2 (loaded on activation, ~2000 tokens): AGENTS.md + relevant skill bodies
Tier 3 (on demand via tool call): topic docs (docs/architecture.md, etc.)

The cap constants are enforced by ``truncate_to_tokens`` which uses the
word-based heuristic from ``loom.memory.store.token_count``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loom.memory.paths import DEFAULT_WORKDIR, memory_file
from loom.memory.store import token_count

TIER1_TOKEN_BUDGET = 500
TIER15_TOKEN_BUDGET = 800
TIER2_TOKEN_BUDGET = 2000
COMBINED_BUDGET = TIER1_TOKEN_BUDGET + TIER15_TOKEN_BUDGET + TIER2_TOKEN_BUDGET

TIER1_HEADER = "## Tier 1 — Always Loaded"
TIER15_HEADER = "## Tier 1.5 — Session Continuity"
TIER2_HEADER = "## Tier 2 — Activation"

# Tier 1.5 knobs (all exposed for tests + future tuning)
_PROGRESS_TAIL_LINES = 80
_HANDOFF_MAX_CHARS = 1500
_SUBSTANTIVE_MIN_CHARS = 30
# Match only EMPTY bullets / headers (whitespace + marker + nothing else),
# NOT bullet lines that have substantive content. `- ` matches (empty);
# `- Finished` does NOT match (has content after marker).
_MARKDOWN_BULLET_RE = re.compile(r"^\s*(?:[-*]\s*|\d+\.\s*)")
_MARKDOWN_HEADER_RE = re.compile(r"^\s*#+\s+")
_MARKDOWN_HR_RE = re.compile(r"^\s*-{3,}\s*$")


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


def _is_substantive(text: str) -> bool:
    """Return True if text has more than _SUBSTANTIVE_MIN_CHARS of body content.

    Strips structural markdown (bullets, numbers, headers, hr) and counts the
    remaining body chars. ``- Finished`` and ``## Last task`` both contribute
    their content after the marker. Empty templates (headers + empty bullets
    + no real body text) yield 0 body chars and are correctly skipped.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_chars: list[str] = []
    for s in lines:
        if _MARKDOWN_HR_RE.match(s):
            continue
        stripped = _MARKDOWN_BULLET_RE.sub("", s)
        stripped = _MARKDOWN_HEADER_RE.sub("", stripped).strip()
        if not stripped:
            continue
        body_chars.append(stripped)
    body = " ".join(body_chars)
    meaningful = sum(1 for ch in body if ch.isalpha() or ch.isdigit())
    return meaningful > _SUBSTANTIVE_MIN_CHARS


def load_session_continuity(workdir: Path = DEFAULT_WORKDIR) -> str:
    """Build the Tier 1.5 'where did we leave off?' section.

    Reads:
    - session-handoff.md (full content if substantive, truncated to _HANDOFF_MAX_CHARS)
    - progress.md (last _PROGRESS_TAIL_LINES lines if it exists)

    Returns ``""`` when neither file has meaningful content. Otherwise returns
    a Tier 1.5 block truncated to TIER15_TOKEN_BUDGET tokens.

    Fail-closed: any exception in the read/parse pipeline is logged and treated
    as 'file absent' — the caller gets ``""`` rather than a broken Tier 1.5.
    """
    handoff_path = workdir / "session-handoff.md"
    progress_path = workdir / "progress.md"
    handoff_text = ""
    progress_text = ""

    try:
        if handoff_path.exists():
            raw = handoff_path.read_text(encoding="utf-8")
            if _is_substantive(raw):
                handoff_text = raw[:_HANDOFF_MAX_CHARS]
    except Exception:
        handoff_text = ""

    try:
        if progress_path.exists():
            lines = progress_path.read_text(encoding="utf-8").splitlines()
            tail = lines[-_PROGRESS_TAIL_LINES:]
            progress_text = "\n".join(tail)
    except Exception:
        progress_text = ""

    if not handoff_text and not progress_text:
        return ""

    sections = [TIER15_HEADER]
    if handoff_text:
        sections.append("### session-handoff.md\n" + handoff_text)
    if progress_text:
        sections.append(f"### progress.md (last {_PROGRESS_TAIL_LINES} lines)\n" + progress_text)
    return truncate_to_tokens("\n\n".join(sections), TIER15_TOKEN_BUDGET)


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
