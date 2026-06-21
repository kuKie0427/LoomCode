"""Eval cases for TUI collapsible tool output (f-tui-collapsible-tools).

Phase P2 — 5 EvalCase classes that lock in the collapsible-output contract:
CollapsibleToolOutput (Vertical + toggle), ToolCallMarker click-toggles-only
(no modal path), ToolCallModal removed, and inline multi-line rendering
preserves newlines.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: CollapsibleToolOutput inherits Vertical ──────────────────────────


class TuiCollapsibleToolOutputExists(EvalCase):
    name = "tui-collapsible-tool-output-exists"
    description = "CollapsibleToolOutput is a Vertical subclass from textual.containers"

    def run(self) -> EvalResult:
        from textual.containers import Vertical

        from loom.tui.chat_log import CollapsibleToolOutput

        if not issubclass(CollapsibleToolOutput, Vertical):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"CollapsibleToolOutput bases: {[b.__name__ for b in CollapsibleToolOutput.__mro__]}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="CollapsibleToolOutput is a Vertical subclass",
        )


# ── Case 2: toggle() exists and starts without "visible" ─────────────────────


class TuiCollapsibleToolOutputToggleable(EvalCase):
    name = "tui-collapsible-tool-output-toggleable"
    description = "CollapsibleToolOutput.toggle() exists; instance starts with display=False"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import CollapsibleToolOutput

        out = CollapsibleToolOutput("some text")
        if not hasattr(out, "toggle") or not callable(out.toggle):
            return EvalResult(
                name=self.name, passed=False,
                detail="CollapsibleToolOutput.toggle missing or not callable",
            )
        if out.display is not False:
            return EvalResult(
                name=self.name, passed=False,
                detail="Freshly-created CollapsibleToolOutput display should be False",
            )
        out.toggle()
        if out.display is not True:
            return EvalResult(
                name=self.name, passed=False,
                detail="toggle() should set display=True",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="toggle() flips display property on/off",
        )


# ── Case 3: ToolCallMarker click toggles output (no modal path) ─────────────


class TuiToolCallMarkerClickTogglesOutput(EvalCase):
    name = "tui-tool-call-marker-click-toggles-output"
    description = (
        "ToolCallMarker.set_output_widget stores reference; ALL click events "
        "(single-click and double-click) toggle the inline output widget — "
        "no modal is opened, matching ThinkingMarker. _open_modal must NOT exist."
    )

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker

        marker = ToolCallMarker("bash", "{}")
        mock_output = MagicMock()
        marker.set_output_widget(mock_output)

        if marker._output_widget is not mock_output:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_output_widget did not store reference",
            )
        if hasattr(marker, "_open_modal"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ToolCallMarker._open_modal must be removed (design parity with ThinkingMarker)",
            )

        for chain in (1, 2, 3):
            mock_output.toggle.reset_mock()
            event = MagicMock()
            event.chain = chain
            marker.on_click(event)
            if mock_output.toggle.call_count != 1:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"chain={chain}: toggle called {mock_output.toggle.call_count} time(s), expected 1",
                )

        return EvalResult(
            name=self.name, passed=True,
            detail="all click events (chain 1/2/3) toggle inline output; no modal path exists",
        )


# ── Case 4: ToolCallModal class must NOT exist (removed with f-tool-display-p2)


class TuiToolCallModalRemoved(EvalCase):
    name = "tui-tool-call-modal-removed"
    description = (
        "ToolCallModal class must be removed from loom.tui.chat_log — it was "
        "the dead-code modal path triggered by double-click that violated the "
        "design intent (single inline toggle, like ThinkingMarker)."
    )

    def run(self) -> EvalResult:
        from loom import tui

        if hasattr(tui.chat_log, "ToolCallModal"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ToolCallModal still defined in loom.tui.chat_log",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="ToolCallModal removed from loom.tui.chat_log",
        )


# ── Case 5: inline CollapsibleToolOutput must render multi-line content as
#            separate rows (regression for "blank on click" bug) ──────────────


class TuiCollapsibleToolOutputPreservesNewlines(EvalCase):
    name = "tui-collapsible-tool-output-preserves-newlines"
    description = (
        "CollapsibleToolOutput renders multi-line tool output (bash, file "
        "reads) as separate rows. Uses Static (not Markdown) because Markdown's "
        "async update + display-toggle interaction left content un-rendered "
        "after agent turn end (regression: blank-after-turn bug 2026-06-22)."
    )

    def run(self) -> EvalResult:
        from textual.widgets import Static

        from loom.tui.chat_log import CollapsibleToolOutput

        out = CollapsibleToolOutput("line1\nline2\nline3")
        children = list(out.compose())
        if len(children) != 1 or not isinstance(children[0], Static):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"compose() should yield exactly one Static child; got {children!r}",
            )
        rendered = str(children[0].content)
        if rendered != "line1\nline2\nline3":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Static renderable should preserve newlines; got {rendered!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="Static renders multi-line output verbatim (no markdown collapse)",
        )