"""Eval case for the SlashCommand registry — f-slash-command-tui.

Locks the contract from ``loom/tui/slash_commands.py``:

  * SLASH_COMMANDS has exactly 7 entries.
  * quit / q / exit aliases all resolve to the same SlashCommand instance.
  * Every SlashCommand has a non-empty description.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class SlashCommandRegistryCase(EvalCase):
    name = "slash_command_registry"
    description = (
        "SLASH_COMMANDS has 7 entries, quit aliases resolve,"
        " all descriptions non-empty"
    )

    def run(self) -> EvalResult:
        from loom.tui.slash_commands import SLASH_COMMANDS, find_command

        if len(SLASH_COMMANDS) != 7:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"len(SLASH_COMMANDS) == {len(SLASH_COMMANDS)},"
                    f" expected 7"
                ),
            )

        q_cmd = find_command("q")
        quit_cmd = find_command("quit")
        exit_cmd = find_command("exit")
        if q_cmd is None or quit_cmd is None or exit_cmd is None:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"find_command aliases: q={q_cmd is not None},"
                    f" quit={quit_cmd is not None},"
                    f" exit={exit_cmd is not None}"
                ),
            )
        if q_cmd is not quit_cmd or quit_cmd is not exit_cmd:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    "q/quit/exit do not all resolve to the same"
                    " SlashCommand instance"
                ),
            )

        for cmd in SLASH_COMMANDS:
            if not cmd.description:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=(
                        f"Command '{cmd.name}' has empty description"
                    ),
                )

        return EvalResult(
            name=self.name, passed=True,
            detail=(
                f"SLASH_COMMANDS: {len(SLASH_COMMANDS)} commands,"
                f" quit aliases consistent, all descriptions non-empty"
            ),
        )
