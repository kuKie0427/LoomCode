from __future__ import annotations

from typing import Literal

from textual.reactive import reactive
from textual.widgets import Static

EngineState = Literal["idle", "thinking", "streaming", "executing", "compacting", "error"]

_RAIL_WIDTH = 10
_RAIL_TICK = "─"
_SHUTTLE = "●"
_SHUTTLE_PASS_RANGE = 1

_CTX_WARN_RATIO = 0.60
_CTX_DANGER_RATIO = 0.85

_SEP = "·"


def _format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _format_elapsed(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _ctx_color_class(ratio: float) -> str:
    if ratio >= _CTX_DANGER_RATIO:
        return "ctx-danger"
    if ratio >= _CTX_WARN_RATIO:
        return "ctx-warn"
    return "ctx-ok"


def _ctx_rail_render(ratio: float, shuttle_phase: int, state: EngineState) -> str:
    """§2.2.3 ctx rail with shuttle pass — idle freezes, active passes.

    Note: §4.2.1 spec requires a `^` tick ABOVE the shuttle, but StatusBar.height=1
    (§9.3 cap) physically can't show two lines. Phase indicator is rendered
    inline as `^0`/`^1` on the same line. Strict ^-above-shuttle deferred to P3.
    """
    base_x = round(ratio * (_RAIL_WIDTH - 1))
    offset = 0 if state == "idle" else shuttle_phase * _SHUTTLE_PASS_RANGE
    shuttle_x = max(0, min(_RAIL_WIDTH - 1, base_x + offset))

    rail_chars = [_RAIL_TICK] * _RAIL_WIDTH
    rail_chars[shuttle_x] = _SHUTTLE
    rail_str = "".join(rail_chars)

    color_map = {
        "ctx-ok": "[$success]",
        "ctx-warn": "[$warning]",
        "ctx-danger": "[$error]",
    }
    return f"{color_map[_ctx_color_class(ratio)]}{rail_str}[/]"


def _render_engine_badge(state: EngineState) -> str:
    """§4.2.1 engine badge — 1-glyph state indicator.

    idle  → [$text-muted]● idle[/]
    error → [$error]⊗ error[/]
    其他 (thinking/streaming/executing/compacting) → [$accent]▸ run[/]
    """
    if state == "error":
        return "[$error]⊗ error[/]"
    if state == "idle":
        return "[$text-muted]● idle[/]"
    return "[$accent]▸ run[/]"


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
    }
    """

    turns: reactive[int] = reactive(0)
    tools: reactive[int] = reactive(0)
    ctx_tokens: reactive[int] = reactive(0)
    ctx_window: reactive[int] = reactive(0)
    elapsed_seconds: reactive[int] = reactive(0)
    git_branch: reactive[str] = reactive("")
    engine_state: reactive[EngineState] = reactive("idle")
    shuttle_phase: reactive[int] = reactive(0)

    def render(self) -> str:
        app = self.app
        model = getattr(getattr(app, "llm", None), "model", "?")

        ctx_window = self.ctx_window or 1
        ratio = self.ctx_tokens / ctx_window if ctx_window > 0 else 0.0
        rail = _ctx_rail_render(ratio, self.shuttle_phase, self.engine_state)
        ctx_str = (
            f"[$text-muted]ctx:[/] {rail} "
            f"[$text-muted]^{self.shuttle_phase}[/]"
            f" {_format_tokens(self.ctx_tokens)}/{_format_tokens(self.ctx_window)} "
            f"({ratio * 100:.0f}%)"
        )

        # Opencode-style bottom dock: stats on the left, key hints on the right,
        # separated by a wider gap. Key hints are dimmed in $text-faint so they
        # read as "ambient" — the eye is pulled to the live stats first.
        stat_parts = [
            "[$text-faint]loom[/]",
            f"[$secondary]{model}[/]",
        ]
        if self.git_branch:
            stat_parts.append(
                f"[$text-faint]⎇[/] [$text-muted]{self.git_branch}[/]"
            )
        stat_parts.append(f"{self.turns}t·{self.tools}tl")
        stat_parts.append(ctx_str)
        stat_parts.append(_render_engine_badge(self.engine_state))

        elapsed = _format_elapsed(self.elapsed_seconds)
        key_hints = f"[$text-faint]esc ^l / {elapsed}[/]"

        joined_stats = f" {_SEP} ".join(stat_parts)
        return f" {joined_stats}   {key_hints} "
