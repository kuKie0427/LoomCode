"""Eval case for the command palette modal — f-tui-cmd-completion-p2.

Tests ``filter_commands`` and ``all_commands`` at the module level
(no modal rendering, no LLM calls):

  * ``filter_commands("qu", limit=20)`` returns exactly 1 match: ``quit``
  * ``filter_commands("help", limit=20)`` returns ``help`` in the first 2 results
  * ``filter_commands("", limit=20)`` returns 7 results (all commands)
  * ``all_commands()`` returns 7 items, each with non-empty ``description``
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class CommandPaletteModalCase(EvalCase):
    name = "command_palette_modal"
    description = (
        "filter_commands and all_commands: prefix, exact, "
        "empty-query, and description completeness"
    )

    def run(self) -> EvalResult:
        from loom.tui.completer import filter_commands
        from loom.tui.slash_commands import all_commands

        # 1) Prefix "qu" → exactly 1 result: "quit"
        r1 = filter_commands("qu", limit=20)
        names1 = [c.name for c in r1]
        if len(names1) != 1 or names1[0] != "quit":
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Expected exactly 1 match ('quit') for 'qu',"
                    f" got {names1}"
                ),
            )

        # 2) Prefix "help" → "help" in first 2 results
        r2 = filter_commands("help", limit=20)
        names2 = [c.name for c in r2]
        if "help" not in names2[:2]:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Expected 'help' in first 2 for 'help',"
                    f" got {names2}"
                ),
            )

        # 3) Empty query → 7 results (all commands)
        r3 = filter_commands("", limit=20)
        if len(r3) != 7:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Expected 7 results for '',"
                    f" got {len(r3)}"
                ),
            )

        # 4) all_commands() → 7 items, each with non-empty description
        r4 = all_commands()
        if len(r4) != 7:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Expected 7 commands from all_commands(),"
                    f" got {len(r4)}"
                ),
            )
        for cmd in r4:
            if not cmd.description:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=(
                        f"Command '{cmd.name}' has empty description"
                    ),
                )

        return EvalResult(
            name=self.name,
            passed=True,
            detail=(
                "filter_commands('qu' limit=20) → [quit], "
                "filter_commands('help' limit=20) → help in first 2, "
                "filter_commands('' limit=20) → 7 results, "
                "all_commands() → 7 items all with non-empty descriptions"
            ),
        )
