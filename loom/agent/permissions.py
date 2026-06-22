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


def _lsp_rename_outside_workspace(args: dict) -> bool:
    """Permission rule for ``lsp_rename_symbol`` — invoked in TWO passes.

    Pass 1 (PreToolUse, automatic via ``Hooks.check_permission_hook``): the
    handler has not yet called the LSP server, so ``_resolved_files`` is
    absent. We fall back to ``_outside_workspace`` on the entry ``path``.

    Pass 2 (post-LSP, manual from the handler via ``find_rule``): the LSP
    server has returned a ``WorkspaceEdit`` whose keys are every file that
    will be touched. We check each one is within WORKDIR. The check is
    hardened against ``../`` traversal via ``Path.resolve().relative_to``.

    Defense-in-depth: even if a future ``harness.toml`` override replaces
    DEFAULT_POLICY for the first pass, the handler MUST keep the second
    pass hardcoded to DEFAULT_POLICY (not ``hooks.policy``) so a malicious
    or buggy LSP server cannot bypass the workspace boundary via a
    user-overridden policy.
    """
    files = args.get("_resolved_files")
    workdir = WORKDIR.resolve()
    if not files:
        # Early pass (PreToolUse): only entry path known.
        return _outside_workspace({"path": args.get("path", "")})
    for f in files:
        try:
            Path(f).resolve().relative_to(workdir)
        except (ValueError, OSError):
            return True
    return False


def _destructive_bash(args: dict) -> bool:
    cmd = args.get("command", "")
    return any(kw in cmd for kw in ("rm ", "> /etc/", "chmod 777"))


def _mcp_pattern_matches(pattern: str, tool_name: str) -> bool:
    """Match an MCP tool name against a permission pattern with ``*`` wildcard.

    The tool name has the form ``mcp__server__tool`` (3 ``__``-separated
    parts, per the M2 prefix). The pattern is written in
    ``server__tool`` form (2 parts); the leading ``mcp__`` is stripped
    from the tool name before comparison so users can write patterns
    that read naturally.

    Matching is segment-by-segment; ``*`` matches any single segment.
    Segment count mismatch → no match. Examples:

      - ``server__tool`` matches ``mcp__server__tool`` (exact)
      - ``*__read_file`` matches ``mcp__fs__read_file``  (wildcard server)
      - ``github__*`` matches ``mcp__github__create_issue`` (wildcard tool)
      - ``*__*`` matches any ``mcp__server__tool``
    """
    p_parts = pattern.split("__")
    t_parts = tool_name.split("__")
    if t_parts and t_parts[0] == "mcp":
        t_parts = t_parts[1:]
    if len(p_parts) != len(t_parts):
        return False
    for p, t in zip(p_parts, t_parts):
        if p != "*" and p != t:
            return False
    return True


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
        PermissionRule(
            tools=("lsp_rename_symbol",),
            check=_lsp_rename_outside_workspace,
            message="LSP rename would change files outside the workspace",
        ),
    ),
)
