"""Eval case for the /-command completion popup — f-slash-completion-popup.

Locks ``filter_commands`` from ``loom/tui/completer.py``:

  * Prefix ``"mo"`` returns ``model`` in the first 3 results.
  * Prefix ``"q"`` returns ``quit`` as the first result (alias match).
  * Empty query returns all 7 commands.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class SlashCompletionPopupCase(EvalCase):
    name = "slash_completion_popup"
    description = (
        "filter_commands: prefix, alias, empty-query all work"
    )

    def run(self) -> EvalResult:
        from loom.tui.completer import filter_commands

        # 1) Prefix "mo" → "model" should be in the first 3 results
        r1 = filter_commands("mo")
        names1 = [c.name for c in r1]
        if "model" not in names1[:3]:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Expected 'model' in first 3 for 'mo',"
                    f" got {names1}"
                ),
            )

        # 2) Prefix "q" → "quit" should be first (alias match)
        r2 = filter_commands("q")
        names2 = [c.name for c in r2]
        if not names2 or names2[0] != "quit":
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Expected 'quit' first for 'q',"
                    f" got {names2}"
                ),
            )

        # 3) Empty query → all 7 commands
        r3 = filter_commands("")
        if len(r3) != 7:
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"Expected 7 results for '',"
                    f" got {len(r3)}"
                ),
            )

        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "filter_commands: prefix, alias, and empty query"
                " all produce expected results"
            ),
        )
