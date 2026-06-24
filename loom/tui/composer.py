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

    class CompletionTab(Message):
        """Tab key pressed while in `/` command mode."""

    class CompletionMove(Message):
        """Up/Down arrow pressed while in `/` command mode."""

        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    class CompletionHide(Message):
        """Conditions changed — hide the completion popup."""

    class CompletionQuery(Message):
        """Text changed while in `/` command mode."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

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
        if self.text.startswith("/") and " " not in self.text:
            if event.key == "tab":
                event.prevent_default()
                event.stop()
                self.post_message(self.CompletionTab())
                return
            if event.key == "up":
                event.prevent_default()
                event.stop()
                self.post_message(self.CompletionMove(-1))
                return
            if event.key == "down":
                event.prevent_default()
                event.stop()
                self.post_message(self.CompletionMove(1))
                return
            if event.key == "escape":
                event.prevent_default()
                event.stop()
                self.post_message(self.CompletionHide())
                return
        await super()._on_key(event)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Text changed — show/hide the completion popup.

        Uses ``TextArea.Changed`` (instead of reading ``self.text``
        inline in ``_on_key``) because ``TextArea._on_key`` does not
        update ``self.text`` synchronously for non-printable keys such
        as Backspace.  ``Changed`` always fires *after* the edit has
        been applied, so ``self.text`` is guaranteed fresh.
        """
        if self.text.startswith("/") and " " not in self.text:
            self.post_message(self.CompletionQuery(self.text))
        else:
            self.post_message(self.CompletionHide())

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.prevent_default()
        return super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.prevent_default()
        return super()._on_mouse_scroll_down(event)
