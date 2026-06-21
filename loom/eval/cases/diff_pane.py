"""Harness eval cases for f-tui-diff-viewer-p3 (minimal scope)."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class DiffPaneModuleDefined(EvalCase):
    name = "diff-pane-module-defined"
    description = "loom.tui.diff_pane module exists with public API"

    def run(self) -> EvalResult:
        try:
            from loom.tui import diff_pane
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("DiffPane", "colorize_diff"):
            if not hasattr(diff_pane, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="DiffPane + colorize_diff present")


class DiffPaneColorsCorrectly(EvalCase):
    name = "diff-pane-colors-correctly"
    description = "colorize_diff applies green to +, red to -, yellow to @@"

    def run(self) -> EvalResult:
        from loom.tui.diff_pane import colorize_diff
        out = colorize_diff("+added\n-removed\n@@ hunk\n")
        if "[green]+added" not in out:
            return EvalResult(name=self.name, passed=False, detail="green missing")
        if "[red]-removed" not in out:
            return EvalResult(name=self.name, passed=False, detail="red missing")
        if "[yellow]@@ hunk" not in out:
            return EvalResult(name=self.name, passed=False, detail="yellow missing")
        return EvalResult(name=self.name, passed=True, detail="green/red/yellow all present")


class DiffPaneEmptyShowsPlaceholder(EvalCase):
    name = "diff-pane-empty-shows-placeholder"
    description = "DiffPane.render() returns placeholder for empty/whitespace content"

    def run(self) -> EvalResult:
        from loom.tui.diff_pane import DiffPane
        pane = DiffPane()
        out = pane.render()
        if "no diff" not in out.lower():
            return EvalResult(name=self.name, passed=False, detail=f"got: {out!r}")
        return EvalResult(name=self.name, passed=True, detail="empty content shows placeholder")
