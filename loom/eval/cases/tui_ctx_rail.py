"""Eval cases for §2.2.3 primitive 1: ctx rail + shuttle pass.

These eval cases lock the structural contracts that the test suite cannot
directly enforce (because tests run after the source may have drifted):
- No fill bar glyphs in status_bar source
- _ctx_rail_render helper is defined
- App's on_mount registers a 1Hz shuttle-tick interval
- _tick_shuttle has the idle early-return
- _ctx_rail_render uses the round(ratio * ...) position formula
"""
from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult


class TuiCtxRailNoFillBar(EvalCase):
    name = "tui-ctx-rail-no-fill-bar"
    description = "loom/tui/status_bar.py must not contain █ or ░ fill-bar glyphs (§2.2.4 forbidden)"

    def run(self) -> EvalResult:
        source = inspect.getsource(__import__("loom.tui.status_bar", fromlist=["*"]))
        if "█" in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="loom/tui/status_bar.py contains █ (fill bar) — §2.2.4 forbids it",
            )
        if "░" in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="loom/tui/status_bar.py contains ░ (empty bar) — §2.2.4 forbids it",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="No fill-bar glyphs (█ or ░) found in status_bar source",
        )


class TuiCtxRailShuttleHelperDefined(EvalCase):
    name = "tui-ctx-rail-shuttle-helper-defined"
    description = "_ctx_rail_render pure helper must be defined in loom.tui.status_bar"

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _ctx_rail_render  # noqa: F401

        sig = inspect.signature(_ctx_rail_render)
        params = list(sig.parameters.keys())
        if params != ["ratio", "shuttle_phase", "state"]:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"_ctx_rail_render signature must be (ratio, shuttle_phase, state),"
                    f" got ({', '.join(params)})"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="_ctx_rail_render has correct 3-param signature",
        )


class TuiCtxRailShuttleTick1HzInterval(EvalCase):
    name = "tui-ctx-rail-shuttle-tick-1hz-interval"
    description = "AgentTUIApp.on_mount must register a 1Hz interval named 'shuttle-tick'"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp  # noqa: F401

        source = inspect.getsource(AgentTUIApp.on_mount)
        if "set_interval(1.0" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "AgentTUIApp.on_mount must call set_interval(1.0, ...,"
                    " name='shuttle-tick')"
                ),
            )
        if 'name="shuttle-tick"' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="AgentTUIApp.on_mount must name the shuttle interval 'shuttle-tick'",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="on_mount registers 1Hz set_interval named 'shuttle-tick'",
        )


class TuiCtxRailIdleFreeze(EvalCase):
    name = "tui-ctx-rail-idle-freeze"
    description = "_tick_shuttle first control flow must early-return when engine_state == 'idle'"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp  # noqa: F401

        source = inspect.getsource(AgentTUIApp._tick_shuttle)
        lines = source.splitlines()

        guard_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('if self.engine_state == "idle"'):
                guard_idx = i
                break

        if guard_idx is None:
            return EvalResult(
                name=self.name,
                passed=False,
                detail='_tick_shuttle must check `if self.engine_state == "idle":`',
            )

        for i in range(guard_idx + 1, min(guard_idx + 3, len(lines))):
            if lines[i].strip() == "return":
                return EvalResult(
                    name=self.name,
                    passed=True,
                    detail="_tick_shuttle early-returns when engine_state == 'idle'",
                )

        return EvalResult(
            name=self.name,
            passed=False,
            detail="'return' not found immediately after idle guard in _tick_shuttle",
        )


class TuiCtxRailShuttlePositionFormula(EvalCase):
    name = "tui-ctx-rail-shuttle-position-formula"
    description = "_ctx_rail_render must use round(ratio * ...) for the shuttle base position"

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _ctx_rail_render  # noqa: F401

        source = inspect.getsource(_ctx_rail_render)
        if "round(ratio *" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="_ctx_rail_render must use round(ratio * ...) for base shuttle position",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="_ctx_rail_render uses round(ratio * (_RAIL_WIDTH - 1)) for base position",
        )
