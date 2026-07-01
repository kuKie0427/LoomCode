"""TUI message types for the loom agent.

These messages bridge the boundary between the agent worker thread
(using ``asyncio.to_thread``) and the main Textual event loop.
Callbacks fire on the worker thread; ``on_X`` handlers fire on the main
loop.  ``post_message`` is safe to call from any thread.
"""

from typing import Literal

from textual.message import Message


class AssistantTurnStart(Message):
    """Emitted when the assistant begins a new turn."""

    def __init__(self) -> None:
        super().__init__()


class TextDelta(Message):
    """A streaming text fragment from the assistant."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ThinkingDelta(Message):
    """A streaming thinking fragment from the assistant (extended-thinking models)."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolUseStarted(Message):
    """A tool call has been dispatched."""

    def __init__(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id


class ToolUseCompleted(Message):
    """A tool call has finished."""

    def __init__(self, tool_use_id: str, output: str, is_error: bool) -> None:
        super().__init__()
        self.tool_use_id = tool_use_id
        self.output = output
        self.is_error = is_error


class CompactOccurred(Message):
    """Context compression reduced the message list."""

    def __init__(self, msgs_before: int, msgs_after: int) -> None:
        super().__init__()
        self.msgs_before = msgs_before
        self.msgs_after = msgs_after


class AssistantTurnEnd(Message):
    """The assistant has completed its turn."""

    def __init__(self, tool_calls: int, total_messages: int, duration: float) -> None:
        super().__init__()
        self.tool_calls = tool_calls
        self.total_messages = total_messages
        self.duration = duration


class TodoUpdate(Message):
    """Posted when the agent's todo list changes (todo_write tool run)."""

    def __init__(self, todos: list) -> None:
        super().__init__()
        self.todos = todos


class SubagentStart(Message):
    """Posted when a subagent begins (task / task_* / review tool called).

    ``agent_name`` is the weaving-themed display name (织针 / 飞梭 / 经线 /
    织补 / 验布) used by the ChatLog SubagentMarker and the Header overlay.
    """

    def __init__(self, subagent_id: str, description: str, agent_name: str = "织针") -> None:
        super().__init__()
        self.subagent_id = subagent_id
        self.description = description
        self.agent_name = agent_name


class SubagentEnd(Message):
    """Posted when a subagent completes (or errors)."""

    def __init__(
        self,
        subagent_id: str,
        elapsed: float,
        state: Literal["done", "error"],
    ) -> None:
        super().__init__()
        self.subagent_id = subagent_id
        self.elapsed = elapsed
        self.state = state


class ShowNotification(Message):
    """Posted to display an inline notification in the ChatLog.

    Replaces Textual's built-in ``notify()`` toasts, which violate the
    TUI design language (no floating banners — see §2 rule 6). The app
    handles this by appending a SystemNote to the ChatLog.
    """

    def __init__(self, text: str, severity: str = "info") -> None:
        super().__init__()
        self.text = text
        self.severity = severity
