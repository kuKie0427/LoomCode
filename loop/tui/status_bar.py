from textual.widgets import Static


class StatusBar(Static):
    """Footer bar showing model, turn count, and tool call count."""

    def render(self) -> str:
        app = self.app
        model = app.llm.model
        turns = len(app.history)
        tools = app.tool_call_count
        return f" loop | model: {model} | turns: {turns} | tools: {tools} "
