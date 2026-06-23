from __future__ import annotations

from typing import Literal

from textual.reactive import reactive
from textual.widgets import Static

from loom.agent.credential import credentials
from loom.agent.providers.registry import parse_model_id

EngineState = Literal["idle", "thinking", "streaming", "executing", "compacting", "error"]

# §9.3 StatusBar (post-revamp) — gear-rack ctx rail primitives.
# Rail widened from 10 to 14 cells so the gear occupies a stable cell
# (gears must not straddle two cells — verified at the terminal-rendering
# layer; ⚙ was rejected because its emoji variant renders 2-wide and breaks
# alignment).
_RAIL_WIDTH = 14
# Gear frames: ❋ (U+274B) base / ✻ (U+273B) rotating / ✜ (U+271C) mid.
# All width=1, geometric — stable at terminal layer.
_GEAR_FRAMES = ("❋", "✻", "✜")
# Engaged chain (already-transmitted) ┅ U+2505 — threshold-colored.
_CHAIN = "┅"
# Un-engaged teeth (text-faint) ┄ U+2504 — always $text-faint.
_TOOTH = "┄"

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


def _semantic_token(ratio: float) -> str:
    """Map ratio → Rich markup opening token for the threshold class."""
    return {
        "ctx-ok": "[$success]",
        "ctx-warn": "[$warning]",
        "ctx-danger": "[$error]",
    }[_ctx_color_class(ratio)]


def _active_tier(field: str, is_active: bool) -> str:
    """§9.3 active-boost: identity + session tier promoted one tier brighter
    when engine_state != idle. Returns Rich markup opening token.
    """
    if is_active:
        # active: model→$secondary, branch glyph→$text-muted, branch name→$text-muted,
        # turns·tools→$foreground
        return {
            "model": "[$secondary]",
            "branch_glyph": "[$text-muted]",
            "branch_name": "[$text-muted]",
            "stats": "[$foreground]",
        }[field]
    # idle: muted baseline
    return {
        "model": "[$text-muted]",
        "branch_glyph": "[$text-faint]",
        "branch_name": "[$text-faint]",
        "stats": "[$text-muted]",
    }[field]


def _ctx_rail_components(ratio: float, phase: int, state: EngineState) -> str:
    """§2.2.3 primitive 1 + §2.2.2 controlled exception — gear-rack advance.

    Returns: colorized rail markup string
      - engaged cells (i < pos):  `[$semantic]┅[/]`
      - gear cell (i == pos):     `[$accent-light]{frame}[/]`
      - un-engaged cells (i > pos):`[$text-faint]┄[/]`

    Idle freezes gear frame at base `❋` regardless of `phase`. Active cycles
    through `_GEAR_FRAMES` at 1Hz via `(phase % 3)`. Gear position is pure
    `round(ratio * (_RAIL_WIDTH - 1))` — no ±1 bob.
    """
    ratio = max(0.0, min(1.0, ratio))
    pos = round(ratio * (_RAIL_WIDTH - 1))
    pos = max(0, min(_RAIL_WIDTH - 1, pos))

    gear_frame = _GEAR_FRAMES[0] if state == "idle" else _GEAR_FRAMES[phase % 3]
    semantic = _semantic_token(ratio)

    parts: list[str] = []
    for i in range(_RAIL_WIDTH):
        if i < pos:
            parts.append(f"{semantic}{_CHAIN}[/]")
        elif i == pos:
            parts.append(f"[$accent-light]{gear_frame}[/]")
        else:
            parts.append(f"[$text-faint]{_TOOTH}[/]")
    return "".join(parts)


def _build_ctx_line_components(
    app,
    ctx_tokens: int,
    ctx_window: int,
    engine_state: EngineState,
    phase: int,
) -> tuple[str, str]:
    """§9.3 StatusBar prefix + rail builder. Pure helper, no widget coupling.

    Returns: (prefix_str, rail_str)
      - prefix_str: ' [$model-tier]<model>[/] [$branch-tier]⎇[/] [$branch-name-tier]<branch>[/] [$stats-tier]<N>t·<M>tl[/] [$text-muted]ctx:[/] '
      - rail_str:  '[$success|warning|error]┅...[$accent-light]❋[/][$text-faint]┄...[/]'

    Active boost: when engine_state != idle, model/branch/turns·tools tiers
    promote one tier brighter (§9.3 active-boost table).
    """
    model = getattr(getattr(app, "llm", None), "model", "?")
    git_branch = getattr(app, "_git_branch", "") or ""
    turns = getattr(app, "user_turn_count", 0)
    tools = getattr(app, "tool_call_count", 0)
    is_active = engine_state != "idle"

    model_tier = _active_tier("model", is_active)
    branch_glyph_tier = _active_tier("branch_glyph", is_active)
    branch_name_tier = _active_tier("branch_name", is_active)
    stats_tier = _active_tier("stats", is_active)

    pid, _ = parse_model_id(model)
    has_creds = credentials.get(pid) is not None
    status_suffix = " [$accent]✓[/]" if has_creds else " [$text-muted]✗[/]"
    prefix_parts: list[str] = [f"{model_tier}{model}[/]{status_suffix}"]
    if git_branch:
        prefix_parts.append(
            f"{branch_glyph_tier}⎇[/] {branch_name_tier}{git_branch}[/]"
        )
    prefix_parts.append(f"{stats_tier}{turns}t·{tools}tl[/]")
    prefix = " · ".join(prefix_parts) + " [$text-muted]ctx:[/]"

    ctx_window = ctx_window or 1
    ratio = ctx_tokens / ctx_window if ctx_window > 0 else 0.0
    rail_str = _ctx_rail_components(ratio, phase, engine_state)

    return prefix, rail_str


def _render_engine_badge(state: EngineState) -> str:
    """§4.2.1 engine badge — 6-state indicator (post-revamp, no tool name).

    idle       → [$text-muted]● idle[/]
    thinking   → [$warning]◌ thinking[/]
    streaming  → [$accent]▸ streaming[/]
    executing  → [$accent]⊙ executing[/]
    compacting → [$secondary]◌ compacting[/]
    error      → [$error]⊗ error[/]
    """
    if state == "idle":
        return "[$text-muted]● idle[/]"
    if state == "thinking":
        return "[$warning]◌ thinking[/]"
    if state == "streaming":
        return "[$accent]▸ streaming[/]"
    if state == "executing":
        return "[$accent]⊙ executing[/]"
    if state == "compacting":
        return "[$secondary]◌ compacting[/]"
    if state == "error":
        return "[$error]⊗ error[/]"
    # Should be unreachable — EngineState is a closed Literal. Fall through
    # to muted idle so a future state addition doesn't crash render().
    return "[$text-muted]● idle[/]"


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
    # Kept as `shuttle_phase` for backward compatibility with the existing
    # 1Hz `set_interval(..., name="shuttle-tick")` driver in app.py — the
    # counter now cycles 0/1/2 (3 frames) instead of 0↔1 (2 frames).
    shuttle_phase: reactive[int] = reactive(0)

    def render(self) -> str:
        app = self.app
        ctx_window = self.ctx_window or 1
        ratio = self.ctx_tokens / ctx_window if ctx_window > 0 else 0.0

        prefix, rail_str = _build_ctx_line_components(
            app, self.ctx_tokens, self.ctx_window, self.engine_state, self.shuttle_phase,
        )

        # Danger coloring: when the rail is in $warning / $error, the
        # <used>/<window> (N%) text inherits the same semantic color so the
        # number doesn't read gray against a red rail (§9.3).
        if ratio >= _CTX_DANGER_RATIO:
            tail_tier = "[$error]"
        elif ratio >= _CTX_WARN_RATIO:
            tail_tier = "[$warning]"
        else:
            tail_tier = "[$foreground]"

        ctx_tail = (
            f"{rail_str} {tail_tier}{_format_tokens(self.ctx_tokens)}/"
            f"{_format_tokens(self.ctx_window)} ({ratio * 100:.0f}%)[/]"
        )
        engine_badge = _render_engine_badge(self.engine_state)
        elapsed = f"[$text-muted]{_format_elapsed(self.elapsed_seconds)}[/]"

        return f"{prefix}{ctx_tail} {_SEP} {engine_badge} {elapsed}"