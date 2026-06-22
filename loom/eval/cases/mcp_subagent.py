"""Harness eval cases for f-mcp-subagent-docs (Phase PM-4, M9 fix).

Two product-behavior guarantees that lock the per-server subagent
opt-in gate in ``mcp_manager._discover_server``:

  1. **mcp-subagent-opt-in-adds-to-sub-tools**: when
     ``MCPServerConfig.subagent_access=True``, the manager mirrors the
     discovered tool into ``SUB_TOOLS`` (and the matching handler into
     ``SUB_HANDLERS``) so ``spawn_subagent`` can call it.

  2. **mcp-subagent-default-excluded-from-sub-tools**: when
     ``subagent_access`` is absent or False (the default), the tool is
     NOT mirrored into ``SUB_TOOLS``. The M9 safety guarantee: only
     explicit per-server opt-in exposes MCP tools to subagents. The
     PM-2 3-state permission gate still fires for subagent tool calls,
     but defense-in-depth is to require the operator to flip the bit.

The eval runner is the regression net for product behavior; pytest is
for unit correctness (see tests/test_mcp_subagent.py for the
finer-grained 6 tests). These two cases cover the high-level
invariants the eval suite already gates for every other loom
subsystem.
"""

from __future__ import annotations

from unittest.mock import patch

from loom.agent.config import HarnessConfig, MCPServerConfig
from loom.eval.runner import EvalCase, EvalResult


def _fake_start(server) -> None:
    """Stand-in for mcp_client.start: pretend handshake succeeded."""
    server.tools = [
        {
            "name": "fake_tool",
            "description": f"fake tool from {server.name}",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _make_cfg(*specs: MCPServerConfig) -> HarnessConfig:
    """Build a HarnessConfig whose MCPConfig carries only `specs`."""
    from loom.agent.config import MCPConfig
    base = HarnessConfig.from_defaults()
    return HarnessConfig.from_defaults().__class__(
        policy=base.policy,
        checkpoint=base.checkpoint,
        mcp=MCPConfig(servers=tuple(specs)),
    )


def _reset_state() -> None:
    """Wipe mcp_manager state + SUB_TOOLS / SUB_HANDLERS + TOOL_REGISTRY."""
    from loom.agent import mcp_manager as mm
    from loom.agent import tools as tools_mod_local
    from loom.agent.tools import TOOL_REGISTRY, _resync_from_registry

    mm._ACTIVE_SERVERS.clear()
    mm._PER_SERVER_LOCKS.clear()
    mm._DISCOVERY_THREADS.clear()
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith("mcp__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass
    # Strip mcp__* entries from SUB_TOOLS / SUB_HANDLERS so the case
    # starts from a clean slate (other cases may have left entries).
    tools_mod_local.SUB_TOOLS[:] = [
        t for t in tools_mod_local.SUB_TOOLS
        if not t.get("name", "").startswith("mcp__")
    ]
    for hname in list(tools_mod_local.SUB_HANDLERS.keys()):
        if hname.startswith("mcp__"):
            del tools_mod_local.SUB_HANDLERS[hname]
    _resync_from_registry()


class MCPSubagentOptInAddsToSubTools(EvalCase):
    name = "mcp-subagent-opt-in-adds-to-sub-tools"
    description = (
        "M9 opt-in: MCPServerConfig.subagent_access=True mirrors the "
        "discovered tool into SUB_TOOLS + SUB_HANDLERS so spawn_subagent "
        "can call it. Default behavior is no mirror; opt-in required."
    )

    def run(self) -> EvalResult:
        from loom.agent import mcp_manager as mm
        from loom.agent.config import MCPServerConfig
        from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS, TOOL_REGISTRY

        _reset_state()
        try:
            cfg = _make_cfg(
                MCPServerConfig(name="fs", command="ignored", subagent_access=True),
            )
            with patch.object(mm, "mcp_start", side_effect=_fake_start):
                mm.start_discovery(cfg)
                for t in mm._DISCOVERY_THREADS:
                    t.join(timeout=2.0)

            sub_names = {t["name"] for t in SUB_TOOLS}
            if "mcp__fs__fake_tool" not in sub_names:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"subagent_access=True should add mcp__fs__fake_tool "
                        f"to SUB_TOOLS; got {sorted(sub_names)[:10]}"
                    ),
                )
            if "mcp__fs__fake_tool" not in SUB_HANDLERS:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="handler missing from SUB_HANDLERS",
                )
            if not callable(SUB_HANDLERS["mcp__fs__fake_tool"]):
                return EvalResult(
                    name=self.name, passed=False,
                    detail="SUB_HANDLERS entry not callable",
                )
            # Main surface still has the tool (regression guard).
            if "mcp__fs__fake_tool" not in TOOL_REGISTRY.names():
                return EvalResult(
                    name=self.name, passed=False,
                    detail="TOOL_REGISTRY missing the tool (regression)",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=(
                    "subagent_access=True mirrored mcp__fs__fake_tool into "
                    "SUB_TOOLS + SUB_HANDLERS; main agent surface intact"
                ),
            )
        finally:
            _reset_state()


class MCPSubagentDefaultExcludedFromSubTools(EvalCase):
    name = "mcp-subagent-default-excluded-from-sub-tools"
    description = (
        "M9 safety: default subagent_access=False (omitted) does NOT "
        "mirror the discovered tool into SUB_TOOLS. Subagents cannot "
        "call mcp__* tools the operator has not explicitly opted in. "
        "The main agent still sees the tool in TOOL_REGISTRY."
    )

    def run(self) -> EvalResult:
        from loom.agent import mcp_manager as mm
        from loom.agent.config import MCPServerConfig
        from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS, TOOL_REGISTRY

        _reset_state()
        try:
            # subagent_access absent → defaults to False (M9 safety).
            cfg = _make_cfg(
                MCPServerConfig(name="github", command="ignored"),
            )
            with patch.object(mm, "mcp_start", side_effect=_fake_start):
                mm.start_discovery(cfg)
                for t in mm._DISCOVERY_THREADS:
                    t.join(timeout=2.0)

            sub_names = {t["name"] for t in SUB_TOOLS}
            if "mcp__github__fake_tool" in sub_names:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"M9 violation: subagent_access default (False) "
                        f"must NOT add mcp__github__fake_tool to SUB_TOOLS; "
                        f"got {sorted(sub_names)[:10]}"
                    ),
                )
            if "mcp__github__fake_tool" in SUB_HANDLERS:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="M9 violation: handler leaked into SUB_HANDLERS",
                )
            # Main agent surface MUST still have the tool.
            if "mcp__github__fake_tool" not in TOOL_REGISTRY.names():
                return EvalResult(
                    name=self.name, passed=False,
                    detail="TOOL_REGISTRY missing the tool (regression)",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=(
                    "subagent_access default (False) excluded mcp__github__fake_tool "
                    "from SUB_TOOLS / SUB_HANDLERS; main agent surface intact"
                ),
            )
        finally:
            _reset_state()
