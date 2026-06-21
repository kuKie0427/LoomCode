"""Eval cases for §4.2.1 TickOverlay: ShuttleTickOverlay widget + app wiring.

These eval cases lock the structural contracts for the ShuttleTickOverlay
widget defined in loom/tui/status_bar.py and its wiring in loom/tui/app.py:
- ShuttleTickOverlay class existence + reactives + DEFAULT_CSS
- compose() yields TickOverlay before StatusBar
- _build_ctx_line_components helper signature
- tick_str uses $text-muted color (NOT success/warning/error)
- _sync_shuttle_tick_overlay propagates 4 reactives
"""
from __future__ import annotations

import importlib
import inspect

from loom.eval.runner import EvalCase, EvalResult


class TuiShuttleTickOverlayClassDefined(EvalCase):
    name = "tui-shuttle-tick-overlay-class-defined"
    description = (
        "ShuttleTickOverlay must be defined in loom.tui.status_bar with 4 reactive"
        " attributes and correct DEFAULT_CSS (§4.2.1)"
    )

    def run(self) -> EvalResult:
        from loom.tui.status_bar import ShuttleTickOverlay  # noqa: F401

        if not hasattr(ShuttleTickOverlay, "engine_state"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ShuttleTickOverlay missing engine_state reactive",
            )
        if not hasattr(ShuttleTickOverlay, "shuttle_phase"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ShuttleTickOverlay missing shuttle_phase reactive",
            )
        if not hasattr(ShuttleTickOverlay, "ctx_tokens"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ShuttleTickOverlay missing ctx_tokens reactive",
            )
        if not hasattr(ShuttleTickOverlay, "ctx_window"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ShuttleTickOverlay missing ctx_window reactive",
            )

        css = ShuttleTickOverlay.DEFAULT_CSS
        for needle, label in [
            ("height: 1", "height: 1"),
            ("background: transparent", "background: transparent"),
            ("color: $text-muted", "color: $text-muted"),
        ]:
            if needle not in css:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"ShuttleTickOverlay.DEFAULT_CSS must contain '{label}'"
                    ),
                )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "ShuttleTickOverlay has 4 reactives (engine_state, shuttle_phase,"
                " ctx_tokens, ctx_window) + correct DEFAULT_CSS"
            ),
        )


class TuiShuttleTickOverlayAboveStatusBar(EvalCase):
    name = "tui-shuttle-tick-overlay-above-status-bar"
    description = (
        "AgentTUIApp.compose() must yield ShuttleTickOverlay BEFORE"
        " StatusBar in #chrome"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp  # noqa: F401

        source = inspect.getsource(AgentTUIApp.compose)
        if "ShuttleTickOverlay" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="AgentTUIApp.compose() must yield ShuttleTickOverlay",
            )
        if "StatusBar" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="AgentTUIApp.compose() must yield StatusBar",
            )

        tick_idx = source.index("ShuttleTickOverlay")
        bar_idx = source.index("StatusBar")
        if tick_idx < bar_idx:
            return EvalResult(
                name=self.name,
                passed=True,
                detail="compose() yields ShuttleTickOverlay BEFORE StatusBar in #chrome",
            )
        return EvalResult(
            name=self.name,
            passed=False,
            detail="compose() must yield ShuttleTickOverlay before StatusBar",
        )


class TuiShuttleTickOverlayHelperExists(EvalCase):
    name = "tui-shuttle-tick-overlay-helper-exists"
    description = (
        "_build_ctx_line_components must be defined in loom.tui.status_bar with"
        " signature (app, ctx_tokens, ctx_window, engine_state, shuttle_phase)"
        " returning tuple[str, str, str]"
    )

    def run(self) -> EvalResult:
        mod = importlib.import_module("loom.tui.status_bar")
        if not hasattr(mod, "_build_ctx_line_components"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="loom.tui.status_bar missing _build_ctx_line_components",
            )

        fn = mod._build_ctx_line_components
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        expected = ["app", "ctx_tokens", "ctx_window", "engine_state", "shuttle_phase"]
        if params != expected:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"_build_ctx_line_components params must be {expected},"
                    f" got {params}"
                ),
            )
        return_ann = sig.return_annotation
        if return_ann is not inspect.Parameter.empty:
            if str(return_ann) != str(tuple[str, str, str]):
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"_build_ctx_line_components return must be"
                        f" tuple[str, str, str], got {return_ann}"
                    ),
                )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "_build_ctx_line_components has 5-param signature returning"
                " tuple[str, str, str]"
            ),
        )


class TuiShuttleTickOverlayUsesTextMutedColor(EvalCase):
    name = "tui-shuttle-tick-overlay-uses-text-muted-color"
    description = (
        "_build_ctx_line_components must define tick_str using [$text-muted]"
        " (§4.2.1 — tick is positional metadata, not a status indicator)"
    )

    def run(self) -> EvalResult:
        from loom.tui.status_bar import _build_ctx_line_components  # noqa: F401

        source = inspect.getsource(_build_ctx_line_components)
        if "[$text-muted]" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "tick_str must use [$text-muted] — tick is positional metadata,"
                    " not a status indicator (§4.2.1)"
                ),
            )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "tick_str uses [$text-muted] (not success/warning/error) —"
                " tick is positional metadata (§4.2.1)"
            ),
        )


class TuiShuttleTickOverlaySyncHelperExists(EvalCase):
    name = "tui-shuttle-tick-overlay-sync-helper-exists"
    description = (
        "AgentTUIApp._sync_shuttle_tick_overlay must query_one(ShuttleTickOverlay)"
        " and set engine_state, shuttle_phase, ctx_tokens, ctx_window"
    )

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp  # noqa: F401

        if not hasattr(AgentTUIApp, "_sync_shuttle_tick_overlay"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="AgentTUIApp missing _sync_shuttle_tick_overlay method",
            )

        source = inspect.getsource(AgentTUIApp._sync_shuttle_tick_overlay)
        if "query_one(ShuttleTickOverlay)" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "_sync_shuttle_tick_overlay must call"
                    " query_one(ShuttleTickOverlay)"
                ),
            )

        reactives = ["engine_state", "shuttle_phase", "ctx_tokens", "ctx_window"]
        missing = [r for r in reactives if f"tick.{r}" not in source]
        if missing:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"_sync_shuttle_tick_overlay missing assignments for:"
                    f" {', '.join(missing)}"
                ),
            )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "_sync_shuttle_tick_overlay calls query_one(ShuttleTickOverlay)"
                " and sets all 4 reactives"
            ),
        )
