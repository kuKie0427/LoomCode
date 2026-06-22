"""Harness eval cases for f-mcp-permission-gate (Phase PM-2, M5 CRITICAL).

Four product-behavior guarantees that lock the M5 invariant and the
R3 regression guard:

  1. mcp-permission-deny-hard-blocks: an mcp__* tool whose name matches
     a deny pattern in ``mcp.permissions.deny`` returns a non-empty
     ``"Permission denied"`` string from ``Hooks._check_mcp_permissions``
     without ever invoking ``_ask_user``.

  2. mcp-permission-auto-approve-allows: an mcp__* tool whose name
     matches an auto_approve pattern returns ``None`` (allow) without
     ever invoking ``_ask_user``.

  3. mcp-permission-prompt-when-neither: an mcp__* tool whose name
     matches NEITHER list invokes ``_ask_user`` (y/N prompt). User
     "allow" → ``None``; user "deny" → ``"Permission denied by user"``.
     This is the M5 invariant: no silent fall-through.

  4. mcp-permission-no-fake-block-constructed: the hooks.py source
     MUST NOT contain the string ``fake_block``. R3 regression guard
     from LSP PL-3 — never construct a synthetic PreToolUse block.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from loom.agent import loop as _loop
from loom.agent.config import (
    HarnessConfig,
    MCPConfig,
    MCPPermissions,
)
from loom.agent.hooks import Hooks
from loom.eval.runner import EvalCase, EvalResult


def _with_mcp_config(
    *,
    auto_approve: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
):
    """Return a context manager that swaps ``_active_config`` for a
    HarnessConfig with the given MCP permissions, restoring on exit.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
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

    return _ctx()


class MCPPermissionDenyHardBlocks(EvalCase):
    name = "mcp-permission-deny-hard-blocks"
    description = (
        "M5 gate: an mcp__* tool matching a deny pattern returns a "
        "'Permission denied' string and never invokes _ask_user"
    )

    def run(self) -> EvalResult:
        with _with_mcp_config(deny=("*__delete",)):
            asker_called = {"n": 0}

            def _asker(*_a, **_k) -> str:
                asker_called["n"] += 1
                return "allow"

            hooks = Hooks(asker=_asker)
            result = hooks._check_mcp_permissions("mcp__github__delete", {"force": True})
        if result is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="deny pattern matched but _check_mcp_permissions returned None",
            )
        if "Permission denied" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"reason missing 'Permission denied': {result!r}",
            )
        if asker_called["n"] != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_ask_user was called {asker_called['n']}× on deny path "
                       f"(M5 violation: deny must hard-block, no user override)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"deny hard-blocked with reason: {result[:60]!r}",
        )


class MCPPermissionAutoApproveAllows(EvalCase):
    name = "mcp-permission-auto-approve-allows"
    description = (
        "M5 gate: an mcp__* tool matching an auto_approve pattern "
        "returns None (allow) and never invokes _ask_user"
    )

    def run(self) -> EvalResult:
        with _with_mcp_config(auto_approve=("filesystem__read_file",)):
            asker_called = {"n": 0}

            def _asker(*_a, **_k) -> str:
                asker_called["n"] += 1
                return "deny"

            hooks = Hooks(asker=_asker)
            result = hooks._check_mcp_permissions("mcp__filesystem__read_file", {"path": "/tmp/x"})
        if result is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"auto_approve should return None, got {result!r}",
            )
        if asker_called["n"] != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_ask_user was called {asker_called['n']}× on auto_approve path "
                       f"(M5 violation: auto_approve must be silent)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="auto_approve silent-allowed without prompting",
        )


class MCPPermissionPromptWhenNeither(EvalCase):
    name = "mcp-permission-prompt-when-neither"
    description = (
        "M5 gate: an mcp__* tool matching NEITHER list invokes _ask_user; "
        "user allow → None, user deny → 'Permission denied by user' "
        "(never silently allow)"
    )

    def run(self) -> EvalResult:
        # Sub-case A: user allows → None
        with _with_mcp_config():
            asker_calls: list[str] = []

            def _asker_a(*_a, **_k) -> str:
                asker_calls.append("allow")
                return "allow"

            hooks = Hooks(asker=_asker_a)
            result_a = hooks._check_mcp_permissions("mcp__fs__list_files", {"path": "/tmp"})
        if result_a is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"user allow should return None, got {result_a!r}",
            )
        if asker_calls != ["allow"]:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_ask_user not invoked as expected: calls={asker_calls}",
            )

        # Sub-case B: user denies → "Permission denied by user"
        with _with_mcp_config():
            asker_calls_b: list[str] = []

            def _asker_b(*_a, **_k) -> str:
                asker_calls_b.append("deny")
                return "deny"

            hooks = Hooks(asker=_asker_b)
            result_b = hooks._check_mcp_permissions("mcp__fs__list_files", {"path": "/tmp"})
        if result_b is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="user deny should return non-None, got None",
            )
        if "Permission denied by user" not in result_b:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"user-deny message missing 'Permission denied by user': {result_b!r}",
            )
        if asker_calls_b != ["deny"]:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_ask_user not invoked as expected (B): calls={asker_calls_b}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="both sub-cases (allow → None, deny → 'Permission denied by user') passed",
        )


class MCPPermissionNoFakeBlockConstructed(EvalCase):
    name = "mcp-permission-no-fake-block-constructed"
    description = (
        "R3 regression guard: hooks.py source MUST NOT contain the "
        "string 'fake_block'. Constructing a synthetic PreToolUse "
        "block to route MCP calls through the generic gate is forbidden."
    )

    def run(self) -> EvalResult:
        hooks_path = Path(inspect.getfile(Hooks))
        source = hooks_path.read_text(encoding="utf-8")
        if "fake_block" in source:
            for i, line in enumerate(source.splitlines(), 1):
                if "fake_block" in line:
                    snippet = line.strip()
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=f"fake_block found at {hooks_path}:{i}: {snippet[:80]!r}",
                    )
            return EvalResult(
                name=self.name, passed=False,
                detail="fake_block string present in hooks.py source",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"hooks.py ({hooks_path.name}) contains no fake_block construction",
        )
