"""Tests for f-mcp-permission-gate (Phase PM-2, M5 CRITICAL fix).

Covers the 3-state MCP permission gate: deny (hard block) /
auto_approve (silent allow) / neither (y/N prompt). The M5
invariant is that *every* mcp__* call goes through one of these
three states; no silent fall-through is permitted.

The R3 regression guard (``test_no_fake_block_constructed``) ensures
no synthetic tool block is constructed in hooks.py to route mcp__*
calls through the generic PreToolUse path — that was the bug pattern
the LSP PL-3 subagent slipped past ``--filter mcp``. The guard is a
permanent invariant.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from loom.agent import loop as _loop
from loom.agent.config import (
    ConfigError,
    HarnessConfig,
    MCPConfig,
    MCPPermissions,
    load_config,
)
from loom.agent.hooks import Hooks
from loom.agent.permissions import _mcp_pattern_matches


# ── fixtures ───────────────────────────────────────────────────────────────


@contextmanager
def _patched_mcp_config(
    *,
    auto_approve: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
) -> Iterator[None]:
    """Install a HarnessConfig whose ``mcp.permissions`` has the given
    auto_approve / deny patterns for the duration of the with-block.

    Saves and restores ``loom.agent.loop._active_config`` so the swap
    is local to the test. The default config's policy /
    disabled_tools are preserved so unrelated branch behavior is
    unchanged.

    Note: ``Hooks._check_mcp_permissions`` does
    ``from loom.agent.loop import _active_config`` at call time, so
    the patch must live on the ``loop`` module's namespace, not
    hooks'.
    """
    original = _loop._active_config
    new_cfg = HarnessConfig(
        policy=original.policy,
        checkpoint=original.checkpoint,
        telemetry=original.telemetry,
        disabled_tools=original.disabled_tools,
        run_init_sh_on_session_end=original.run_init_sh_on_session_end,
        llm=original.llm,
        max_turns=original.max_turns,
        lsp=original.lsp,
        mcp=MCPConfig(servers=(), permissions=MCPPermissions(
            auto_approve=auto_approve, deny=deny,
        )),
    )
    _loop._active_config = new_cfg
    try:
        yield
    finally:
        _loop._active_config = original


class _Block:
    """Minimal duck-typed stand-in for a tool-use block."""

    def __init__(self, name: str, input: dict | None = None) -> None:
        self.name = name
        self.input = input or {}


# ── 1. _mcp_pattern_matches (segment-wise wildcard) ────────────────────────


def test_mcp_pattern_matches_exact() -> None:
    """Pattern ``server__tool`` matches ``mcp__server__tool`` exactly."""
    assert _mcp_pattern_matches("github__search_code", "mcp__github__search_code")


def test_mcp_pattern_matches_wildcard_server() -> None:
    """``*__read_file`` matches every server's ``read_file``."""
    assert _mcp_pattern_matches("__read_file".replace("__", "*__"), "mcp__fs__read_file")
    assert _mcp_pattern_matches("__delete".replace("__", "*__"), "mcp__github__delete")


def test_mcp_pattern_matches_wildcard_tool() -> None:
    """``github__*`` matches every tool on the github server."""
    assert _mcp_pattern_matches("github__".replace("__", "__*"), "mcp__github__create_issue")
    assert _mcp_pattern_matches("github__".replace("__", "__*"), "mcp__github__search_code")


def test_mcp_pattern_matches_double_wildcard() -> None:
    """``*__*`` matches any mcp__server__tool regardless of segments."""
    assert _mcp_pattern_matches("*__*", "mcp__github__create_issue")
    assert _mcp_pattern_matches("*__*", "mcp__x__y")
    # Single-segment tools are NOT matched (segment count differs).
    assert not _mcp_pattern_matches("*__*", "mcp__onlyserver")


def test_mcp_pattern_no_match_different_segment_count() -> None:
    """Pattern with different segment count than tool → no match."""
    assert not _mcp_pattern_matches("server", "mcp__server__tool")
    assert not _mcp_pattern_matches("a__b__c", "mcp__a__b")
    assert not _mcp_pattern_matches("a__b__c__d", "mcp__a__b__c")


# ── 2. MCPPermissions default state ────────────────────────────────────────


def test_mcp_permissions_default_empty() -> None:
    """An MCPConfig with no permissions configured has empty deny + auto_approve."""
    perms = MCPPermissions()
    assert perms.auto_approve == ()
    assert perms.deny == ()


def test_mcp_config_parse_permissions_minimal(tmp_path: Path) -> None:
    """[mcp.permissions] auto_approve + deny round-trip through load_config."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.permissions]\n'
        'auto_approve = ["filesystem__read_file", "*__list_files"]\n'
        'deny = ["*__delete", "*__drop_table"]\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.mcp.permissions.auto_approve == (
        "filesystem__read_file", "*__list_files",
    )
    assert cfg.mcp.permissions.deny == (
        "*__delete", "*__drop_table",
    )


def test_mcp_config_parse_permissions_rejects_non_list_string() -> None:
    """``auto_approve = "literal"`` (string, not list) → ConfigError."""
    import shutil
    wd = Path("/tmp") / "loop-test-pm2-bad-autoapprove"
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True, exist_ok=True)
    try:
        (wd / "harness.toml").write_text(
            '[mcp.permissions]\nauto_approve = "literal"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"\[mcp\.permissions\]\.auto_approve"):
            load_config(wd)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_mcp_config_parse_permissions_rejects_non_string_items() -> None:
    """``deny = [42]`` (non-string items) → ConfigError."""
    import shutil
    wd = Path("/tmp") / "loop-test-pm2-bad-deny"
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True, exist_ok=True)
    try:
        (wd / "harness.toml").write_text(
            '[mcp.permissions]\ndeny = [42]\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"\[mcp\.permissions\]\.deny"):
            load_config(wd)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


# ── 3. _check_mcp_permissions — 3-state semantics ─────────────────────────


def test_mcp_permissions_deny_hard_blocks() -> None:
    """Deny pattern match → return "Permission denied" string; _ask_user NOT called.

    M5 invariant: deny is a HARD block, no user override, no prompt.
    """
    with _patched_mcp_config(deny=("*__delete",)):
        hooks = Hooks(asker=lambda *_a, **_k: "allow")  # would-be allow ignored
        result = hooks._check_mcp_permissions("mcp__github__delete", {"force": True})
    assert result is not None
    assert "Permission denied" in result
    assert "delete" in result


def test_mcp_permissions_auto_approve_silent_allow() -> None:
    """Auto-approve pattern match → return None; _ask_user NOT called.

    M5 invariant: auto_approve is silent; the user is never prompted.
    """
    with _patched_mcp_config(auto_approve=("filesystem__read_file",)):
        hooks = Hooks(asker=lambda *_a, **_k: pytest.fail(
            "_ask_user must NOT be called for auto-approved tools",
        ))
        result = hooks._check_mcp_permissions("mcp__filesystem__read_file", {"path": "/tmp/x"})
    assert result is None


def test_mcp_permissions_neither_prompts_user_allow() -> None:
    """Tool not in either list → _ask_user is called; user allows → return None."""
    with _patched_mcp_config():
        hooks = Hooks(asker=lambda *a, **k: "allow")
        result = hooks._check_mcp_permissions("mcp__fs__list_files", {"path": "/tmp"})
    assert result is None


def test_mcp_permissions_neither_prompts_user_deny() -> None:
    """Tool not in either list → _ask_user is called; user denies → "Permission denied by user"."""
    with _patched_mcp_config():
        hooks = Hooks(asker=lambda *a, **k: "deny")
        result = hooks._check_mcp_permissions("mcp__fs__list_files", {"path": "/tmp"})
    assert result is not None
    assert "Permission denied by user" in result


def test_mcp_permissions_deny_overrides_auto_approve() -> None:
    """Tool in BOTH deny and auto_approve → deny wins (deny checked first)."""
    with _patched_mcp_config(
        auto_approve=("*__destructive",),
        deny=("*__destructive",),
    ):
        hooks = Hooks(asker=lambda *a, **k: pytest.fail(
            "deny path must short-circuit before auto_approve",
        ))
        result = hooks._check_mcp_permissions("mcp__x__destructive", {})
    assert result is not None
    assert "Permission denied" in result


# ── 4. check_permission_hook routing ──────────────────────────────────────


def test_check_permission_hook_routes_mcp_to_mcp_check() -> None:
    """An mcp__* tool must go through _check_mcp_permissions, NOT _check_rules."""
    with _patched_mcp_config(deny=("*__read_file",)):
        hooks = Hooks()
        with patch.object(hooks, "_check_mcp_permissions",
                          wraps=hooks._check_mcp_permissions) as mcp_check, \
             patch.object(hooks, "_check_rules",
                          wraps=hooks._check_rules) as rules_check:
            result = hooks.check_permission_hook(
                "PreToolUse",
                _Block("mcp__github__read_file", {"path": "/tmp/x"}),
            )
    assert result is not None
    assert "Permission denied" in result
    assert mcp_check.call_count == 1
    assert rules_check.call_count == 0, (
        "M5 routing violation: mcp__* tool must NOT pass through _check_rules"
    )


def test_check_permission_hook_non_mcp_unchanged() -> None:
    """A non-mcp tool must still go through _check_rules; _check_mcp_permissions NOT called."""
    with _patched_mcp_config():
        hooks = Hooks()
        with patch.object(hooks, "_check_mcp_permissions",
                          wraps=hooks._check_mcp_permissions) as mcp_check, \
             patch.object(hooks, "_check_rules",
                          wraps=hooks._check_rules) as rules_check:
            # write_file to a path inside WORKDIR — no rule should match
            hooks.check_permission_hook(
                "PreToolUse",
                _Block("write_file", {"path": "loomy.txt"}),
            )
    assert mcp_check.call_count == 0, (
        "non-mcp__* tool must NOT enter the MCP gate"
    )
    assert rules_check.call_count == 1


def test_check_permission_hook_skips_mcp_path_for_non_string_name() -> None:
    """A tool block whose ``name`` is not a ``str`` (e.g. MagicMock) must skip
    the MCP gate and not fall through to ``_ask_user``.

    Regression guard for the PM-2 fix that added an ``isinstance`` guard
    before ``block.name.startswith("mcp__")``. Pre-fix, MagicMock-based
    tests in ``failure_modes.py`` and ``phase5_coverage.py`` raised
    ``EOFError`` because the truthy ``.startswith`` MagicMock entered
    the MCP branch and fell through to ``_ask_user`` → ``input()``.
    """
    from unittest.mock import MagicMock
    with _patched_mcp_config():
        hooks = Hooks()
        with patch.object(hooks, "_check_mcp_permissions",
                          wraps=hooks._check_mcp_permissions) as mcp_check, \
             patch.object(hooks, "_ask_user",
                          wraps=hooks._ask_user) as asker:
            mock_block = MagicMock()
            # .name is itself a MagicMock (auto-attribute); .startswith
            # is auto-attributed to a truthy MagicMock. Pre-fix this
            # entered the MCP branch.
            mock_block.name = MagicMock(name="bash.name")
            hooks.check_permission_hook("PreToolUse", mock_block)
    assert mcp_check.call_count == 0, (
        "non-string block.name must skip the MCP gate; "
        "MagicMock.startswith auto-attributes to a truthy value"
    )
    assert asker.call_count == 0, (
        "non-string block.name must not reach _ask_user (would block on stdin)"
    )


# ── 5. R3 regression guard: no fake_block constructed ─────────────────────


def test_no_fake_block_constructed() -> None:
    """hooks.py source MUST NOT contain the string ``fake_block``.

    LSP PL-3 introduced a synthetic PreToolUse block to route MCP tool
    calls through the generic gate. That pattern is forbidden: the real
    block from ``_run_tool_block`` is the only legitimate entry point.
    Permanent invariant — see AGENTS.md rule 11.
    """
    hooks_path = Path(inspect.getfile(Hooks))
    source = hooks_path.read_text(encoding="utf-8")
    assert "fake_block" not in source, (
        f"R3 regression: hooks.py contains a fake_block construction at "
        f"{hooks_path}. Constructing a synthetic PreToolUse block is "
        f"forbidden — use the real block from _run_tool_block."
    )
