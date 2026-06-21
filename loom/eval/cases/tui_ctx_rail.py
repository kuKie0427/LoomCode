"""Eval cases for §2.2.3 primitive 1 (gear-rack advance) and §2.2.2 controlled exception.

These eval cases lock the structural contracts that the test suite cannot
directly enforce (because tests run after the source may have drifted):
- Gear-rack source forbids continuous solid-block fill glyphs (`█`/`░`) but
  allows the gear glyphs (`❋✻✜`) + chain `┅` + un-engaged teeth `┄`
  (§2.2.2 controlled exception: gear-rack IS a fill, but a scoped one).
- _ctx_rail_components gear helper is defined with the new contract.
- AgentTUIApp.on_mount registers a 1Hz interval named 'shuttle-tick'
  (name kept for backward compatibility; phase counter now cycles 0/1/2
  for the 3-frame gear cycle instead of 0↔1).
- _tick_shuttle has the idle early-return guard (unchanged invariant).
- _ctx_rail_components uses the round(ratio * ...) position formula.
"""
from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult

# Gear-rack glyph vocabulary per §2.2.3 primitive 1 + §2.2.2 controlled exception
_GEAR_FRAMES = ("❋", "✻", "✜")
_GEAR_CHAIN_ENGAGED = "┅"
_GEAR_TEETH_UNENGAGED = "┄"
# Continuous solid-block fill glyphs — STILL forbidden (§2.2.2 forbids rectangular fills)
_FORBIDDEN_BLOCKS = ("█", "░")


class TuiCtxRailGearContract(EvalCase):
    name = "tui-ctx-rail-gear-contract"
    description = (
        "loom/tui/status_bar.py forbids rectangular fill glyphs (█/░) but allows "
        "gear-rack glyphs (❋✻✜/┅/┄) per §2.2.2 controlled exception"
    )

    def run(self) -> EvalResult:
        source = inspect.getsource(__import__("loom.tui.status_bar", fromlist=["*"]))
        for bad in _FORBIDDEN_BLOCKS:
            if bad in source:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"loom/tui/status_bar.py contains {bad!r} (rectangular fill) — "
                        "§2.2.2 still forbids continuous solid-block fill bars"
                    ),
                )
        missing: list[str] = []
        for g in _GEAR_FRAMES:
            if g not in source:
                missing.append(g)
        if _GEAR_CHAIN_ENGAGED not in source:
            missing.append(_GEAR_CHAIN_ENGAGED)
        if _GEAR_TEETH_UNENGAGED not in source:
            missing.append(_GEAR_TEETH_UNENGAGED)
        if missing:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "loom/tui/status_bar.py is missing gear-rack glyph(s): "
                    f"{missing} — §2.2.3 primitive 1 gear-rack contract"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "Gear-rack contract: █/░ absent; ❋✻✜/┅/┄ all present "
                "(§2.2.2 controlled exception + §2.2.3 primitive 1)"
            ),
        )


class TuiCtxRailGearHelperDefined(EvalCase):
    name = "tui-ctx-rail-gear-helper-defined"
    description = (
        "_ctx_rail_components gear helper must exist in loom.tui.status_bar "
        "with signature (ratio, phase, state)"
    )

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _ctx_rail_components  # noqa: F401

        sig = inspect.signature(_ctx_rail_components)
        params = list(sig.parameters.keys())
        if params != ["ratio", "phase", "state"]:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"_ctx_rail_components signature must be (ratio, phase, state),"
                    f" got ({', '.join(params)})"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="_ctx_rail_components has gear-helper 3-param signature (ratio, phase, state)",
        )


class TuiCtxRailGearTick1HzInterval(EvalCase):
    name = "tui-ctx-rail-gear-tick-1hz-interval"
    description = (
        "AgentTUIApp.on_mount must register a 1Hz interval named 'shuttle-tick' "
        "for the gear-rack advance (name preserved for backward compatibility)"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp  # noqa: F401

        source = inspect.getsource(AgentTUIApp.on_mount)
        if "set_interval(1.0" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "AgentTUIApp.on_mount must call set_interval(1.0, ...,"
                    " name='shuttle-tick') for the gear-rack 1Hz advance"
                ),
            )
        if 'name="shuttle-tick"' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "AgentTUIApp.on_mount must name the gear-rack interval "
                    "'shuttle-tick' (name kept for backward compatibility)"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="on_mount registers 1Hz set_interval named 'shuttle-tick' for gear-rack advance",
        )


class TuiCtxRailIdleFreeze(EvalCase):
    name = "tui-ctx-rail-idle-freeze"
    description = (
        "_tick_shuttle first control flow must early-return when engine_state == 'idle' "
        "(gear freezes at base frame ❋ while idle — §2.2.2 invariant)"
    )

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
                    detail=(
                        "_tick_shuttle early-returns when engine_state == 'idle' "
                        "(gear freezes at base frame ❋)"
                    ),
                )

        return EvalResult(
            name=self.name,
            passed=False,
            detail="'return' not found immediately after idle guard in _tick_shuttle",
        )


class TuiCtxRailGearPositionFormula(EvalCase):
    name = "tui-ctx-rail-gear-position-formula"
    description = (
        "_ctx_rail_components must use round(ratio * (WIDTH - 1)) for the "
        "gear's base position along the rack"
    )

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _ctx_rail_components  # noqa: F401

        source = inspect.getsource(_ctx_rail_components)
        if "round(ratio *" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "_ctx_rail_components must use round(ratio * ...) for the "
                    "gear's base position on the rack"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "_ctx_rail_components uses round(ratio * (RAIL_WIDTH - 1)) "
                "for the gear base position"
            ),
        )
