"""Persistent permission decisions.

Stores "allow always" decisions from the PermissionScreen modal
in a JSON file at .minicode/permissions.json. Decisions expire
after a configurable TTL (default 30 days) so long-forgotten
grants don't accumulate.

Key format: f"{tool_name}::{canonical_pattern}".
The canonical_pattern is a normalized form of the input args
(e.g. for bash: the command string with whitespace normalized).

Special case: workspace-write/edit decisions are NEVER persisted
— those are always re-prompted to avoid silent file damage.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)


WORKSPACE_WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "edit_lines"})


@dataclass
class PermissionGrant:
    tool: str
    pattern: str
    granted_at: float
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at


def _key(tool: str, pattern: str) -> str:
    return f"{tool}::{pattern}"


def _canonicalize_pattern(tool: str, args: dict) -> str:
    """Build a stable key from tool + args.

    For bash: the command string with whitespace collapsed.
    For read_file/grep/glob: the path with WORKDIR stripped.
    For everything else: JSON-serialized sorted args.
    """
    if tool == "bash":
        cmd = args.get("command", "")
        return re.sub(r"\s+", " ", cmd).strip()
    if tool in ("read_file", "grep", "glob", "edit_file", "edit_lines", "multi_edit",
                "write_file"):
        path = args.get("path", args.get("pattern", ""))
        return str(path).strip()
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def _store_path(workdir: Path) -> Path:
    return workdir / ".minicode" / "permissions.json"


def load_grants(workdir: Path) -> dict[str, PermissionGrant]:
    path = _store_path(workdir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("permissions.json unreadable: %s", exc)
        return {}
    grants: dict[str, PermissionGrant] = {}
    now = time.time()
    for key, entry in raw.items():
        g = PermissionGrant(
            tool=entry.get("tool", "?"),
            pattern=entry.get("pattern", "?"),
            granted_at=entry.get("granted_at", 0.0),
            expires_at=entry.get("expires_at", 0.0),
        )
        if not g.is_expired(now):
            grants[key] = g
    return grants


def save_grants(workdir: Path, grants: dict[str, PermissionGrant]) -> None:
    path = _store_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: asdict(v) for k, v in grants.items()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def grant(workdir: Path, tool: str, args: dict, ttl_days: int = 30) -> PermissionGrant | None:
    """Record an "allow always" decision. Returns None for tools
    that should never be auto-allowed (workspace-write tools)."""
    if tool in WORKSPACE_WRITE_TOOLS:
        return None
    pattern = _canonicalize_pattern(tool, args)
    now = time.time()
    g = PermissionGrant(
        tool=tool,
        pattern=pattern,
        granted_at=now,
        expires_at=now + ttl_days * 86400,
    )
    grants = load_grants(workdir)
    grants[_key(tool, pattern)] = g
    save_grants(workdir, grants)
    return g


def is_granted(workdir: Path, tool: str, args: dict) -> bool:
    if tool in WORKSPACE_WRITE_TOOLS:
        return False
    pattern = _canonicalize_pattern(tool, args)
    grants = load_grants(workdir)
    return _key(tool, pattern) in grants


def revoke(workdir: Path, tool: str, args: dict) -> bool:
    pattern = _canonicalize_pattern(tool, args)
    grants = load_grants(workdir)
    key = _key(tool, pattern)
    if key in grants:
        del grants[key]
        save_grants(workdir, grants)
        return True
    return False


def list_grants(workdir: Path) -> list[PermissionGrant]:
    return list(load_grants(workdir).values())
