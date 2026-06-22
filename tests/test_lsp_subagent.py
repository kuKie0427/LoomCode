"""Tests for f-lsp-subagent-docs (Phase PL-4).

Subagent access to LSP tools — `SUB_TOOLS` and `SUB_HANDLERS` mirror the
parent `TOOL_REGISTRY` set for the three LSP tools, but route to the SAME
handler implementations (no copy-paste). A fail-closed test guarantees that
a subagent invoking `lsp_goto_definition` without an LSP config in
`_active_config` returns a string (not raises a traceback).
"""

from __future__ import annotations

from loom.agent import tools as tools_mod


def _sub_tool_names() -> set[str]:
    return {t["name"] for t in tools_mod.SUB_TOOLS}


class TestSubToolsContainsLSP:
    def test_sub_tools_contains_lsp_goto(self) -> None:
        assert "lsp_goto_definition" in _sub_tool_names()

    def test_sub_tools_contains_lsp_find_references(self) -> None:
        assert "lsp_find_references" in _sub_tool_names()

    def test_sub_tools_contains_lsp_rename(self) -> None:
        assert "lsp_rename_symbol" in _sub_tool_names()


class TestSubHandlersLSPRouted:
    def test_sub_handlers_lsp_goto_callable(self) -> None:
        handler = tools_mod.SUB_HANDLERS.get("lsp_goto_definition")
        assert handler is not None
        assert callable(handler)

    def test_sub_handlers_lsp_find_references_callable(self) -> None:
        handler = tools_mod.SUB_HANDLERS.get("lsp_find_references")
        assert handler is not None
        assert callable(handler)

    def test_sub_handlers_lsp_rename_callable(self) -> None:
        handler = tools_mod.SUB_HANDLERS.get("lsp_rename_symbol")
        assert handler is not None
        assert callable(handler)

    def test_sub_handlers_lsp_rename_routes_to_real_handler(self) -> None:
        """SUB_HANDLERS["lsp_rename_symbol"] is identity-equal to
        tools_mod.run_lsp_rename_symbol — no copy-paste or wrapper."""
        assert tools_mod.SUB_HANDLERS["lsp_rename_symbol"] is tools_mod.run_lsp_rename_symbol

    def test_sub_handlers_lsp_goto_routes_to_real_handler(self) -> None:
        assert tools_mod.SUB_HANDLERS["lsp_goto_definition"] is tools_mod.run_lsp_goto_definition

    def test_sub_handlers_lsp_find_references_routes_to_real_handler(self) -> None:
        assert tools_mod.SUB_HANDLERS["lsp_find_references"] is tools_mod.run_lsp_find_references


class TestSubHandlersFailClosed:
    def test_sub_handlers_lsp_goto_returns_no_config_when_unset(
        self, monkeypatch, tmp_path,
    ) -> None:
        """A subagent (which inherits the same WORKDIR / `_active_config`)
        calling `lsp_goto_definition` with no `[lsp]` block in harness.toml
        must get back a string (not raise a traceback). The string may be
        either "No LSP server configured" (PL-2 manager wired) or "LSP
        unavailable" (PL-1 stub) — both satisfy the fail-closed contract."""
        from loom.agent import lsp_manager as lm
        from loom.agent.config import HarnessConfig

        monkeypatch.setattr("loom.agent.tools.WORKDIR", tmp_path)
        (tmp_path / "x.py").write_text("x = 1\n")
        # Force the manager into a no-config state by clearing its caches
        # and pointing `_active_config` at the empty default config. This is
        # exactly the state a subagent spawned from a fresh REPL would see.
        lm._ACTIVE_SERVERS.clear()
        lm._PER_SERVER_LOCKS.clear()
        monkeypatch.setattr("loom.agent.loop._active_config",
                            HarnessConfig.from_defaults())

        # Call via SUB_HANDLERS — this is the path the subagent loop takes.
        handler = tools_mod.SUB_HANDLERS["lsp_goto_definition"]
        out = handler(path="x.py", line=0, character=0)
        assert isinstance(out, str), (
            f"subagent handler must return str, got {type(out).__name__}: {out!r}"
        )
        # Both PL-1 stub and PL-2 wired paths are acceptable.
        assert (
            "No LSP server configured" in out
            or "LSP unavailable" in out
        ), f"unexpected fail-closed output: {out!r}"
