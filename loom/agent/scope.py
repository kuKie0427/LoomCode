"""Scope subsystem: WIP=1 enforcement.

Machine-enforces AGENTS.md rule 'WIP=1' by warning at CLI entry if
multiple features are in-progress simultaneously. Warn-only (does
not exit) to allow emergency override; matches the SessionEnd
init.sh warn-only design (f-session-end-mandatory-init-sh).
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


def check_wip1(workdir: Path) -> list[str]:
    """Return IDs of active features (in-progress or review-pending). Logs warning if > 1.

    Silent on missing or malformed feature_list.json — never crashes
    the CLI for a guideline check.
    """
    path = Path(workdir) / "feature_list.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    features = data.get("features", [])
    if not isinstance(features, list):
        return []
    active = [
        f.get("id", "<no-id>")
        for f in features
        if isinstance(f, dict) and f.get("status") in ("in-progress", "review-pending")
    ]
    if len(active) > 1:
        logger.warning(
            "WIP=1 violation: {} features in-progress or review-pending: {}. "
            "Finish or move one to 'blocked' before starting another.",
            len(active), active,
        )
    return active
