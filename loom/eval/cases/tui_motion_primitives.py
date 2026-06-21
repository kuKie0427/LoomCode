"""Eval cases for §2.2.3 motion primitives: shuttle pass + tool marker cycle + section button pulse.

These eval cases lock source-level structural contracts for the 5 motion primitives
defined in docs/tui-design-language.md §2.2.3 (P2 implementation):
- Primitive 1: ctx rail + shuttle pass (covered by tui_ctx_rail.py)
- Primitive 2: ToolCallMarker 1Hz glyph cycle
- Primitive 3: Thinking spinner 5fps (pre-existing, not re-evaluated here)
- Primitive 4: HeaderSectionButton pulse (P2b — uses Python set_interval due to Textual
  CSS parser v8.2.7 limitation; see .sisyphus/notepads/loop-tui-paradigm-p2b/learnings.md)
- Primitive 5: Composer cursor blink (Textual system default, no eval needed)

The eval cases inspect source code (via ``inspect.getsource``) because the test suite
cannot enforce these structural contracts at runtime — they ensure the motion primitives
stay wired correctly across future refactors.
"""
from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult

# ── Primitive 2: ToolCallMarker 1Hz glyph cycle ────────────────────────────────


class TuiToolMarkerCycle3FramesDefined(EvalCase):
    name = "tui-tool-marker-cycle-3-frames-defined"
    description = "ToolCallMarker must declare _RUNNING_GLYPHS with 3 frames: ⊙ ⊚ ◎ (§2.2.3 primitive 2)"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker  # noqa: F401

        if not hasattr(ToolCallMarker, "_RUNNING_GLYPHS"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ToolCallMarker missing _RUNNING_GLYPHS class attribute",
            )
        glyphs = ToolCallMarker._RUNNING_GLYPHS
        if glyphs != ("⊙", "⊚", "◎"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"_RUNNING_GLYPHS must be ('⊙', '⊚', '◎'), got {glyphs!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="_RUNNING_GLYPHS = ('⊙', '⊚', '◎') — 3 frames for 1Hz cycle",
        )


class TuiToolMarkerCycleTimer1Hz(EvalCase):
    name = "tui-tool-marker-cycle-timer-1hz"
    description = "ToolCallMarker._start_cycle_timer must call set_interval(1.0, ..., name='tool-cycle')"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker  # noqa: F401

        source = inspect.getsource(ToolCallMarker)
        if "set_interval(1.0" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "ToolCallMarker must call set_interval(1.0, ...,"
                    " name='tool-cycle') — 1Hz timer for glyph cycle"
                ),
            )
        if 'name="tool-cycle"' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ToolCallMarker must name the cycle interval 'tool-cycle'",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="ToolCallMarker registers 1Hz set_interval named 'tool-cycle'",
        )


class TuiToolMarkerCycleStopsOnComplete(EvalCase):
    name = "tui-tool-marker-cycle-stops-on-complete"
    description = "ToolCallMarker.set_complete must call _stop_cycle_timer() to freeze the cycle"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker  # noqa: F401

        source = inspect.getsource(ToolCallMarker.set_complete)
        lines = source.splitlines()
        stop_called = any("_stop_cycle_timer" in ln for ln in lines[:5])
        complete_set = any("_complete = True" in ln for ln in lines[:10])
        if not stop_called:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "set_complete must call _stop_cycle_timer() in the first few lines"
                    " — freeze the cycle before flipping _complete flag"
                ),
            )
        if not complete_set:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="set_complete must set self._complete = True",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="set_complete stops cycle timer BEFORE flipping _complete flag",
        )


# ── Primitive 4: HeaderSectionButton pulse (Python set_interval — Textual CSS limitation) ─


class TuiHeaderSectionButtonPulseTimerDefined(EvalCase):
    name = "tui-header-section-button-pulse-timer-defined"
    description = (
        "HeaderSectionButton._start_pulse must call set_interval(0.5, ..., name='header-pulse')"
        " — 0.5s Python timer for opacity pulse (Textual CSS does not support @keyframes/animations)"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import HeaderSectionButton  # noqa: F401

        source = inspect.getsource(HeaderSectionButton)
        if "set_interval(0.5" not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "HeaderSectionButton must call set_interval(0.5, ..., name='header-pulse')"
                    " — 0.5s interval = 1Hz toggle rate"
                ),
            )
        if 'name="header-pulse"' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="HeaderSectionButton must name the pulse interval 'header-pulse'",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="HeaderSectionButton registers 0.5s set_interval named 'header-pulse' (1Hz)",
        )


class TuiHeaderSectionButtonPulseClassToggle(EvalCase):
    name = "tui-header-section-button-pulse-class-toggle"
    description = (
        "HeaderSectionButton.update_pulse must add 'pulsing' class when has_count=True,"
        " remove it when has_count=False"
    )

    def run(self) -> EvalResult:
        from loom.tui.header import HeaderSectionButton  # noqa: F401

        if not hasattr(HeaderSectionButton, "update_pulse"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="HeaderSectionButton missing update_pulse method",
            )
        source = inspect.getsource(HeaderSectionButton.update_pulse)
        if 'add_class("pulsing")' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="update_pulse must call add_class('pulsing') when has_count=True",
            )
        if 'remove_class("pulsing")' not in source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="update_pulse must call remove_class('pulsing') when has_count=False",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="update_pulse toggles 'pulsing' class on/off correctly",
        )


# ── ChatLog engine_state reactive propagation ──────────────────────────────────


class TuiChatlogEngineStateReactivePropagation(EvalCase):
    name = "tui-chatlog-engine-state-reactive-propagation"
    description = (
        "ChatLog must declare engine_state reactive + watch_engine_state that fans out"
        " to all _tool_markers.values() (P2a §2.2.3 primitive 2 propagation)"
    )

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ChatLog  # noqa: F401

        if not hasattr(ChatLog, "engine_state"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ChatLog missing engine_state class-level reactive",
            )
        if not hasattr(ChatLog, "watch_engine_state"):
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ChatLog missing watch_engine_state method",
            )
        watch_source = inspect.getsource(ChatLog.watch_engine_state)
        if "_tool_markers" not in watch_source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="ChatLog.watch_engine_state must reference _tool_markers",
            )
        if "marker.engine_state" not in watch_source:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    "ChatLog.watch_engine_state must set marker.engine_state = new"
                    " on each marker"
                ),
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="ChatLog has engine_state reactive + watch_engine_state fan-out",
        )
