from textual import events
from textual.message import Message
from textual.widgets import TextArea


class Composer(TextArea):
    """Single-line input with /command support. Submits on Enter.

    The Composer intentionally does NOT consume wheel events. The default
    TextArea (ScrollView) would try to scroll its own mostly-empty viewport
    on every wheel tick; that fights the user's intent of scrolling the
    chat history. We suppress TextArea's scroll with prevent_default but
    do NOT stop the event, so it bubbles up to the App's wheel handler
    which routes it to the ChatLog. This mirrors how opencode's input
    prompt has no scroll behavior of its own.
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("tab_behavior", "focus")
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)
        self.placeholder = "Type a prompt, / for commands"

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text
            self.text = ""
            self.post_message(self.Submitted(text))
            return
        await super()._on_key(event)

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.prevent_default()
        return super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.prevent_default()
        return super()._on_mouse_scroll_down(event)
