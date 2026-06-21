"""Minimal diff viewer for the TUI.

Renders a unified diff string (as produced by loom's edit_file /
multi_edit / edit_lines tools) as a colored Static widget.

This is the MINIMAL Phase 3 scope per the roadmap: just a renderer,
no interactive approval UI, no diff generation, no two-pane
side-by-side. The goal is to make the diff output from the
editing tools actually VISIBLE in the TUI when an agent runs
edit_file and clicks the tool result to expand it.

Per-line coloring (loom-ink theme):
  + line (green) — added
  - line (red)   — removed
  @@ hunk header (yellow) — context marker
  default (foreground)   — context / unchanged
"""

from __future__ import annotations

import re

from textual.reactive import reactive
from textual.widgets import Static

_ADD = re.compile(r"^\+")
_REM = re.compile(r"^-")
_HUNK = re.compile(r"^@@")


def colorize_diff(diff: str) -> str:
    """Convert a unified diff to Rich markup for Static rendering."""
    out: list[str] = []
    for line in diff.splitlines():
        if _HUNK.match(line):
            out.append(f"[yellow]{line}[/yellow]")
        elif _ADD.match(line):
            out.append(f"[green]{line}[/green]")
        elif _REM.match(line):
            out.append(f"[red]{line}[/red]")
        else:
            out.append(line)
    return "\n".join(out)


class DiffPane(Static):
    """Renders a unified diff string with line-level color highlighting.

    Content updates via the reactive `diff_text` attribute; the
    widget re-renders automatically when it changes. Empty content
    shows a placeholder.
    """

    diff_text: reactive[str] = reactive("")

    PLACEHOLDER = "(no diff to display)"

    def render(self) -> str:
        text = self.diff_text or ""
        if not text.strip():
            return f"[dim]{self.PLACEHOLDER}[/dim]"
        return colorize_diff(text)

    def watch_diff_text(self, new_value: str) -> None:
        self.update(self.render())
