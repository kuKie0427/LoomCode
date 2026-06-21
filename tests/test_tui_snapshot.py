from loom.tui.app import AgentTUIApp
from loom.tui.screens import PermissionScreen


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

