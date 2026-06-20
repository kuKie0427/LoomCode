"""Eval cases for TUI inline event markers (f-tui-inline-event-markers)."""

from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult


class TuiSubagentMarkerAddDefined(EvalCase):
    name = "tui-subagent-marker-add-defined"
    description = "ChatLog.add_subagent_marker method exists"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ChatLog

        if not hasattr(ChatLog, "add_subagent_marker"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ChatLog.add_subagent_marker not found",
            )
        if not callable(getattr(ChatLog, "add_subagent_marker", None)):
            return EvalResult(
                name=self.name, passed=False,
                detail="ChatLog.add_subagent_marker is not callable",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="ChatLog.add_subagent_marker exists and is callable",
        )


class TuiSubagentMarkerCompleteDefined(EvalCase):
    name = "tui-subagent-marker-complete-defined"
    description = "ChatLog.complete_subagent_marker method exists"

    def run(self) -> EvalResult:
        from loom.tui.chat_log import ChatLog

        if not hasattr(ChatLog, "complete_subagent_marker"):
            return EvalResult(
                name=self.name, passed=False,
                detail="ChatLog.complete_subagent_marker not found",
            )
        if not callable(getattr(ChatLog, "complete_subagent_marker", None)):
            return EvalResult(
                name=self.name, passed=False,
                detail="ChatLog.complete_subagent_marker is not callable",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="ChatLog.complete_subagent_marker exists and is callable",
        )


class TuiAppWiresSubagentMarkerInStart(EvalCase):
    name = "tui-app-wires-subagent-marker-in-start"
    description = "AgentTUIApp.on_subagent_start calls chat_log.add_subagent_marker"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        src = inspect.getsource(AgentTUIApp.on_subagent_start)
        if "add_subagent_marker" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail="on_subagent_start does not call add_subagent_marker",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="on_subagent_start wires add_subagent_marker",
        )


class TuiAppWiresSubagentMarkerInEnd(EvalCase):
    name = "tui-app-wires-subagent-marker-in-end"
    description = "AgentTUIApp.on_subagent_end calls chat_log.complete_subagent_marker"

    def run(self) -> EvalResult:
        from loom.tui.app import AgentTUIApp

        src = inspect.getsource(AgentTUIApp.on_subagent_end)
        if "complete_subagent_marker" not in src:
            return EvalResult(
                name=self.name, passed=False,
                detail="on_subagent_end does not call complete_subagent_marker",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="on_subagent_end wires complete_subagent_marker",
        )
