from __future__ import annotations

from typing import Literal

from textual.reactive import reactive
from textual.widgets import Static

EngineState = Literal["idle", "thinking", "streaming", "executing", "compacting", "error"]

_BAR_WIDTH = 10
_BAR_FULL = "█"
_BAR_EMPTY = "░"

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


def _progress_bar(ratio: float, width: int = _BAR_WIDTH) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def _ctx_color_class(ratio: float) -> str:
    if ratio >= _CTX_DANGER_RATIO:
        return "ctx-danger"
    if ratio >= _CTX_WARN_RATIO:
        return "ctx-warn"
    return "ctx-ok"


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

    def render(self) -> str:
        app = self.app
        model = getattr(getattr(app, "llm", None), "model", "?")

        ctx_window = self.ctx_window or 1
        ratio = self.ctx_tokens / ctx_window if ctx_window > 0 else 0.0
        bar = _progress_bar(ratio)
        color_open = {
            "ctx-ok": "[$success]",
            "ctx-warn": "[$warning]",
            "ctx-danger": "[$error]",
        }[_ctx_color_class(ratio)]
        ctx_str = (
            f"[$text-muted]ctx:[/] {color_open}{bar}[/] "
            f"{_format_tokens(self.ctx_tokens)}/{_format_tokens(self.ctx_window)} "
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
