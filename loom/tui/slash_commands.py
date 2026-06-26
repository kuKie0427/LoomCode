"""Slash command registry for the loom TUI.

Provides a SlashCommand dataclass, a module-level SLASH_COMMANDS registry,
query helpers (find_command, all_commands), and handler functions for each
command. Used by AgentTUIApp.run_slash_command via table lookup instead of
if/elif chains.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loom.tui.app import AgentTUIApp

from loom.agent.loop import WORKDIR
from loom.tui.auth_input import AuthInputModal
from loom.tui.chat_log import ChatLog
from loom.tui.connect_provider import ConnectProviderModal
from loom.tui.model_picker import ModelPicker

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class SlashCommand:
    name: str
    description: str
    handler: Callable[[AgentTUIApp, str], Awaitable[None]]
    aliases: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_help(app: AgentTUIApp, args: str) -> None:
    chat_log = app.query_one(ChatLog)
    chat_log.append_system_note(
        "**Commands:** /help, /clear, /init, /model <name>, /connect, "
        "/thinking, /resume [id], /sessions, /new, /status, /quit"
    )


async def handle_clear(app: AgentTUIApp, args: str) -> None:
    app.history.clear()
    app.user_turn_count = 0
    app.tool_call_count = 0
    app.ctx_tokens = 0
    chat_log = app.query_one(ChatLog)
    await chat_log.clear_content()


async def handle_model(app: AgentTUIApp, args: str) -> None:
    chat_log = app.query_one(ChatLog)
    if args.strip():
        app.llm.change_model(args.strip())
        app._sync_status_bar()
        chat_log.append_system_note(f"Model changed to **{app.llm.model}**")
        from loom.agent.model_state import ModelState

        provider, _, model_id = args.strip().partition("/")
        if provider and model_id:
            ms = ModelState(WORKDIR)
            ms.add_recent(provider, model_id)
            ms.set_default(provider, model_id)
    else:
        from loom.agent.model_state import ModelState

        ms = ModelState(WORKDIR)
        app.push_screen(ModelPicker(recent=ms.recent(limit=10)), app._on_model_picked)


async def handle_sessions(app: AgentTUIApp, args: str) -> None:
    """Open the session picker modal to switch between saved sessions."""
    from loom.tui.session_picker import SessionPicker

    app.push_screen(
        SessionPicker(current_session_id=app._session_id),
        app._on_session_picked,
    )


async def handle_new(app: AgentTUIApp, args: str) -> None:
    """Start a new session, saving the current one first."""
    app.new_session()


async def handle_resume(app: AgentTUIApp, args: str) -> None:
    """Resume a session.

    With no args: load the most recent checkpoint (legacy behavior).
    With a session_id arg: switch to that specific session.
    """
    chat_log = app.query_one(ChatLog)
    target = args.strip()
    if target:
        # Resume a specific session by id.
        app.switch_session(target)
        return
    # Legacy: resume from the single checkpoint.json file.
    import loom.agent.checkpoint as checkpoint

    if checkpoint.exists(WORKDIR):
        ckpt = checkpoint.load(WORKDIR)
        if ckpt is not None:
            app.history = ckpt.get("messages", [])
            app.user_turn_count = sum(
                1 for m in app.history if m.get("role") == "user"
            )
            app.tool_call_count = ckpt.get("tool_call_count", 0)
            app._refresh_ctx_tokens()
            chat_log.append_system_note(
                f"Resumed from checkpoint ({ckpt.get('saved_at', '?')}, "
                f"{len(app.history)} messages)"
            )
        else:
            chat_log.append_system_note("Checkpoint file corrupted or empty.")
    else:
        chat_log.append_system_note("No checkpoint found.")


async def handle_connect(app: AgentTUIApp, args: str) -> None:
    if args.strip():
        provider_id = args.strip()
        app.push_screen(AuthInputModal(provider_id), app._on_connect_auth_done)
    else:
        app.push_screen(ConnectProviderModal(), app._on_connect_done)


async def handle_status(app: AgentTUIApp, args: str) -> None:
    from loom.agent.credential import credentials
    from loom.agent.providers import PROVIDERS

    chat_log = app.query_one(ChatLog)
    all_creds = credentials.all()
    status = (
        f"**Session Status**\n"
        f"- Model: `{app.llm.model}`\n"
        f"- Messages: {len(app.history)}\n"
        f"- Tool calls: {app.tool_call_count}\n"
        f"\n"
        f"**Providers:**\n"
    )
    for pid in sorted(PROVIDERS):
        try:
            inst = PROVIDERS[pid](api_key="", base_url=None)
            display = inst.display_name or pid
        except Exception:
            display = pid
        if pid in all_creds:
            status += f"  ✓ **{display}** ({pid}) — key saved\n"
        else:
            status += f"    {display} ({pid}) — no key\n"
    chat_log.append_system_note(status)


async def handle_quit(app: AgentTUIApp, args: str) -> None:
    await app.action_quit()


async def handle_thinking(app: AgentTUIApp, args: str) -> None:
    """Set thinking mode and effort for DeepSeek models.

    Usage: /thinking on|off|high|max
      on      — enable thinking with default effort (high)
      off     — disable thinking
      high    — high thinking effort (default)
      max     — maximum thinking effort
      (no arg) — cycle: off → on → off
    """
    arg = args.strip().lower()
    chat_log = app.query_one(ChatLog)

    if arg in ("", "toggle"):
        # Cycle: detect current state
        current = app.llm._provider_options
        if current is None or current.get("thinking", {}).get("type") == "enabled":
            # currently on or default → turn off
            arg = "off"
        else:
            arg = "on"

    configs: dict[str, dict[str, object]] = {
        "on": {"reasoning_effort": "high", "thinking": {"type": "enabled"}},
        "high": {"reasoning_effort": "high", "thinking": {"type": "enabled"}},
        "max": {"reasoning_effort": "max", "thinking": {"type": "enabled"}},
        "off": {"thinking": {"type": "disabled"}},
    }
    if arg not in configs:
        chat_log.append_system_note(
            "**Usage:** /thinking on|off|high|max  (or /thinking to toggle)"
        )
        return

    app.llm.set_provider_options(configs[arg])
    label = {"on": "on (high)", "high": "high", "max": "max", "off": "off"}
    chat_log.append_system_note(f"Thinking mode: **{label[arg]}**")


async def handle_init(app: AgentTUIApp, args: str) -> None:
    from loom.detect import detect_project
    from loom.init_cmd import format_results, init

    chat_log = app.query_one(ChatLog)
    force = args.strip().lower() in ("--force", "-f")
    results = init(WORKDIR, force=force)
    project = detect_project(WORKDIR)
    output = format_results(project, results)
    chat_log.append_system_note(f"**/init** — scaffold harness files\n\n{output}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand(
        name="help",
        description="Show available commands",
        handler=handle_help,
    ),
    SlashCommand(
        name="init",
        description="Scaffold or update harness files (--force to overwrite)",
        handler=handle_init,
    ),
    SlashCommand(
        name="clear",
        description="Clear conversation history",
        handler=handle_clear,
    ),
    SlashCommand(
        name="model",
        description="Switch model or open the model picker",
        handler=handle_model,
    ),
    SlashCommand(
        name="sessions",
        description="Open the session picker to switch or delete sessions",
        handler=handle_sessions,
    ),
    SlashCommand(
        name="new",
        description="Start a new session (saves the current one first)",
        handler=handle_new,
    ),
    SlashCommand(
        name="connect",
        description="Connect a provider or enter an API key",
        handler=handle_connect,
    ),
    SlashCommand(
        name="resume",
        description="Resume a session: /resume <id> or /resume for last checkpoint",
        handler=handle_resume,
    ),
    SlashCommand(
        name="status",
        description="Show session status and provider credentials",
        handler=handle_status,
    ),
    SlashCommand(
        name="thinking",
        description="Set thinking mode/effort: on|off|high|max",
        aliases=("think", "thing"),
        handler=handle_thinking,
    ),
    SlashCommand(
        name="quit",
        description="Quit the application",
        aliases=("q", "exit"),
        handler=handle_quit,
    ),
]


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def find_command(name: str) -> SlashCommand | None:
    lower = name.lower()
    # 1) Exact match on name or alias (fast path)
    for cmd in SLASH_COMMANDS:
        if cmd.name == lower or lower in cmd.aliases:
            return cmd
    # 2) Prefix match on name or alias — closes the gap between what the
    #    completion popup shows (filter_commands uses prefix matching) and
    #    what Enter dispatches.  E.g. /ex → quit (via "exit" alias).
    matches: list[SlashCommand] = []
    for cmd in SLASH_COMMANDS:
        if cmd.name.startswith(lower) or any(
            a.startswith(lower) for a in cmd.aliases
        ):
            matches.append(cmd)
    if len(matches) == 1:
        return matches[0]
    # 3) Ambiguous or no match — the caller will show "Unknown command"
    #    which is correct: the user should use the popup (Tab / ↑↓) to pick.
    return None


def all_commands() -> list[SlashCommand]:
    return list(SLASH_COMMANDS)
