"""Permission policy — single source of truth for "should this tool call be blocked?".

Three earlier layers drifted from each other:
- run_bash's hardcoded `dangerous` list (5 items) in loom/agent/tools.py
- Hooks.DENY_LIST (7 items) in loom/agent/hooks.py
- Hooks.PERMISSION_RULES (2 rules) in loom/agent/hooks.py

This module consolidates them. Both `run_bash` (direct invocation path) and
`Hooks.check_permission_hook` (LLM tool-use path) read from the same
`DEFAULT_POLICY`, so a dangerous pattern either blocks everywhere or blocks
nowhere.

Two policy components, distinct semantics:

- `deny_patterns` — auto-deny any tool input matching a substring.
- `rules` — when matched, prompt the user (y/N). `_ask_user` lives on Hooks.

Future `harness.toml` per-project overrides can replace `DEFAULT_POLICY`
at startup; the dataclass is the natural injection point.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

WORKDIR = Path.cwd()


@dataclass(frozen=True)
class PermissionRule:
    tools: tuple[str, ...]
    check: Callable[[dict], bool]
    message: str


@dataclass(frozen=True)
class PermissionPolicy:
    deny_patterns: tuple[str, ...]
    rules: tuple[PermissionRule, ...] = field(default_factory=tuple)

    def matches_deny(self, command: str) -> str | None:
        for pattern in self.deny_patterns:
            if pattern in command:
                return pattern
        return None

    def find_rule(self, tool_name: str, args: dict) -> PermissionRule | None:
        for rule in self.rules:
            if tool_name in rule.tools and rule.check(args):
                return rule
        return None


def _outside_workspace(args: dict) -> bool:
    raw = args.get("path", "")
    if not raw:
        return False
    try:
        return not (WORKDIR / raw).resolve().is_relative_to(WORKDIR)
    except (OSError, ValueError):
        return True


def _destructive_bash(args: dict) -> bool:
    cmd = args.get("command", "")
    return any(kw in cmd for kw in ("rm ", "> /etc/", "chmod 777"))


DEFAULT_POLICY = PermissionPolicy(
    deny_patterns=(
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        "> /dev/sda",
    ),
    rules=(
        PermissionRule(
            tools=("write_file", "edit_file"),
            check=_outside_workspace,
            message="Writing outside workspace",
        ),
        PermissionRule(
            tools=("bash",),
            check=_destructive_bash,
            message="Potentially destructive command",
        ),
    ),
)
