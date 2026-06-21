"""Tests for _ctx_rail_components — §2.2.3 primitive 1 gear-rack advance.

Pure helper tests: no Textual app needed. Direct invocation only.
"""
from __future__ import annotations

import re

from loom.tui.status_bar import _ctx_rail_components

_GEAR_GLYPHS = ("\u274b", "\u273b", "\u271c")  # ❋ ✻ ✜
_RAIL_CELL_CLASS = r"[\u274b\u273b\u271c\u2505\u2504]"  # gear frames + chain + tooth


def _cells(out: str) -> str:
    return "".join(re.findall(_RAIL_CELL_CLASS, out))


def _gear_pos(cells: str) -> int:
    for g in _GEAR_GLYPHS:
        if g in cells:
            return cells.index(g)
    return -1


def test_ctx_rail_idle_zero_ratio():
    out = _ctx_rail_components(0.0, 0, "idle")
    assert out.count("\u274b") == 1
    # ratio=0 → pos=0 → first cell is the gear itself (no chain cells).
    assert out.startswith("[$accent-light]\u274b"), (
        f"gear should be at position 0 for ratio=0, got: {out!r}"
    )


def test_ctx_rail_idle_full_ratio():
    out = _ctx_rail_components(1.0, 0, "idle")
    assert out.count("\u274b") == 1
    assert _cells(out).endswith("\u274b"), (
        f"gear should be at last cell for ratio=1.0, got: {_cells(out)!r}"
    )


def test_ctx_rail_idle_freezes_at_phase_zero():
    """§2.1 rule 2: idle freezes gear regardless of phase."""
    out_phase0 = _ctx_rail_components(0.5, 0, "idle")
    out_phase1 = _ctx_rail_components(0.5, 1, "idle")
    assert out_phase0 == out_phase1, (
        f"idle must freeze gear; phase0={out_phase0!r} phase1={out_phase1!r}"
    )


def test_ctx_rail_active_phase_zero_frame():
    """Active phase=0 → base frame ❋ (U+274B)."""
    out = _ctx_rail_components(0.5, 0, "executing")
    assert "\u274b" in out, (
        f"phase=0 active: gear frame should be base ❋, got: {out!r}"
    )


def test_ctx_rail_active_phase_one_frame():
    """Active phase=1 → rotating frame ✻ (U+273B)."""
    out = _ctx_rail_components(0.5, 1, "executing")
    assert "\u273b" in out, (
        f"phase=1 active: gear frame should be rotating ✻, got: {out!r}"
    )


def test_ctx_rail_active_phase_two_frame():
    """Active phase=2 → mid frame ✜ (U+271C)."""
    out = _ctx_rail_components(0.5, 2, "executing")
    assert "\u271c" in out, (
        f"phase=2 active: gear frame should be mid ✜, got: {out!r}"
    )


def test_ctx_rail_active_position_is_pure_ratio():
    """Active gear position is pure round(ratio * (WIDTH - 1)) — no phase bob.

    WIDTH=14, ratio=0.5 → pos = round(6.5) = 6 (banker's rounding).
    All 3 phases must place the gear glyph at the same cell index.
    """
    pos0 = _gear_pos(_cells(_ctx_rail_components(0.5, 0, "executing")))
    pos1 = _gear_pos(_cells(_ctx_rail_components(0.5, 1, "executing")))
    pos2 = _gear_pos(_cells(_ctx_rail_components(0.5, 2, "executing")))
    assert pos0 == pos1 == pos2 == 6, (
        f"active gear position must be pure ratio (pos=6 for 0.5), "
        f"got phase0={pos0}, phase1={pos1}, phase2={pos2}"
    )


def test_ctx_rail_color_thresholds():
    """§8.3 thresholds: 0.5→success, 0.7→warning, 0.9→error."""
    out_ok = _ctx_rail_components(0.5, 0, "executing")
    out_warn = _ctx_rail_components(0.7, 0, "executing")
    out_err = _ctx_rail_components(0.9, 0, "executing")
    assert "[$success]" in out_ok, f"ratio 0.5 should be success: {out_ok!r}"
    assert "[$warning]" in out_warn, f"ratio 0.7 should be warning: {out_warn!r}"
    assert "[$error]" in out_err, f"ratio 0.9 should be error: {out_err!r}"


def test_ctx_rail_clamps_out_of_range_ratio():
    """ratio < 0 → position 0; ratio > 1 → last position (13)."""
    cells_neg = _cells(_ctx_rail_components(-0.1, 0, "executing"))
    cells_over = _cells(_ctx_rail_components(1.5, 0, "executing"))
    assert cells_neg[0] == "\u274b", (
        f"negative ratio must clamp to pos 0, got: {cells_neg!r}"
    )
    assert cells_over[-1] == "\u274b", (
        f"over-1 ratio must clamp to last pos, got: {cells_over!r}"
    )


def test_ctx_rail_no_fill_bar_glyphs():
    """§2.2.4 forbidden: no █ (fill) or ░ (empty) anywhere in output."""
    for ratio in (0.0, 0.3, 0.5, 0.7, 0.9, 1.0):
        for phase in (0, 1, 2):
            for state in ("idle", "executing"):
                out = _ctx_rail_components(ratio, phase, state)
                assert "\u2588" not in out, (
                    f"fill glyph in ratio={ratio} phase={phase} state={state}: {out!r}"
                )
                assert "\u2591" not in out, (
                    f"empty glyph in ratio={ratio} phase={phase} state={state}: {out!r}"
                )