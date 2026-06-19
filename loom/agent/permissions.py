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


def _canonicalize(command: str) -> str:
    """Decode a single layer of Python ``unicode_escape`` sequences.

    Used to neutralize trivial obfuscation in deny-pattern matching, e.g.
    ``printf '\\x72\\x6d\\x20-rf\\x20/'`` which ``unicode_escape`` turns
    into ``rm -rf /``. Single-pass only — recursive decoding would
    re-introduce the bypass-via-nesting attack. On malformed escapes
    (e.g. ``\\xZZ``), return the original string unchanged so the
    permission check stays safe-fail rather than raising.
    """
    try:
        return command.encode().decode("unicode_escape")
    except UnicodeDecodeError:
        return command


@dataclass(frozen=True)
class PermissionPolicy:
    deny_patterns: tuple[str, ...]
    rules: tuple[PermissionRule, ...] = field(default_factory=tuple)

    def matches_deny(self, command: str) -> str | None:
        canonical = _canonicalize(command)
        for pattern in self.deny_patterns:
            if pattern in canonical:
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
        # Original 9 (Task 2 baseline)
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        "> /dev/sda",
        "base64 -d|",
        "base64 --decode|",
        # Task 3 — network exfiltration (trailing space avoids curl-config etc.)
        # Ordering matters: rsync/scp/netcat listed before nc because
        # ``rsync `` contains ``nc `` as a substring and would otherwise
        # match first.
        "curl ",
        "wget ",
        "rsync ",
        "scp ",
        "ssh ",
        "netcat ",
        "nc ",
        # Task 3 — in-process code execution (only `-c` form, avoids python --version)
        "python -c ",
        "python3 -c ",
        "perl -e ",
        "ruby -e ",
        "bash -c ",
        # Task 3 — root escalation
        "su -",
        "su root",
        "pkexec ",
        "doas ",
        # Task 3 — destructive system ops
        "kill -9 1",
        "halt",
        "poweroff",
        "init 0",
        "fdisk",
        # Task 3 — fork bomb
        ":(){ ",
        # Task 3 — hex-escape printf fallback (catches malformed \x that
        # survives canonicalize; for valid \xHH the existing rm -rf / etc.
        # patterns catch the decoded form)
        "printf '\\x",
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
