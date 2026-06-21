"""Eval cases for TUI permission modal (f-tui-permission-modal).

Phase F3 Task 6 — 5 EvalCase classes that lock in the permission modal contract:
PermissionScreen, ToolCallMarker, Hooks asker injection, and TUI asker wiring.
"""

from __future__ import annotations

from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: PermissionScreen inherits ModalScreen ────────────────────────────


class TuiPermissionScreenExists(EvalCase):
    name = "tui-permission-screen-exists"
    description = "PermissionScreen inherits ModalScreen"

    def run(self) -> EvalResult:
        from textual.screen import ModalScreen

        from loom.tui.screens import PermissionScreen

        if not issubclass(PermissionScreen, ModalScreen):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"PermissionScreen bases: {[b.__name__ for b in PermissionScreen.__mro__]}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="PermissionScreen is a ModalScreen subclass",
        )


# ── Case 2: ToolCallMarker inherits Static + has set_complete method ──────────


class TuiToolCallMarkerExists(EvalCase):
    name = "tui-tool-call-marker-exists"
    description = "ToolCallMarker inherits Static + has set_complete method"

    def run(self) -> EvalResult:
        from textual.widgets import Static

        from loom.tui.chat_log import ToolCallMarker

        if not issubclass(ToolCallMarker, Static):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"ToolCallMarker bases: {[b.__name__ for b in ToolCallMarker.__mro__]}",
            )
        if not hasattr(ToolCallMarker, "set_complete"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ToolCallMarker missing 'set_complete' method",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="ToolCallMarker(Static) + set_complete() present",
        )


# ── Case 3: ToolCallMarker 3-state contract (§2.2.3 primitive 2) ─────────────


class TuiToolMarkerThreeStatesContract(EvalCase):
    name = "tui-tool-marker-three-states-contract"
    description = "ToolCallMarker: _RUNNING_GLYPHS=3 frames + set_complete done/error contract (§2.2.3)"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker

        # ── 1. _RUNNING_GLYPHS 3-frame cycle ─────────────────────────────────
        if not hasattr(ToolCallMarker, "_RUNNING_GLYPHS"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ToolCallMarker missing _RUNNING_GLYPHS class attribute",
            )
        glyphs = ToolCallMarker._RUNNING_GLYPHS
        if glyphs != ("⊙", "⊚", "◎"):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_RUNNING_GLYPHS must be ('⊙', '⊚', '◎'), got {glyphs!r}",
            )

        # ── 2. set_complete(done): _complete=True, tool-done class ───────────
        marker = ToolCallMarker("bash", "ls")
        if marker._complete:
            return EvalResult(
                name=self.name, passed=False,
                detail="Fresh ToolCallMarker should not be _complete",
            )
        marker.set_complete("output ok", is_error=False)
        if not marker._complete:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(ok) should set _complete=True",
            )
        if marker._is_error:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(ok) should set _is_error=False",
            )
        if not marker.has_class("tool-done"):
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(ok) should add tool-done class",
            )
        if marker.has_class("tool-error"):
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(ok) should NOT have tool-error class",
            )

        # ── 3. set_complete(error): _complete=True, tool-error class ─────────
        marker2 = ToolCallMarker("bash", "ls")
        marker2.set_complete("error output", is_error=True)
        if not marker2._complete:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(error) should set _complete=True",
            )
        if not marker2._is_error:
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(error) should set _is_error=True",
            )
        if not marker2.has_class("tool-error"):
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(error) should add tool-error class",
            )
        if marker2.has_class("tool-done"):
            return EvalResult(
                name=self.name, passed=False,
                detail="set_complete(error) should NOT have tool-done class",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "_RUNNING_GLYPHS=3 frames (⊙,⊚,◎) + "
                "set_complete done→_complete=True,tool-done / "
                "error→_complete=True,tool-error"
            ),
        )


# ── Case 4: Hooks(asker=...) constructor parameter works ─────────────────────


class TuiHooksAskerInjectable(EvalCase):
    name = "tui-hooks-asker-injectable"
    description = "Hooks(asker=...) constructor parameter works"

    def run(self) -> EvalResult:
        from loom.agent.hooks import Hooks

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
        # CRITICAL: import the INSTANCE from loom.agent.loop, not the module
        from loom.agent.loop import hooks
        from loom.tui.app import AgentTUIApp

        original_asker = hooks._asker
        try:
            with patch("loom.tui.app.AgentTUIApp._make_tui_asker") as mock_factory:
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
