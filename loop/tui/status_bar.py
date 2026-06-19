from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from loop.tui.chat_log import ChatLog

_BAR_WIDTH = 10
_BAR_FULL = "█"
_BAR_EMPTY = "░"

_CTX_WARN_RATIO = 0.60
_CTX_DANGER_RATIO = 0.85


def _format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


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

    def render(self) -> str:
        app = self.app
        model = getattr(getattr(app, "llm", None), "model", "?")

        ctx_window = self.ctx_window or 1
        ratio = self.ctx_tokens / ctx_window if ctx_window > 0 else 0.0
        bar = _progress_bar(ratio)
        color_open = {
            "ctx-ok": "[green]",
            "ctx-warn": "[yellow]",
            "ctx-danger": "[red]",
        }[_ctx_color_class(ratio)]
        ctx_str = (
            f"ctx: {color_open}{bar}[/] "
            f"{_format_tokens(self.ctx_tokens)}/{_format_tokens(self.ctx_window)} "
            f"({ratio * 100:.0f}%)"
        )

        hint = ""
        try:
            chat_log = app.query_one(ChatLog)
            if chat_log.max_scroll_y > 0:
                hint = " | scroll with mouse wheel"
        except Exception:
            pass

        return (
            f" loop | model: {model} | turns: {self.turns} | "
            f"tools: {self.tools} | {ctx_str}{hint} "
        )
