"""Eval cases for the TUI Header (summary rail) — f-tui-header-summary-rail.

Locks the spec contract from ``docs/tui-design-language.md`` §4.3 and the
per-section toggle design locked 2026-06-20:

  * Pure helper functions (``mcp_glyph``, ``todo_glyph``, ``subagent_glyph``)
    return worst-state aggregate glyphs per spec §4.3.1.
  * Subagent glyph returns ``(None, 0)`` when count=0 (entire section
    hidden per spec §4.3.1 hide rule).
  * ``AgentTUIApp.compose`` yields ``Header`` BEFORE ``ChatLog`` so the
    dock-top invariant holds (§3 ergonomic grid).
  * Header has no ``transition`` CSS anywhere (spec §6 — instant toggle).
  * Per-section toggle design: Header composes 3 HeaderSectionButton
    children (one per subsystem), each independently clickable. HeaderOverlay
    takes a ``section`` parameter and renders only that section. Only one
    overlay is ever mounted (mutual exclusion).

The widget itself (Header, HeaderOverlay, HeaderSectionButton) and
DEFAULT_MOCK_STATE are exercised in ``tests/test_tui_header.py``; this
module locks the spec contract and the app-level wiring invariants.
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


# ── Case 7: No transition CSS anywhere in Header-related widgets ──────────────


class HeaderInstantToggleNoTransition(EvalCase):
    """Per spec §6, no Header-related widget may have a CSS transition.

    The HeaderOverlay CSS block lives in AgentTUIApp.CSS (positional
    concerns). The Header.DEFAULT_CSS holds the Header's own styling.
    Both must be transition-free.
    """

    name = "header-instant-toggle-no-transition"
    description = (
        "Neither AgentTUIApp.CSS (HeaderOverlay block) nor Header.DEFAULT_CSS"
        " contains any 'transition' property — spec §6 mandates instant"
        " mount/remove (no easing)"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp
        from loom.tui.header import Header

        for css_name, css in [
            ("AgentTUIApp.CSS", AgentTUIApp.CSS),
            ("Header.DEFAULT_CSS", Header.DEFAULT_CSS),
        ]:
            if "HeaderOverlay" not in css and "Header" not in css:
                continue
            for i, line in enumerate(css.splitlines(), 1):
                if "transition" in line:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=(
                            f"Header CSS must have no transition (spec §6),"
                            f" found in {css_name} line {i}: {line!r}"
                        ),
                    )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp.CSS + Header.DEFAULT_CSS — no transition CSS — "
                "instant mount/remove"
            ),
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
        if 'Header(id="header")' not in src:
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


# ── Case 9: Per-section toggle message defined on Header ──────────────────────


class HeaderSectionToggleMessageDefined(EvalCase):
    """Per-section toggle requires Header.SectionToggle message class.

    Each HeaderSectionButton posts Header.SectionToggle(section) when
    clicked — the App handles expand / switch / collapse based on the
    current overlay state.
    """

    name = "header-section-toggle-message-defined"
    description = (
        "Header class defines SectionToggle message with a ``section``"
        " field — per-section toggle contract"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import Header

        msg_cls = getattr(Header, "SectionToggle", None)
        if msg_cls is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="Header class has no SectionToggle message class",
            )
        # Construct a sample message to verify the field exists.
        try:
            msg = msg_cls("mcp")
        except TypeError as e:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Header.SectionToggle('mcp') raised TypeError: {e} —"
                    f" missing required field"
                ),
            )
        if not hasattr(msg, "section"):
            return EvalResult(
                name=self.name, passed=False,
                detail="Header.SectionToggle message has no .section attribute",
            )
        if msg.section != "mcp":
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Header.SectionToggle.section == {msg.section!r},"
                    f" expected 'mcp'"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "Header.SectionToggle('mcp') → section='mcp' — per-section"
                " toggle contract defined"
            ),
        )


# ── Case 10: Header composes 3 HeaderSectionButton children ─────────────────


class HeaderThreeSectionButtons(EvalCase):
    """Per spec §4.3.1, the collapsed line has 3 sections (MCP/Todo/Subagent).
    Each is an independently clickable HeaderSectionButton.
    """

    name = "header-three-section-buttons-in-compose"
    description = (
        "Header.compose yields 3 HeaderSectionButton children, one per"
        " VALID_SECTIONS entry (mcp/todo/subagent), in spec order"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import VALID_SECTIONS, Header, HeaderSectionButton

        header = Header()
        yielded = list(header.compose())
        if len(yielded) != 3:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Header.compose should yield 3 section buttons, got"
                    f" {len(yielded)}"
                ),
            )
        for btn, expected in zip(yielded, VALID_SECTIONS, strict=True):
            if not isinstance(btn, HeaderSectionButton):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"Header.compose yielded non-HeaderSectionButton:"
                        f" {type(btn).__name__}"
                    ),
                )
            if btn.section != expected:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"Button section mismatch: expected {expected!r},"
                        f" got {btn.section!r}"
                    ),
                )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                f"Header.compose yields 3 HeaderSectionButton children"
                f" (sections: {[b.section for b in yielded]})"
            ),
        )


# ── Case 11: HeaderOverlay accepts section parameter and exposes .section ──


class HeaderOverlayHasSectionAttribute(EvalCase):
    """HeaderOverlay takes a ``section`` constructor arg and exposes .section
    so the App can read it back to decide expand/switch/collapse."""

    name = "header-overlay-has-section-attribute"
    description = (
        "HeaderOverlay(section=..., state=...) stores the section arg"
        " as a public .section attribute for the App to read"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import HeaderOverlay, HeaderState, MCPServer

        state = HeaderState(mcps=[MCPServer("db", "connected")])
        overlay = HeaderOverlay(section="mcp", state=state)
        if overlay.section != "mcp":
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"HeaderOverlay.section == {overlay.section!r},"
                    f" expected 'mcp'"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="HeaderOverlay(section='mcp').section == 'mcp' — public attr",
        )


# ── Case 12: App has ESC binding for collapse_header ─────────────────────────


class HeaderEscBindingRegistered(EvalCase):
    """Per spec §4.3.2, ESC collapses the overlay. The BINDINGS list must
    register this action."""

    name = "header-esc-binding-registered"
    description = (
        "AgentTUIApp.BINDINGS contains an 'escape' binding wired to the"
        " action_collapse_header method (spec §4.3.2)"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        bindings = getattr(AgentTUIApp, "BINDINGS", [])
        # BINDINGS is a list of (key, action, description) tuples (Textual).
        has_esc = False
        has_action = False
        for binding in bindings:
            # Textual Binding is a Binding object with .key and .action
            key = getattr(binding, "key", binding[0] if isinstance(binding, tuple) else None)
            action = getattr(binding, "action", binding[1] if isinstance(binding, tuple) else None)
            if key == "escape":
                has_esc = True
                if action == "collapse_header":
                    has_action = True
        if not has_esc:
            return EvalResult(
                name=self.name, passed=False,
                detail="AgentTUIApp.BINDINGS missing 'escape' entry",
            )
        if not has_action:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp.BINDINGS has 'escape' but it's not wired"
                    " to action_collapse_header"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp.BINDINGS has 'escape' → collapse_header — "
                "ESC collapses the overlay per spec §4.3.2"
            ),
        )


# ── Case 13: App has on_header_section_toggle handler ────────────────────────


class HeaderAppHasSectionToggleHandler(EvalCase):
    """Per-section toggle: the App must have on_header_section_toggle."""

    name = "header-app-on-header-section-toggle-defined"
    description = (
        "AgentTUIApp defines on_header_section_toggle(message) handler —"
        " per-section expand/switch/collapse logic"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        handler = getattr(AgentTUIApp, "on_header_section_toggle", None)
        if handler is None:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp has no on_header_section_toggle method —"
                    " per-section toggle not wired"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp.on_header_section_toggle is defined —"
                " per-section toggle handler wired"
            ),
        )


# ── Case 14: App has action_collapse_header for ESC ──────────────────────────


class HeaderAppHasCollapseAction(EvalCase):
    """The App must define action_collapse_header (called by ESC binding)."""

    name = "header-app-action-collapse-header-defined"
    description = (
        "AgentTUIApp defines action_collapse_header() — invoked by the"
        " 'escape' BINDING to collapse the overlay"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        action = getattr(AgentTUIApp, "action_collapse_header", None)
        if action is None:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "AgentTUIApp has no action_collapse_header method —"
                    " ESC binding has no target"
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "AgentTUIApp.action_collapse_header is defined — ESC binding"
                " wired to collapse action"
            ),
        )