from textual.message import Message
from textual.widgets import Input


class Composer(Input):
    """Single-line input with /command support.  Submits on Enter."""

    class Submitted(Message):
        """Posted when the user presses Enter."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.placeholder = "Type a prompt, / for commands"

    async def action_submit(self) -> None:
        value = self.value
        self.value = ""
        self.post_message(self.Submitted(value))
