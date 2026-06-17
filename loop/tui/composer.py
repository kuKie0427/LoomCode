from textual.message import Message
from textual.widgets import TextArea


class Composer(TextArea):
    """Single-line input with /command support. Submits on Enter."""

    class Submitted(Message):
        """Posted when the user presses Enter."""

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
