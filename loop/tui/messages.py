"""TUI message types for the loop agent.

These messages bridge the boundary between the agent worker thread
(using ``asyncio.to_thread``) and the main Textual event loop.
Callbacks fire on the worker thread; ``on_X`` handlers fire on the main
loop.  ``post_message`` is safe to call from any thread.
"""

from textual.message import Message


class AssistantTurnStart(Message):
    """Emitted when the assistant begins a new turn."""

    def __init__(self) -> None:
        super().__init__()


class TextDelta(Message):
    """A streaming text fragment from the assistant."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ToolUseStarted(Message):
    """A tool call has been dispatched."""

    def __init__(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        super().__init__()


class ToolUseCompleted(Message):
    """A tool call has finished."""

    def __init__(self, tool_use_id: str, output: str, is_error: bool) -> None:
        self.tool_use_id = tool_use_id
        self.output = output
        self.is_error = is_error
        super().__init__()


class CompactOccurred(Message):
    """Context compression reduced the message list."""

    def __init__(self, msgs_before: int, msgs_after: int) -> None:
        self.msgs_before = msgs_before
        self.msgs_after = msgs_after
        super().__init__()


class AssistantTurnEnd(Message):
    """The assistant has completed its turn."""

    def __init__(self, tool_calls: int, total_messages: int) -> None:
        self.tool_calls = tool_calls
        self.total_messages = total_messages
        super().__init__()
