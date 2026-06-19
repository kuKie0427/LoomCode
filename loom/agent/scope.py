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
    """Return IDs of in-progress features. Logs warning if > 1.

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
    in_progress = [
        f.get("id", "<no-id>")
        for f in features
        if isinstance(f, dict) and f.get("status") == "in-progress"
    ]
    if len(in_progress) > 1:
        logger.warning(
            "WIP=1 violation: {} features in-progress: {}. "
            "Finish or move one to 'blocked' before starting another.",
            len(in_progress), in_progress,
        )
    return in_progress
