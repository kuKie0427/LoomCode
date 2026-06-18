from textual.widgets import Static

from loop.tui.chat_log import ChatLog


class StatusBar(Static):
    """Footer bar showing model, turn count, tool call count, and scroll hint."""

    def render(self) -> str:
        app = self.app
        model = app.llm.model
        turns = len(app.history)
        tools = app.tool_call_count

        hint = ""
        try:
            chat_log = app.query_one(ChatLog)
            if chat_log.max_scroll_y > 0:
                hint = " | scroll with mouse wheel"
        except Exception:
            pass

        return f" loop | model: {model} | turns: {turns} | tools: {tools}{hint} "
