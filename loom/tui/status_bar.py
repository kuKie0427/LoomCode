from __future__ import annotations

import re
from typing import Literal

from textual.reactive import reactive
from textual.widgets import Static

from loom.agent.credential import credentials
from loom.agent.providers.registry import parse_model_id

EngineState = Literal["idle", "thinking", "streaming", "executing", "compacting", "error"]

# §9.3 StatusBar (post-revamp) — gear-rack ctx rail primitives.
_RAIL_WIDTH = 14
_GEAR_FRAMES = ("❋", "✻", "✜")
_CHAIN = "┅"
_TOOTH = "┄"

_CTX_WARN_RATIO = 0.60
_CTX_DANGER_RATIO = 0.85

_SEP = "·"

# Rich markup tag pattern — used to strip color/style tags for width measurement.
# Matches [token], [/], [$var], [token attr=val] etc.
_STRIP_MARKUP_RE = re.compile(r"\[/?[^\]]*\]")


def _strip_markup(text: str) -> str:
    return _STRIP_MARKUP_RE.sub("", text)


def _visible_width(text: str) -> int:
    """Visible character width after stripping Rich markup."""
    return len(_strip_markup(text))


def _format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _ctx_color_class(ratio: float) -> str:
    if ratio >= _CTX_DANGER_RATIO:
        return "ctx-danger"
    if ratio >= _CTX_WARN_RATIO:
        return "ctx-warn"
    return "ctx-ok"


def _semantic_token(ratio: float) -> str:
    return {
        "ctx-ok": "[$success]",
        "ctx-warn": "[$warning]",
        "ctx-danger": "[$error]",
    }[_ctx_color_class(ratio)]


def _active_tier(field: str, is_active: bool) -> str:
    if is_active:
        return {
            "model": "[$secondary]",
            "branch_glyph": "[$text-muted]",
            "branch_name": "[$text-muted]",
            "stats": "[$foreground]",
        }[field]
    return {
        "model": "[$text-muted]",
        "branch_glyph": "[$text-faint]",
        "branch_name": "[$text-faint]",
        "stats": "[$text-muted]",
    }[field]


def _abbrev_model(model: str) -> str:
    """Shorten model name for narrow terminals.

    ``deepseek-v4-flash`` → ``d-v4-flash``
    ``anthropic/claude-sonnet-4-5`` → ``c-sonnet-4-5``
    ``openai/gpt-4o`` → ``o-gpt-4o``
    """
    if "/" in model:
        provider, _, name = model.partition("/")
        short_provider = provider[0] if provider else "?"
        return f"{short_provider}-{name}"
    # For flat names like deepseek-v4-flash, strip the leading org
    parts = model.split("-")
    if len(parts) >= 3:
        return "-".join(parts[1:])  # drop first segment
    return model


def _ctx_rail_components(ratio: float, phase: int, state: EngineState) -> str:
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


def _render_engine_badge(state: EngineState) -> str:
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
    return "[$text-muted]● idle[/]"


# ── Adaptive compact levels ─────────────────────────────────────────────

# Each level is a (min_visible_width, label) pair.  render() tries levels
# from most verbose (0) to most compact (6), stopping at the first whose
# visible width fits within the available space.
_COMPACT_LEVELS = [
    (88, "full"),      # 0: model · branch · stats · ctx gear + numbers · badge
    (78, "nobranch"),  # 1: drop git branch
    (68, "cmodel"),    # 2: shorten model name
    (55, "cstats"),    # 3: compact turns/tools (drop tools count)
    (42, "norail"),    # 4: drop gear-rack, keep ctx numbers
    (32, "pct"),       # 5: ctx percentage only
    (0,  "badge"),     # 6: engine badge only
]


def _render_line(
    app,
    ctx_tokens: int,
    ctx_window: int,
    engine_state: EngineState,
    phase: int,
    git_branch: str,
    available: int,
) -> str:
    """Build status bar content that fits within *available* visible cells.

    Tries compact levels from most verbose to most compact until the
    visible width (after stripping Rich markup) fits.
    """
    model = getattr(getattr(app, "llm", None), "model", "?")
    pid, _ = parse_model_id(model)
    has_creds = credentials.get(pid) is not None
    turns = getattr(app, "user_turn_count", 0)
    tools = getattr(app, "tool_call_count", 0)
    is_active = engine_state != "idle"
    ctx_window = ctx_window or 1
    ratio = ctx_tokens / ctx_window if ctx_window > 0 else 0.0

    model_tier = _active_tier("model", is_active)
    branch_glyph_tier = _active_tier("branch_glyph", is_active)
    branch_name_tier = _active_tier("branch_name", is_active)
    stats_tier = _active_tier("stats", is_active)
    cred_suffix = " [$accent]✓[/]" if has_creds else " [$text-muted]✗[/]"

    ctx_tail_tier = (
        "[$error]" if ratio >= _CTX_DANGER_RATIO
        else "[$warning]" if ratio >= _CTX_WARN_RATIO
        else "[$foreground]"
    )

    rail_str = _ctx_rail_components(ratio, phase, engine_state)

    for _min_width, _level in _COMPACT_LEVELS:
        line = _build_line(
            model=model,
            cred_suffix=cred_suffix,
            model_tier=model_tier,
            git_branch=git_branch,
            branch_glyph_tier=branch_glyph_tier,
            branch_name_tier=branch_name_tier,
            stats_tier=stats_tier,
            turns=turns,
            tools=tools,
            ctx_tail_tier=ctx_tail_tier,
            rail_str=rail_str,
            ratio=ratio,
            ctx_tokens=ctx_tokens,
            ctx_window=ctx_window,
            engine_state=engine_state,
            level=_level,
        )
        if _visible_width(line) <= available:
            return line

    # Fallback: badge only (always fits)
    return _render_engine_badge(engine_state)


def _build_line(
    model: str,
    cred_suffix: str,
    model_tier: str,
    git_branch: str,
    branch_glyph_tier: str,
    branch_name_tier: str,
    stats_tier: str,
    turns: int,
    tools: int,
    ctx_tail_tier: str,
    rail_str: str,
    ratio: float,
    ctx_tokens: int,
    ctx_window: int,
    engine_state: EngineState,
    level: str,
) -> str:
    """Assemble a status bar line for the given compact *level*."""
    parts: list[str] = []

    # Model
    if level == "cmodel":
        parts.append(f"{model_tier}{_abbrev_model(model)}[/]{cred_suffix}")
    else:
        parts.append(f"{model_tier}{model}[/]{cred_suffix}")

    # Git branch
    if level in ("full",) and git_branch:
        parts.append(f"{branch_glyph_tier}⎇[/] {branch_name_tier}{git_branch}[/]")

    # Turns/tools
    if level in ("pct", "badge"):
        pass  # stats dropped at compact levels
    elif level in ("norail",):
        parts.append(f"{stats_tier}{turns}t[/]")
    else:
        parts.append(f"{stats_tier}{turns}t·{tools}tl[/]")

    # Context
    if level == "badge":
        pass
    elif level == "pct":
        parts.append(f"{ctx_tail_tier}{ratio * 100:.0f}%[/]")
    elif level == "norail":
        parts.append(
            f"[$text-muted]ctx:[/] {ctx_tail_tier}"
            f"{_format_tokens(ctx_tokens)}/{_format_tokens(ctx_window)} "
            f"({ratio * 100:.0f}%)[/]"
        )
    else:
        parts.append(
            f"[$text-muted]ctx:[/]"
            f" {rail_str}"
            f" {ctx_tail_tier}{_format_tokens(ctx_tokens)}"
            f"/{_format_tokens(ctx_window)} ({ratio * 100:.0f}%)[/]"
        )

    # Engine badge — always present
    badge = _render_engine_badge(engine_state)
    parts.append(badge)

    return f" {_SEP} ".join(parts)


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
    git_branch: reactive[str] = reactive("")
    engine_state: reactive[EngineState] = reactive("idle")
    shuttle_phase: reactive[int] = reactive(0)

    def render(self) -> str:
        app = self.app
        # Content area: widget width minus CSS padding (0 1 = 2 cols).
        available = max(10, self.size.width - 2)
        return _render_line(
            app,
            self.ctx_tokens,
            self.ctx_window,
            self.engine_state,
            self.shuttle_phase,
            self.git_branch,
            available,
        )
