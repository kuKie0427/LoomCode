from loom.tui.app import AgentTUIApp
from loom.tui.chat_log import (
    ChatLog,
    CollapsibleToolOutput,
    StreamingOverlay,
    ThinkingDisplay,
    ToolCallMarker,
)
from loom.tui.command_palette import CommandPaletteModal
from loom.tui.model_picker import ModelPicker
from loom.tui.screens import PermissionScreen
from loom.tui.status_bar import StatusBar
from loom.tui.welcome import WelcomeModal


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


# ── StatusBar: 3 engine states ─────────────────────────────────────────────
# engine_state is set directly on the StatusBar widget (not the App-level
# reactive) so the _tick_shuttle timer (which checks app.engine_state)
# stays idle and does not advance shuttle_phase — keeping snapshots
# deterministic.


def test_status_bar_idle(snap_compare):
    """StatusBar idle state (engine_state=idle)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        status_bar = pilot.app.query_one(StatusBar)
        status_bar.engine_state = "idle"
        status_bar.shuttle_phase = 0
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_status_bar_streaming(snap_compare):
    """StatusBar streaming state (engine_state=streaming)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        status_bar = pilot.app.query_one(StatusBar)
        status_bar.engine_state = "streaming"
        status_bar.shuttle_phase = 0
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_status_bar_error(snap_compare):
    """StatusBar error state (engine_state=error)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        status_bar = pilot.app.query_one(StatusBar)
        status_bar.engine_state = "error"
        status_bar.shuttle_phase = 0
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


# ── ChatLog: streaming + tool-call states ──────────────────────────────────


def test_chatlog_streaming_with_thinking(snap_compare):
    """ChatLog with StreamingOverlay + ThinkingDisplay expanded."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        streaming = StreamingOverlay("Generating response...")
        thinking = ThinkingDisplay("Let me analyze this step by step...")
        thinking.display = True
        await chat_log.mount(streaming)
        await chat_log.mount(thinking)
        await pilot.pause(0.2)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_chatlog_tool_call_collapsed(snap_compare):
    """ChatLog with ToolCallMarker in collapsed state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        marker = ToolCallMarker(
            "bash", "rm -rf /tmp/foo", tool_input={"command": "rm -rf /tmp/foo"}
        )
        marker.set_complete("removed /tmp/foo", is_error=False)
        output = CollapsibleToolOutput("removed /tmp/foo")
        output.display = False
        marker.set_output_widget(output)
        await chat_log.mount(marker)
        await chat_log.mount(output)
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_chatlog_tool_call_expanded(snap_compare):
    """ChatLog with ToolCallMarker in expanded state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        marker = ToolCallMarker(
            "bash", "ls /tmp", tool_input={"command": "ls /tmp"}
        )
        marker.set_complete("file1.txt\nfile2.log", is_error=False)
        output = CollapsibleToolOutput("file1.txt\nfile2.log")
        output.display = True
        output.set_class(True, "expanded")
        marker.set_output_widget(output)
        await chat_log.mount(marker)
        await chat_log.mount(output)
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


# ── Modals: welcome, command palette, model picker ────────────────────────


def test_welcome_banner(snap_compare):
    """WelcomeModal (welcome screen) shown on startup.

    Under pytest, AgentTUIApp sets _skip_welcome=True, so WelcomeModal is
    NOT auto-pushed in on_mount. We push it manually here to capture its
    visual state. (Plan referred to WelcomeBanner; the actual live welcome
    flow uses WelcomeModal from loom/tui/welcome.py — WelcomeBanner in
    chat_log.py is dead code, never mounted.)
    """
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.push_screen(WelcomeModal(model=pilot.app.llm.model))
        await pilot.pause(0.2)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_command_palette_open(snap_compare):
    """CommandPaletteModal open state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.push_screen(CommandPaletteModal())
        await pilot.pause(0.2)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_model_picker_open(snap_compare):
    """ModelPicker open state (mock only anthropic connected to avoid timeout)."""
    from unittest.mock import MagicMock, patch

    app = AgentTUIApp()
    fake_cred = MagicMock()

    def _fake_get(provider_id: str):
        return fake_cred if provider_id == "anthropic" else None

    async def run_before(pilot):
        # Mock both credentials.get (legacy path) and credentials.all
        # (the optimized _build_rows path) so only anthropic appears.
        with patch("loom.agent.credential.credentials.get", side_effect=_fake_get), \
             patch("loom.agent.credential.credentials.all", return_value={"anthropic": fake_cred}):
            pilot.app.push_screen(ModelPicker())
            await pilot.pause(0.4)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
