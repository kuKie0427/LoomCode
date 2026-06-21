"""Eval cases for TUI marker argument summary (f-tool-display-p1).

4 EvalCase classes that lock the marker-summary contract:

1. Summary dispatched per tool name (bash → command, read_file → filename, etc.)
2. Missing field returns empty string (defensive .get() contract)
3. Marker text includes summary in running and done states
4. Error is still distinguishable via ⊗ + tool-error class (not via summary alone)
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: summary dispatched per known tool name ────────────────────────────


class TuiMarkerSummaryDispatchedPerToolName(EvalCase):
    name = "tui-marker-summary-dispatched-per-tool-name"
    description = "_summarize_tool_args returns command for bash, filename for read_file, pattern for glob, task count for todo_write"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import _summarize_tool_args

        bash = _summarize_tool_args("bash", {"command": "npm test"})
        if bash != "npm test":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"bash summary: expected 'npm test', got '{bash}'",
            )

        read = _summarize_tool_args("read_file", {"path": "/home/src/app.py"})
        if read != "app.py":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"read_file summary: expected 'app.py', got '{read}'",
            )

        write = _summarize_tool_args("write_file", {"path": "/tmp/out.txt"})
        if write != "out.txt":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"write_file summary: expected 'out.txt', got '{write}'",
            )

        edit = _summarize_tool_args("edit_file", {"path": "loom/tui/app.py"})
        if edit != "app.py":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"edit_file summary: expected 'app.py', got '{edit}'",
            )

        glob_ = _summarize_tool_args("glob", {"pattern": "**/*.py"})
        if glob_ != "**/*.py":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"glob summary: expected '**/*.py', got '{glob_}'",
            )

        todo = _summarize_tool_args("todo_write", {"todos": [{"text": "a"}, {"text": "b"}]})
        if todo != "2 tasks":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"todo_write summary: expected '2 tasks', got '{todo}'",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail=(
                "bash → 'npm test', read_file → 'app.py', "
                "write_file → 'out.txt', edit_file → 'app.py', "
                "glob → '**/*.py', todo_write → '2 tasks'"
            ),
        )


# ── Case 2: missing field returns empty string ────────────────────────────────


class TuiMarkerSummaryMissingFieldReturnsEmpty(EvalCase):
    name = "tui-marker-summary-missing-field-returns-empty"
    description = "_summarize_tool_args returns '' for missing/unknown fields (defensive .get())"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import _summarize_tool_args

        cases: list[tuple[str, str, dict, str]] = [
            ("bash empty input", "bash", {}, ""),
            ("bash None input", "bash", {}, ""),
            ("read_file missing path", "read_file", {}, ""),
            ("glob missing pattern", "glob", {}, ""),
            ("todo_write no todos", "todo_write", {}, ""),
            ("unknown tool", "unknown_tool", {"command": "ls"}, ""),
        ]

        for label, tool, inp, expected in cases:
            result = _summarize_tool_args(tool, inp)
            if result != expected:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"{label}: expected '{expected}', got '{result}'",
                )

        # Also test the None-input case separately (different type)
        none_result = _summarize_tool_args("bash", None)  # type: ignore[arg-type]
        if none_result != "":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"bash None input: expected '', got '{none_result}'",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="All 7 defensive checks return '' on missing/unknown input",
        )


# ── Case 3: marker text includes summary in running and done states ───────────


class TuiMarkerSummaryAppearsInRunningAndDone(EvalCase):
    name = "tui-marker-summary-appears-in-running-and-done"
    description = "ToolCallMarker render text includes the summary in both running and done states"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker

        marker = ToolCallMarker(
            "bash", '{"command": "npm test"}',
            tool_input={"command": "npm test"},
        )
        text = str(marker.render())
        # Running state: summary replaces 'running' suffix
        if "· npm test" not in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Running state missing summary: '{text}'",
            )
        # Must NOT show bare 'running' when summary is present
        if "· running" in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Running state shows 'running' instead of summary: '{text}'",
            )

        marker.set_complete("all tests passed", is_error=False)
        text = str(marker.render())
        # Done state: summary replaces 'done' suffix
        if "· npm test" not in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Done state missing summary: '{text}'",
            )
        if "· done" in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Done state shows 'done' instead of summary: '{text}'",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="Running state shows '· npm test' (not '· running'); done shows same (not '· done')",
        )


# ── Case 4: error still distinguishable via ⊗ + tool-error class ──────────────


class TuiMarkerSummaryErrorStillDistinguishable(EvalCase):
    name = "tui-marker-summary-error-still-distinguishable"
    description = "Error state uses ⊗ glyph + tool-error CSS class; summary alone does NOT signal error"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ToolCallMarker

        marker = ToolCallMarker(
            "bash", '{"command": "rm -rf /"}',
            tool_input={"command": "rm -rf /"},
        )
        marker.set_complete("permission denied", is_error=True)
        text = str(marker.render())

        # Error must show ⊗ glyph
        if "⊗" not in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Error state missing ⊗ glyph: '{text}'",
            )
        # Error must have tool-error CSS class
        if not marker.has_class("tool-error"):
            return EvalResult(
                name=self.name, passed=False,
                detail="Error state missing tool-error CSS class",
            )
        # Summary MUST still be present (error is NOT conveyed by summary alone)
        if "· rm -rf /" not in text:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Error state missing summary: '{text}'",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="⊗ glyph present, tool-error class present, summary visible",
        )
