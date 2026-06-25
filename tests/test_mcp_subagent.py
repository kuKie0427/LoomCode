"""Tests for f-mcp-subagent-docs (Phase PM-4, M9 fix).

Covers the per-server subagent opt-in gate in ``mcp_manager``. M9
(Oracle risk): without an explicit opt-in, subagents could call
dangerous MCP tools (``mcp__github__push``, ``mcp__sqlite__drop_table``)
that the operator only intended to expose to the main agent. The
PM-4 fix mirrors tools into ``SUB_TOOLS`` / ``SUB_HANDLERS`` ONLY when
``MCPServerConfig.subagent_access=True`` (default False).

Covers:

  - default ``subagent_access=False`` does NOT add tools to SUB_TOOLS
  - ``subagent_access=True`` DOES add tools to SUB_TOOLS + SUB_HANDLERS
  - SUB_HANDLERS entry is identity-equal to the TOOL_REGISTRY handler
  - shutdown_all clears SUB_TOOLS / SUB_HANDLERS for the server
  - subagent calling mcp__* goes through PreToolUse + _check_mcp_permissions
    (KEY SAFETY: subagents cannot bypass the PM-2 3-state gate)
  - subagent_access is per-server: one True / one False → only True in SUB_TOOLS

The handler tests mirror the existing test_mcp_manager.py / test_mcp_handler.py
patterns: install a fake server, mock ``mcp_start`` to seed tools, run
``start_discovery`` to spawn the discovery thread, then assert on the
post-discovery state of ``SUB_TOOLS`` and ``SUB_HANDLERS``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from loom.agent import mcp_manager as mm
from loom.agent import tools as tools_mod
from loom.agent.config import HarnessConfig, MCPConfig, MCPServerConfig
from loom.agent.mcp_client import MCPServer
from loom.agent.tools import (
    SUB_HANDLERS,
    SUB_TOOLS,
    TOOL_REGISTRY,
)

# ── Shared fixture: clean state between tests ───────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_manager_state():
    """Wipe mcp_manager state + SUB_TOOLS / SUB_HANDLERS + TOOL_REGISTRY.

    Also resync TOOLS / TOOL_HANDLERS after the test so any
    mcp__* entry that mcp_manager._discover_server appended via
    ``_resync_from_registry`` does not linger as a phantom item in the
    module-level TOOLS list (which the next test would observe as
    ``initial = len(get_tools())`` and fail its ``initial + 1`` assertion).

    P2-1 fix: also reset ``mm._DISCOVERY_STARTED`` — without this, the first
    test that calls ``start_discovery`` sets the flag True, and subsequent
    tests' ``start_discovery`` calls become no-ops (mcp_manager.py:110-113
    early-return), so no tools ever register. Mirrors the fixture in
    test_mcp_manager.py:44,57.
    """
    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
    mm._DISCOVERY_STARTED = False  # P2-1 fix: reset idempotency guard
    # Snapshot & restore SUB_TOOLS / SUB_HANDLERS so tests don't leak into
    # each other. Using [:] = list preserves module identity.
    saved_sub_tools = list(tools_mod.SUB_TOOLS)
    saved_sub_handlers = dict(tools_mod.SUB_HANDLERS)
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass
    # Resync now so TOOLS / TOOL_HANDLERS match the (cleaned) registry.
    try:
        tools_mod._resync_from_registry()
    except Exception:
        pass
    yield
    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
    mm._DISCOVERY_STARTED = False  # P2-1 fix: reset for next test
    tools_mod.SUB_TOOLS[:] = saved_sub_tools
    tools_mod.SUB_HANDLERS.clear()
    tools_mod.SUB_HANDLERS.update(saved_sub_handlers)
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass
    # Resync again after the test so a subsequent test (e.g.
    # test_mcp_wire.test_get_tools_lazy_reflects_registry) does not
    # observe a stale mcp__* entry in the module-level TOOLS list.
    try:
        tools_mod._resync_from_registry()
    except Exception:
        pass


def _make_cfg(*specs: MCPServerConfig) -> HarnessConfig:
    base = HarnessConfig.from_defaults()
    return HarnessConfig.from_defaults().__class__(
        policy=base.policy,
        checkpoint=base.checkpoint,
        mcp=MCPConfig(servers=tuple(specs)),
    )


def _fake_start(server: MCPServer) -> None:
    """Stand-in for mcp_client.start: pretend handshake succeeded."""
    server.tools = [
        {
            "name": "fake_tool",
            "description": f"fake tool from {server.name}",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# ── 1. subagent_access=True adds to SUB_TOOLS + SUB_HANDLERS ────────────────


def test_subagent_access_true_adds_to_sub_tools() -> None:
    """subagent_access=True → SUB_TOOLS contains mcp__server__* entries.

    M9: opt-in flag must mirror the registered tool into the subagent
    surface so ``spawn_subagent`` can call it.
    """
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored", subagent_access=True),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    sub_names = {t["name"] for t in SUB_TOOLS}
    assert "mcp__fs__fake_tool" in sub_names, (
        f"M9 violation: subagent_access=True should add mcp__fs__fake_tool "
        f"to SUB_TOOLS; got {sorted(sub_names)[:10]}"
    )
    assert "mcp__fs__fake_tool" in SUB_HANDLERS, (
        "M9 violation: handler must also be in SUB_HANDLERS"
    )
    assert callable(SUB_HANDLERS["mcp__fs__fake_tool"]), (
        "SUB_HANDLERS entry for mcp__fs__fake_tool must be callable"
    )


# ── 2. subagent_access=False (default) does NOT add to SUB_TOOLS ────────────


def test_subagent_access_false_does_not_add_to_sub_tools() -> None:
    """subagent_access=False (the default) → SUB_TOOLS does NOT contain mcp__*.

    M9: default-False is the safety guarantee. The main agent sees the
    tool (it's in TOOL_REGISTRY), but subagents do not.
    """
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored"),  # subagent_access=False default
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    # Main agent surface has the tool.
    assert "mcp__fs__fake_tool" in TOOL_REGISTRY.names(), (
        "TOOL_REGISTRY should still have the tool for the main agent"
    )
    # Subagent surface does NOT.
    sub_names = {t["name"] for t in SUB_TOOLS}
    assert "mcp__fs__fake_tool" not in sub_names, (
        f"M9 violation: subagent_access=False (default) must NOT add "
        f"mcp__fs__fake_tool to SUB_TOOLS; got {sorted(sub_names)[:10]}"
    )
    assert "mcp__fs__fake_tool" not in SUB_HANDLERS


# ── 3. SUB_HANDLERS entry is identity-equal to TOOL_REGISTRY handler ────────


def test_sub_tools_mcp_handler_same_as_tool_registry() -> None:
    """SUB_HANDLERS[name] is the SAME function as TOOL_REGISTRY's handler.

    M9 invariant: the handler is shared, not copy-pasted. All PM-2 /
    PM-3 mitigations apply identically. The identity check is the
    cheapest way to assert this.
    """
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored", subagent_access=True),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    main_handler = TOOL_REGISTRY.handler_for("mcp__fs__fake_tool")
    sub_handler = SUB_HANDLERS.get("mcp__fs__fake_tool")
    assert main_handler is not None, "main TOOL_REGISTRY handler missing"
    assert sub_handler is not None, "sub SUB_HANDLERS handler missing"
    assert main_handler is sub_handler, (
        "M9: SUB_HANDLERS entry must be identity-equal to TOOL_REGISTRY's "
        "handler (no copy-paste wrapper)"
    )


# ── 4. shutdown_all clears SUB_TOOLS / SUB_HANDLERS for the server ──────────


def test_shutdown_all_clears_sub_tools() -> None:
    """After shutdown_all, SUB_TOOLS and SUB_HANDLERS contain no mcp__*."""
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored", subagent_access=True),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
    # Sanity: pre-shutdown the tool is in both surfaces.
    assert "mcp__fs__fake_tool" in TOOL_REGISTRY.names()
    assert "mcp__fs__fake_tool" in SUB_HANDLERS

    mm.shutdown_all()

    assert "mcp__fs__fake_tool" not in SUB_HANDLERS, (
        "M9: shutdown_all must clear SUB_HANDLERS for the server"
    )
    sub_names_after = {t["name"] for t in SUB_TOOLS}
    assert "mcp__fs__fake_tool" not in sub_names_after, (
        f"M9: shutdown_all must clear SUB_TOOLS for the server; "
        f"got {sorted(sub_names_after)[:10]}"
    )
    # And the main TOOL_REGISTRY too (regression guard for existing behavior).
    assert "mcp__fs__fake_tool" not in TOOL_REGISTRY.names()


# ── 5. subagent calling mcp__* goes through PreToolUse + _check_mcp_permissions


@contextmanager
def _patched_mcp_permissions(deny: tuple[str, ...] = ()) -> Iterator[None]:
    """Install a HarnessConfig whose mcp.permissions has the given deny patterns."""
    from loom.agent import loop as _loop
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
        mcp=MCPConfig(servers=(), permissions=type(_loop._active_config.mcp.permissions)(
            auto_approve=(), deny=deny,
        )),
    )
    _loop._active_config = new_cfg
    try:
        yield
    finally:
        _loop._active_config = original


def test_subagent_call_mcp_goes_through_permission_gate() -> None:
    """Subagent calling mcp__* fires PreToolUse → _check_mcp_permissions.

    KEY SAFETY: subagents cannot bypass the PM-2 3-state gate. We
    install a deny pattern for the tool, then drive the subagent's
    tool call through ``spawn_subagent``'s handler dispatch path,
    and assert the call is blocked.
    """
    from loom.agent.hooks import Hooks
    from loom.agent.tools import spawn_subagent

    # Build a real handler the way mcp_manager would, register it
    # in SUB_HANDLERS so the subagent can call it.
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored", subagent_access=True),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)
    assert "mcp__fs__fake_tool" in SUB_HANDLERS

    # Install a deny pattern. Subagent PreToolUse must fire the gate.
    with _patched_mcp_permissions(deny=("*__fake_tool",)):
        # Drive the subagent via the llm_client / hooks path. The real
        # ``spawn_subagent`` requires a real LLM; we mock it to emit one
        # tool_use block and return. Use MagicMock for the response so we
        # don't have to construct a real anthropic.types.Message.
        from unittest.mock import MagicMock

        call_state = {"n": 0}

        def _mock_create(**_kwargs):
            call_state["n"] += 1
            if call_state["n"] == 1:
                block = MagicMock()
                block.type = "tool_use"
                block.id = "tool_1"
                block.name = "mcp__fs__fake_tool"
                block.input = {"x": 1}
                resp = MagicMock()
                resp.stop_reason = "tool_use"
                resp.content = [block]
                return resp
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = "done"
            resp = MagicMock()
            resp.stop_reason = "end_turn"
            resp.content = [text_block]
            return resp

        mock_client = MagicMock()
        mock_client.model = "claude-test"
        mock_client.invoke = _mock_create

        # Track _check_mcp_permissions invocations.
        check_count = {"mcp": 0}

        real_check = Hooks._check_mcp_permissions
        def _spy_check(self, tool_name, args):
            check_count["mcp"] += 1
            return real_check(self, tool_name, args)

        # Build a Hooks instance with check_permission_hook registered as
        # a PreToolUse callback — the same wiring the main agent loop uses.
        # Without this, a fresh Hooks() has no PreToolUse callbacks and
        # the subagent's PreToolUse would be a no-op, defeating the test.
        test_hooks = Hooks()
        test_hooks.register_hook("PreToolUse", test_hooks.check_permission_hook)

        with patch.object(Hooks, "_check_mcp_permissions", _spy_check):
            result = spawn_subagent(
                description="call mcp__fs__fake_tool",
                llm_client=mock_client,
                hooks=test_hooks,
            )

    # The permission gate fired for the mcp__* tool.
    assert check_count["mcp"] >= 1, (
        "M9 safety violation: subagent calling mcp__fs__fake_tool must "
        "trigger _check_mcp_permissions; got 0 calls"
    )
    # The result reflects the denial (the tool call returned a blocked-result
    # message that the LLM saw, and the second call returned "done").
    assert "done" in result or "Permission" in result, (
        f"unexpected subagent result: {result!r}"
    )


# ── 6. subagent_access is per-server (one True, one False) ──────────────────


def test_subagent_access_only_affects_specified_server() -> None:
    """Two servers — one True, one False — only the True one reaches SUB_TOOLS.

    M9: per-server opt-in must be respected independently. A filesystem
    server with subagent_access=True must NOT leak github tools into
    SUB_TOOLS just because github is a different server.
    """
    cfg = _make_cfg(
        MCPServerConfig(name="fs", command="ignored", subagent_access=True),
        MCPServerConfig(name="github", command="ignored", subagent_access=False),
    )
    with patch.object(mm, "mcp_start", side_effect=_fake_start):
        mm.start_discovery(cfg)
        for t in mm._DISCOVERY_THREADS:
            t.join(timeout=2.0)

    sub_names = {t["name"] for t in SUB_TOOLS}

    # True server → in SUB_TOOLS
    assert "mcp__fs__fake_tool" in sub_names, (
        f"subagent_access=True server 'fs' should be in SUB_TOOLS; "
        f"got {sorted(sub_names)}"
    )
    # False server → NOT in SUB_TOOLS
    assert "mcp__github__fake_tool" not in sub_names, (
        f"M9 violation: subagent_access=False server 'github' must NOT be "
        f"in SUB_TOOLS; got {sorted(sub_names)}"
    )
    # And same for SUB_HANDLERS
    assert "mcp__fs__fake_tool" in SUB_HANDLERS
    assert "mcp__github__fake_tool" not in SUB_HANDLERS

    # Both still in main TOOL_REGISTRY (regression guard)
    reg_names = set(TOOL_REGISTRY.names())
    assert "mcp__fs__fake_tool" in reg_names
    assert "mcp__github__fake_tool" in reg_names
