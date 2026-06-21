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


def _ctx_rail_components(
    ratio: float, shuttle_phase: int, state: EngineState
) -> tuple[int, str]:
    """§2.2.3 shuttle pass on fixed rail — pure helper.

    Returns: (shuttle_x, colorized_rail_str)
      - shuttle_x: 0..9 position of shuttle within rail
      - colorized_rail_str: '[$success|warning|error]─...●─...[/]'
    """
    ratio = max(0.0, min(1.0, ratio))
    base_x = round(ratio * (_RAIL_WIDTH - 1))
    offset = 0 if state == "idle" else shuttle_phase * _SHUTTLE_PASS_RANGE
    shuttle_x = max(0, min(_RAIL_WIDTH - 1, base_x + offset))

    rail_chars = [_RAIL_TICK] * _RAIL_WIDTH
    rail_chars[shuttle_x] = _SHUTTLE

    color_open = {
        "ctx-ok": "[$success]",
        "ctx-warn": "[$warning]",
        "ctx-danger": "[$error]",
    }[_ctx_color_class(ratio)]
    return shuttle_x, f"{color_open}{''.join(rail_chars)}[/]"


def _build_ctx_line_components(
    app,
    ctx_tokens: int,
    ctx_window: int,
    engine_state: EngineState,
    shuttle_phase: int,
    *,
    quiet_prefix: bool = False,
) -> tuple[str, str, str]:
    """Shared prefix + rail + tick builder. Used by both StatusBar and ShuttleTickOverlay.
    
    Returns: (prefix_str, rail_str, tick_str)
      - prefix_str: ' loom · model · ⎇ branch · 0t·0tl · ctx: '
      - rail_str:  '[$success|warning|error]─...●─...[/]'
      - tick_str:  '[$text-muted]─...─^─...─[/]' (single ^ at shuttle position, rest blank)
    
    quiet_prefix=True: render prefix with all $text-faint tokens (used by
    ShuttleTickOverlay so the ^ caret is the only visually prominent element
    on the row — the duplicate prefix becomes "ambient noise" matching the
    §9.3 dim tier rather than re-rendering the full §9.3 hierarchy).
    """
    model = getattr(getattr(app, "llm", None), "model", "?")
    git_branch = getattr(app, "_git_branch", "") or ""
    turns = getattr(app, "user_turn_count", 0)
    tools = getattr(app, "tool_call_count", 0)

    if quiet_prefix:
        prefix_parts = [
            "loom",
            model,
        ]
        if git_branch:
            prefix_parts.append(f"⎇ {git_branch}")
        prefix_parts.append(f"{turns}t·{tools}tl")
        joined = " · ".join(prefix_parts)
        prefix = f"[$surface]{joined} ctx:[/] "
    else:
        # StatusBar row: full §9.3 hierarchy
        prefix_parts = [
            "[$text-faint]loom[/]",
            f"[$secondary]{model}[/]",
        ]
        if git_branch:
            prefix_parts.append(
                f"[$text-faint]⎇[/] [$text-muted]{git_branch}[/]"
            )
        prefix_parts.append(f"{turns}t·{tools}tl")
        prefix = f" {_SEP} ".join(prefix_parts) + " [$text-muted]ctx:[/] "

    ctx_window = ctx_window or 1
    ratio = ctx_tokens / ctx_window if ctx_window > 0 else 0.0
    shuttle_x, rail_str = _ctx_rail_components(ratio, shuttle_phase, engine_state)

    # Tick row: same prefix (caller prepends it), then spaces, then ^ at shuttle_x,
    # then spaces. Tick is always $text-muted (NOT the success/warning/error from
    # rail color) — the tick is positional metadata, not a status indicator.
    tick_chars = [" "] * _RAIL_WIDTH
    tick_chars[shuttle_x] = "^"
    tick_str = f"[$text-muted]{''.join(tick_chars)}[/]"

    return prefix, rail_str, tick_str


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
        ctx_window = self.ctx_window or 1
        ratio = self.ctx_tokens / ctx_window if ctx_window > 0 else 0.0
        prefix, rail, _tick = _build_ctx_line_components(
            app, self.ctx_tokens, self.ctx_window, self.engine_state, self.shuttle_phase,
        )
        # prefix already ends with " [$text-muted]ctx:[/] "
        # Append rail + tokens + pct directly after.
        ctx_tail = f"{rail} {_format_tokens(self.ctx_tokens)}/{_format_tokens(self.ctx_window)} ({ratio * 100:.0f}%)"
        engine_badge = _render_engine_badge(self.engine_state)
        elapsed = _format_elapsed(self.elapsed_seconds)
        key_hints = f"[$text-faint]esc ^l / {elapsed}[/]"
        return f"{prefix}{ctx_tail} {_SEP} {engine_badge}   {key_hints} "


class ShuttleTickOverlay(Static):
    """§4.2.1 ^ tick above the shuttle — 1-line widget, sits above StatusBar.

    Mirrors StatusBar's prefix (loom · model · ⎇ branch · Nt·Mtl · ctx:) so the
    `^` glyph appears exactly above the shuttle. Idle freezes the tick at base
    position; active bobs ±1 char at 1Hz (synchronized with shuttle).
    """

    DEFAULT_CSS = """
    ShuttleTickOverlay {
        height: 1;
        background: transparent;
        color: $text-muted;
        padding: 0 1;
    }
    """

    engine_state: reactive[EngineState] = reactive("idle")
    shuttle_phase: reactive[int] = reactive(0)
    ctx_tokens: reactive[int] = reactive(0)
    ctx_window: reactive[int] = reactive(0)

    def render(self) -> str:
        try:
            app = self.app
        except Exception:
            return ""
        prefix, _rail, tick_str = _build_ctx_line_components(
            app, self.ctx_tokens, self.ctx_window, self.engine_state, self.shuttle_phase,
            quiet_prefix=True,
        )
        return prefix + tick_str
