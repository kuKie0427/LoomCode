"""Tab-triggered / command completion popup.

Models opencode autocomplete.tsx /-mode:
  - Input starting with / and no space → shows matches
  - Up/Down keyboard navigation
  - Tab/Enter selects highlighted → replaces Composer with "/<name> "
  - Esc / focus lost / space typed → hides
"""

from __future__ import annotations

from difflib import get_close_matches

from textual.reactive import reactive
from textual.widgets import Static

from loom.tui.slash_commands import SlashCommand, all_commands


def filter_commands(query: str, limit: int = 8) -> list[SlashCommand]:
    """Filter all slash commands by prefix match and fuzzy fallback.

    Empty query → first ``limit`` commands from ``all_commands()``.
    Prefix matching (name or alias) sorted first.
    ``difflib.get_close_matches`` fuzzy fallback (cutoff=0.3).
    Prefix items always ahead of fuzzy hits.
    Combined result capped at ``limit``.
    """
    all_cmds = all_commands()
    if not query:
        return all_cmds[:limit]

    lower = query.lower()
    prefix_matches: list[SlashCommand] = []
    rest: list[SlashCommand] = []

    for cmd in all_cmds:
        if cmd.name.startswith(lower) or any(
            a.startswith(lower) for a in cmd.aliases
        ):
            prefix_matches.append(cmd)
        else:
            rest.append(cmd)

    fuzzy_names = get_close_matches(
        lower, [c.name for c in rest], n=limit, cutoff=0.3
    )
    fuzzy_set = {c.name for c in prefix_matches}
    fuzzy_matches = [
        c for c in rest if c.name in fuzzy_names and c.name not in fuzzy_set
    ]

    result = prefix_matches + fuzzy_matches
    return result[:limit]


class CommandCompleter(Static):
    """Dropdown completion popup shown when the user types ``/`` in the Composer."""

    selected: reactive[int] = reactive(0)
    matches: reactive[list[SlashCommand]] = reactive([])

    DEFAULT_CSS = """
    CommandCompleter {
        height: auto;
        max-height: 8;
        background: $panel 97%;
        border-top: solid $border;
        padding: 0 1;
        display: none;
    }
    CommandCompleter.visible {
        display: block;
    }
    .row {
        height: 1;
    }
    .row.selected {
        background: $accent 30%;
        color: $text;
        text-style: bold;
    }
    """

    def show_for(self, query: str) -> None:
        """Show completions for the given query (full Composer text)."""
        stripped = query.lstrip("/")
        self.matches = filter_commands(stripped)
        self.selected = 0
        self.add_class("visible")

    def hide(self) -> None:
        self.remove_class("visible")
        self.matches = []
        self.selected = 0

    def move(self, direction: int) -> None:
        if not self.matches:
            return
        total = len(self.matches)
        self.selected = (self.selected + direction) % total

    def current(self) -> SlashCommand | None:
        if self.matches and 0 <= self.selected < len(self.matches):
            return self.matches[self.selected]
        return None

    def watch_matches(self, matches: list[SlashCommand]) -> None:
        self._render_rows()

    def watch_selected(self, selected: int) -> None:
        self._render_rows()

    def _render_rows(self) -> None:
        if not self.matches:
            self.update("[dim]No match[/]")
            return
        lines: list[str] = []
        for i, cmd in enumerate(self.matches):
            alias_str = (
                f" [dim]({', '.join(cmd.aliases)})[/dim]" if cmd.aliases else ""
            )
            if i == self.selected:
                lines.append(f"▸ /{cmd.name}{alias_str} — {cmd.description}")
            else:
                lines.append(
                    f"  /{cmd.name}{alias_str} — [dim]{cmd.description}[/dim]"
                )
        self.update("\n".join(lines))
