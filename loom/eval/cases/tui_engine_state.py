"""Eval cases for the §2.2.1 EngineState type and §4.2.1 engine badge.

Covers:
1. All 6 EngineState literals defined in status_bar
2. _render_engine_badge has 6 explicit branches (post-revamp)
3. AgentTUIApp has a reactive engine_state field
4. run_agent_turn callbacks explicitly set all 6 engine states
"""

from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult


class TuiEngineState6StatesDefined(EvalCase):
    name = "tui-engine-state-6-states-defined"
    description = "loom.tui.status_bar contains all 6 EngineState literals"

    def run(self) -> EvalResult:
        from loom.tui import status_bar

        source = inspect.getsource(status_bar)
        for state in (
            "idle",
            "thinking",
            "streaming",
            "executing",
            "compacting",
            "error",
        ):
            if f'"{state}"' not in source:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Literal {state!r} not found in status_bar source",
                )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="All 6 EngineState literals present in status_bar",
        )


class TuiEngineBadge6RenderPaths(EvalCase):
    name = "tui-engine-badge-6-render-paths"
    description = (
        "_render_engine_badge has 6 explicit branches (post-revamp):"
        " idle, thinking, streaming, executing, compacting, error"
    )

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _render_engine_badge

        source = inspect.getsource(_render_engine_badge)
        checks = {
            "idle branch": 'state == "idle"' in source,
            "thinking branch": 'state == "thinking"' in source,
            "streaming branch": 'state == "streaming"' in source,
            "executing branch": 'state == "executing"' in source,
            "compacting branch": 'state == "compacting"' in source,
            "error branch": 'state == "error"' in source,
        }
        missing = [name for name, ok in checks.items() if not ok]
        if missing:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"Missing branches in _render_engine_badge: {missing}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "All 6 render paths present (idle / thinking / streaming /"
                " executing / compacting / error)"
            ),
        )


class TuiAppEngineStateReactiveDefined(EvalCase):
    name = "tui-app-engine-state-reactive-defined"
    description = "AgentTUIApp.engine_state is a textual.reactive field"

    def run(self) -> EvalResult:
        from textual.reactive import reactive

        from loom.tui.app import AgentTUIApp

        field = AgentTUIApp.__dict__.get("engine_state")
        if field is None:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="AgentTUIApp has no engine_state class attribute",
            )
        if not isinstance(field, reactive):
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"engine_state is {type(field).__name__}, not textual.reactive",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="AgentTUIApp.engine_state is a textual.reactive instance",
        )


class TuiAppCallbacksSetEngineState(EvalCase):
    name = "tui-app-callbacks-set-engine-state"
    description = "run_agent_turn callbacks explicitly set all 6 engine states"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        source = inspect.getsource(AgentTUIApp.run_agent_turn)
        expected = {"thinking", "streaming", "executing", "compacting", "error", "idle"}
        found = set()
        for state in expected:
            if f'"{state}"' in source:
                found.add(state)
        missing = expected - found
        if missing:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"Missing state strings in run_agent_turn: {missing}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="All 6 engine states explicitly set in run_agent_turn callbacks",
        )
