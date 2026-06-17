"""Eval cases for TUI permission modal (f-tui-permission-modal).

Phase F3 Task 6 — 5 EvalCase classes that lock in the permission modal contract:
PermissionScreen, ToolCallCard, Hooks asker injection, and TUI asker wiring.
"""

from __future__ import annotations

from unittest.mock import patch

from loop.eval.runner import EvalCase, EvalResult

# ── Case 1: PermissionScreen inherits ModalScreen ────────────────────────────


class TuiPermissionScreenExists(EvalCase):
    name = "tui-permission-screen-exists"
    description = "PermissionScreen inherits ModalScreen"

    def run(self) -> EvalResult:
        from textual.screen import ModalScreen

        from loop.tui.screens import PermissionScreen

        if not issubclass(PermissionScreen, ModalScreen):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"PermissionScreen bases: {[b.__name__ for b in PermissionScreen.__mro__]}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="PermissionScreen is a ModalScreen subclass",
        )


# ── Case 2: ToolCallCard inherits Static + has complete method ───────────────


class TuiToolCallCardExists(EvalCase):
    name = "tui-tool-call-card-exists"
    description = "ToolCallCard inherits Static + has complete method"

    def run(self) -> EvalResult:
        from textual.widgets import Static

        from loop.tui.widgets import ToolCallCard

        if not issubclass(ToolCallCard, Static):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"ToolCallCard bases: {[b.__name__ for b in ToolCallCard.__mro__]}",
            )
        if not hasattr(ToolCallCard, "complete"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ToolCallCard missing 'complete' method",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="ToolCallCard(Static) + complete() present",
        )


# ── Case 3: 3 states render() returns Text without error ─────────────────────


class TuiToolCardThreeStatesRender(EvalCase):
    name = "tui-tool-card-three-states-render"
    description = "ToolCallCard.render() returns Text for running/completed/error states"

    def run(self) -> EvalResult:
        from rich.text import Text

        from loop.tui.widgets import ToolCallCard

        card = ToolCallCard("bash", {"command": "ls"}, "t-1")
        for state in ["running", "completed", "error"]:
            if state == "running":
                result = card.render()
            else:
                card.state = state
                card.output = "some output here"
                result = card.render()
            if not isinstance(result, Text):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"State '{state}' render() returned {type(result).__name__}, expected Text",
                )
        return EvalResult(
            name=self.name, passed=True,
            detail="All 3 states (running/completed/error) render Text",
        )


# ── Case 4: Hooks(asker=...) constructor parameter works ─────────────────────


class TuiHooksAskerInjectable(EvalCase):
    name = "tui-hooks-asker-injectable"
    description = "Hooks(asker=...) constructor parameter works"

    def run(self) -> EvalResult:
        from loop.agent.hooks import Hooks

        mock_asker = lambda n, a, r: "allow"  # noqa: E731
        h = Hooks(asker=mock_asker)
        if h._asker is not mock_asker:
            return EvalResult(
                name=self.name, passed=False,
                detail="h._asker is not the injected mock_asker",
            )
        result = h._ask_user("bash", {"command": "x"}, "reason")
        if result != "allow":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_ask_user returned '{result}', expected 'allow'",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="Hooks(asker=...) injects and _ask_user delegates to it",
        )


# ── Case 5: AgentTUIApp.__init__ sets hooks._asker ───────────────────────────


class TuiAppInjectsTuiAskerAfterApplyConfig(EvalCase):
    name = "tui-app-injects-tui-asker-after-apply-config"
    description = "AgentTUIApp.__init__ sets hooks._asker to a TUI asker (after apply_config)"

    def run(self) -> EvalResult:
        # CRITICAL: import the INSTANCE from loop.agent.loop, not the module
        from loop.agent.loop import hooks
        from loop.tui.app import AgentTUIApp

        original_asker = hooks._asker
        try:
            with patch("loop.tui.app.AgentTUIApp._make_tui_asker") as mock_factory:
                mock_factory.return_value = lambda n, a, r: "allow"
                AgentTUIApp()
                result = hooks._asker("bash", {}, "r")
                if result != "allow":
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=f"hooks._asker returned '{result}', expected 'allow'",
                    )
        finally:
            hooks._asker = original_asker
        return EvalResult(
            name=self.name, passed=True,
            detail="AgentTUIApp.__init__ injects _make_tui_asker result into hooks._asker",
        )
