"""Tests for _ctx_rail_render — §2.2.3 primitive 1 shuttle pass on ctx rail.

Pure helper tests: no Textual app needed. Direct invocation only.
"""
from __future__ import annotations

from loom.tui.status_bar import _ctx_rail_render


def test_ctx_rail_idle_zero_ratio():
    # ratio=0.0, state=idle → shuttle at position 0 (base_x = round(0 * 9) = 0, offset=0)
    out = _ctx_rail_render(0.0, 0, "idle")
    assert out.count("\u25cf") == 1
    # Position 0: ●─────...
    assert out[out.find("[$") + len("[$success]"):].startswith("\u25cf"), (
        f"shuttle should be at position 0 for ratio=0, got: {out!r}"
    )


def test_ctx_rail_idle_full_ratio():
    # ratio=1.0, state=idle → shuttle at position 9 (base_x = round(1 * 9) = 9)
    out = _ctx_rail_render(1.0, 0, "idle")
    assert out.count("\u25cf") == 1
    # Find the rail portion between [$token] and [/]
    import re

    rail = re.search(r"\[\$(?:success|warning|error)\](.*?)\[/\]", out).group(1)
    assert rail.endswith("\u25cf"), f"shuttle should be at last position for ratio=1.0, got: {rail!r}"


def test_ctx_rail_idle_freezes_at_phase_zero():
    """Spec §2.1 rule 2: idle freezes shuttle regardless of phase."""
    # phase=1 manually set, but state=idle → shuttle stays at base
    out_phase0 = _ctx_rail_render(0.5, 0, "idle")
    out_phase1 = _ctx_rail_render(0.5, 1, "idle")
    assert out_phase0 == out_phase1, (
        f"idle must freeze shuttle; phase0={out_phase0!r} phase1={out_phase1!r}"
    )


def test_ctx_rail_active_phase_zero():
    # ratio=0.5, state=executing, phase=0 → base position only
    out = _ctx_rail_render(0.5, 0, "executing")
    import re

    rail = re.search(r"\[\$(?:success|warning|error)\](.*?)\[/\]", out).group(1)
    # base_x = round(0.5 * 9) = 4 (banker's rounding: round(4.5) = 4)
    assert rail[4] == "\u25cf", f"phase=0 active: shuttle at base pos 4, got: {rail!r}"


def test_ctx_rail_active_phase_one():
    # ratio=0.5, state=executing, phase=1 → base + 1 = 5
    out = _ctx_rail_render(0.5, 1, "executing")
    import re

    rail = re.search(r"\[\$(?:success|warning|error)\](.*?)\[/\]", out).group(1)
    # base_x = 4, offset = phase * 1 = 1, shuttle_x = 5
    assert rail[5] == "\u25cf", f"phase=1 active: shuttle at base+1=5, got: {rail!r}"


def test_ctx_rail_color_thresholds():
    """§8.3 thresholds: 0.5→success, 0.7→warning, 0.9→error."""
    out_ok = _ctx_rail_render(0.5, 0, "executing")
    out_warn = _ctx_rail_render(0.7, 0, "executing")  # >= 0.60 warn threshold
    out_err = _ctx_rail_render(0.9, 0, "executing")  # >= 0.85 danger threshold
    assert "[$success]" in out_ok, f"ratio 0.5 should be success: {out_ok!r}"
    assert "[$warning]" in out_warn, f"ratio 0.7 should be warning: {out_warn!r}"
    assert "[$error]" in out_err, f"ratio 0.9 should be error: {out_err!r}"


def test_ctx_rail_clamps_out_of_range_ratio():
    """ratio < 0 → base 0; ratio > 1 → base 9."""
    out_neg = _ctx_rail_render(-0.1, 0, "executing")
    out_over = _ctx_rail_render(1.5, 0, "executing")
    import re

    rail_neg = re.search(r"\[\$(?:success|warning|error)\](.*?)\[/\]", out_neg).group(1)
    rail_over = re.search(r"\[\$(?:success|warning|error)\](.*?)\[/\]", out_over).group(1)
    assert rail_neg[0] == "\u25cf", f"negative ratio must clamp to pos 0, got: {rail_neg!r}"
    assert rail_over[-1] == "\u25cf", f"over-1 ratio must clamp to last pos, got: {rail_over!r}"


def test_ctx_rail_no_fill_bar_glyphs():
    """§2.2.4 forbidden: no █ (fill) or ░ (empty) anywhere in output."""
    for ratio in (0.0, 0.3, 0.5, 0.7, 0.9, 1.0):
        for phase in (0, 1):
            for state in ("idle", "executing"):
                out = _ctx_rail_render(ratio, phase, state)
                assert "\u2588" not in out, (
                    f"fill glyph in ratio={ratio} phase={phase} state={state}: {out!r}"
                )
                assert "\u2591" not in out, (
                    f"empty glyph in ratio={ratio} phase={phase} state={state}: {out!r}"
                )
