"""Eval cases for the TUI Header (summary rail) — f-tui-header-summary-rail.

Locks the spec contract from ``docs/tui-design-language.md`` §4.3:

  * Pure helper functions (``mcp_glyph``, ``todo_glyph``, ``subagent_glyph``)
    return worst-state aggregate glyphs per spec §4.3.1.
  * Subagent glyph returns ``(None, 0)`` when count=0 (entire section
    hidden per spec §4.3.1 hide rule).
  * ``AgentTUIApp.compose`` yields ``Header`` BEFORE ``ChatLog`` so the
    dock-top invariant holds (§3 ergonomic grid).
  * ``AgentTUIApp.CSS`` does not contain ``transition`` on
    ``#header-overlay`` (spec §6 — instant replace, no animation).

The widget itself (Header, HeaderOverlay) and DEFAULT_MOCK_STATE are
exercised in ``tests/test_tui_header.py``; this module locks the spec
contract and the app-level wiring invariants.
"""

from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: mcp_glyph all-healthy ────────────────────────────────────────────


class HeaderGlyphMcpHealthy(EvalCase):
    name = "header-glyph-mcp-healthy"
    description = (
        "mcp_glyph returns (●, N, N) when all MCP servers are connected"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import MCPServer, mcp_glyph

        servers = [MCPServer("db", "connected"), MCPServer("fs", "connected")]
        got = mcp_glyph(servers)
        if got != ("●", 2, 2):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"mcp_glyph({servers!r}) == {got!r}, expected ('●', 2, 2)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="mcp_glyph(2 connected) == ('●', 2, 2) — all-healthy aggregate",
        )


# ── Case 2: mcp_glyph any-error ─────────────────────────────────────────────


class HeaderGlyphMcpError(EvalCase):
    name = "header-glyph-mcp-error"
    description = (
        "mcp_glyph returns (◌, 0, M) when any MCP server is in error"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import MCPServer, mcp_glyph

        servers = [MCPServer("db", "connected"), MCPServer("gh", "error")]
        got = mcp_glyph(servers)
        if got != ("◌", 0, 2):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"mcp_glyph({servers!r}) == {got!r}, expected ('◌', 0, 2)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "mcp_glyph(1 connected + 1 error) == ('◌', 0, 2) — "
                "worst-state aggregate"
            ),
        )


# ── Case 3: todo_glyph has-active ───────────────────────────────────────────


class HeaderGlyphTodoActive(EvalCase):
    name = "header-glyph-todo-active"
    description = "todo_glyph returns (◐, 0, M) when any todo is active"

    def run(self) -> EvalResult:
        from loom.tui.header import TodoItem, todo_glyph

        items = [TodoItem("x", "done"), TodoItem("y", "active")]
        got = todo_glyph(items)
        if got != ("◐", 0, 2):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"todo_glyph({items!r}) == {got!r}, expected ('◐', 0, 2)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="todo_glyph(1 done + 1 active) == ('◐', 0, 2) — has-active",
        )


# ── Case 4: todo_glyph empty ────────────────────────────────────────────────


class HeaderGlyphTodoEmpty(EvalCase):
    name = "header-glyph-todo-empty"
    description = "todo_glyph returns (○, 0, 0) for an empty list (hidden rule)"

    def run(self) -> EvalResult:
        from loom.tui.header import todo_glyph

        got = todo_glyph([])
        if got != ("○", 0, 0):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"todo_glyph([]) == {got!r}, expected ('○', 0, 0)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="todo_glyph([]) == ('○', 0, 0) — empty list, section hidden",
        )


# ── Case 5: subagent_glyph zero-count → entire section hidden ──────────────


class HeaderSubagentHiddenWhenZero(EvalCase):
    name = "header-subagent-hidden-when-zero"
    description = (
        "subagent_glyph returns (None, 0) when count=0 — the entire section"
        " is hidden from the collapsed line per spec §4.3.1"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import subagent_glyph

        got = subagent_glyph([])
        if got != (None, 0):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"subagent_glyph([]) == {got!r}, expected (None, 0)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "subagent_glyph([]) == (None, 0) — count=0 means the entire"
                " section is hidden, no 0-subagent placeholder"
            ),
        )


# ── Case 6: Header yields before ChatLog in compose ─────────────────────────


class HeaderDockTopInvariant(EvalCase):
    name = "header-dock-top-invariant"
    description = (
        "AgentTUIApp.compose yields Header(id='header') BEFORE ChatLog"
        " so the dock-top invariant holds per spec §3 (Header docks top,"
        " ChatLog below)"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        if not hasattr(AgentTUIApp, "compose"):
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp has no compose method",
            )

        src = inspect.getsource(AgentTUIApp.compose)
        if "Header" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp.compose does not yield Header",
            )
        if "ChatLog" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp.compose does not yield ChatLog",
            )

        header_pos = src.find('Header(id="header")')
        chatlog_pos = src.find('ChatLog(id="chat-log")')
        if header_pos == -1 or chatlog_pos == -1:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Cannot locate Header/ChatLog yield lines: "
                    f"header_pos={header_pos}, chatlog_pos={chatlog_pos}"
                ),
            )
        if header_pos >= chatlog_pos:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Header must be yielded BEFORE ChatLog, got "
                    f"Header at offset {header_pos}, ChatLog at "
                    f"offset {chatlog_pos}"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                f"AgentTUIApp.compose yields Header (offset {header_pos})"
                f" BEFORE ChatLog (offset {chatlog_pos}) — dock-top invariant"
            ),
        )


# ── Case 7: No transition CSS on #header-overlay ────────────────────────────


class HeaderInstantToggleNoTransition(EvalCase):
    name = "header-instant-toggle-no-transition"
    description = (
        "AgentTUIApp.CSS does not contain a transition property on "
        "#header-overlay — spec §6 mandates instant mount/remove"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        css = AgentTUIApp.CSS
        if "#header-overlay" not in css:
            return EvalResult(
                name=self.name, passed=False,
                detail="CSS missing #header-overlay block",
            )
        if "transition" in css:
            # Find the offending line
            for i, line in enumerate(css.splitlines(), 1):
                if "transition" in line:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=(
                            f"Header CSS must have no transition (spec §6), "
                            f"found on line {i}: {line!r}"
                        ),
                    )
        return EvalResult(
            name=self.name, passed=True,
            detail="AgentTUIApp.CSS has no transition CSS — instant mount/remove",
        )


# ── Case 8: Header is imported and yielded in AgentTUIApp.compose ───────────


class HeaderIncludeHeaderInAppCompose(EvalCase):
    name = "header-include-header-in-app-compose"
    description = (
        "AgentTUIApp.compose imports and yields Header from loom.tui.header"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        if not hasattr(AgentTUIApp, "compose"):
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp has no compose method",
            )
        src = inspect.getsource(AgentTUIApp.compose)
        if "Header" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp.compose source has no 'Header' reference",
            )
        # ``inspect.getsource(AgentTUIApp)`` returns only the class body,
        # not module-level imports. Read the full module file so the
        # ``from loom.tui.header import ...`` statement is visible.
        module_file = inspect.getsourcefile(AgentTUIApp)
        if module_file is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="Cannot locate AgentTUIApp source file via inspect",
            )
        with open(module_file) as f:
            module_src = f.read()
        if "from loom.tui.header import" not in module_src:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp module does not import from loom.tui.header"
                    " — Header wiring is not declared"
                ),
            )
        if "Header(id=\"header\")" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp.compose does not yield Header(id='header') —"
                    " Header is not part of the widget tree"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp imports Header from loom.tui.header and yields"
                " Header(id='header') in compose"
            ),
        )
