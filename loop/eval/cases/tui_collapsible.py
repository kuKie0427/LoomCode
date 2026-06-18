"""Eval cases for TUI collapsible tool output (f-tui-collapsible-tools).

Phase P2 — 3 EvalCase classes that lock in the collapsible-output contract:
CollapsibleToolOutput (Vertical + toggle), and ToolCallMarker click-toggle +
double-click-modal behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from loop.eval.runner import EvalCase, EvalResult

# ── Case 1: CollapsibleToolOutput inherits Vertical ──────────────────────────


class TuiCollapsibleToolOutputExists(EvalCase):
    name = "tui-collapsible-tool-output-exists"
    description = "CollapsibleToolOutput is a Vertical subclass from textual.containers"

    def run(self) -> EvalResult:
        from textual.containers import Vertical

        from loop.tui.chat_log import CollapsibleToolOutput

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
    description = "CollapsibleToolOutput.toggle() exists; instance starts without 'visible' class"

    def run(self) -> EvalResult:
        from loop.tui.chat_log import CollapsibleToolOutput

        out = CollapsibleToolOutput("some text")
        if not hasattr(out, "toggle") or not callable(out.toggle):
            return EvalResult(
                name=self.name, passed=False,
                detail="CollapsibleToolOutput.toggle missing or not callable",
            )
        if out.has_class("visible"):
            return EvalResult(
                name=self.name, passed=False,
                detail="Freshly-created CollapsibleToolOutput should not have 'visible' class",
            )
        out.toggle()
        if not out.has_class("visible"):
            return EvalResult(
                name=self.name, passed=False,
                detail="toggle() did not add 'visible' class",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="toggle() flips 'visible' class on/off",
        )


# ── Case 3: ToolCallMarker single-click toggles output, double-click opens modal


class TuiToolCallMarkerClickTogglesOutput(EvalCase):
    name = "tui-tool-call-marker-click-toggles-output"
    description = (
        "ToolCallMarker.set_output_widget stores reference; single-click toggles "
        "output (does NOT push modal); double-click opens modal"
    )

    def run(self) -> EvalResult:
        from loop.tui.chat_log import ToolCallMarker

        marker = ToolCallMarker("bash", "{}")
        mock_output = MagicMock()
        marker.set_output_widget(mock_output)

        if marker._output_widget is not mock_output:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_output_widget did not store reference",
            )

        with patch.object(marker, "_open_modal") as open_modal:
            single_event = MagicMock()
            single_event.chain = 1
            marker.on_click(single_event)

            if mock_output.toggle.call_count != 1:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"single-click: toggle called {mock_output.toggle.call_count} time(s), expected 1",
                )
            if open_modal.call_count != 0:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"single-click opened modal ({open_modal.call_count}x), should not",
                )

            double_event = MagicMock()
            double_event.chain = 2
            marker.on_click(double_event)

            if open_modal.call_count != 1:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"double-click: _open_modal called {open_modal.call_count} time(s), expected 1",
                )

        return EvalResult(
            name=self.name, passed=True,
            detail="single-click toggles output (no modal); double-click opens modal",
        )