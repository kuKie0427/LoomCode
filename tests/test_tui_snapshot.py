from textual.app import App, ComposeResult

from loop.tui.app import AgentTUIApp
from loop.tui.screens import PermissionScreen
from loop.tui.widgets import ToolCallCard


def test_empty_layout(snap_compare):
    """TUI empty state renders correctly."""
    app = AgentTUIApp()
    assert snap_compare(app, terminal_size=(120, 40))

def test_permission_modal_open(snap_compare):
    """PermissionScreen renders correctly when pushed via run_before."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.app.push_screen(PermissionScreen("bash", {"command": "rm -rf /tmp/foo"}, "Risky command"))
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))

def test_tool_card_completed(snap_compare):
    """ToolCallCard completed state renders green border."""
    card = ToolCallCard("bash", {"command": "ls"}, "tool-1")
    card.complete("file1.txt\nfile2.txt", is_error=False)
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield card
    app = TestApp()
    assert snap_compare(app, terminal_size=(80, 10))
