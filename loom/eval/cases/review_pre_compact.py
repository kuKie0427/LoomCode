"""Harness eval cases for f-pre-compact-review: auto-review before autocompact."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import loom.agent.loop as loop_mod
from loom.agent.config import CheckpointConfig, HarnessConfig, ReviewConfig
from loom.agent.loop import _run_pre_compact_review
from loom.agent.permissions import DEFAULT_POLICY
from loom.eval.runner import EvalCase, EvalResult


def _config(pre_compact_review: bool = True) -> HarnessConfig:
    return HarnessConfig(
        policy=DEFAULT_POLICY,
        checkpoint=CheckpointConfig.from_defaults(),
        disabled_tools=frozenset(),
        review=ReviewConfig(enabled=True, pre_compact_review=pre_compact_review),
    )


class PreCompactConfigRespectsOptIn(EvalCase):
    name = "review-pre-compact-config-respects-opt-in"
    description = "pre_compact_review=False means review is skipped"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-opt", "name": "Opt", "description": "d", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            config = _config(pre_compact_review=False)

            with patch("loom.agent.review.run_review") as mock_run:
                with patch.object(loop_mod, "WORKDIR", tmp):
                    _run_pre_compact_review([], config)
                if mock_run.call_count > 0:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=f"run_review called {mock_run.call_count}x (expected 0)",
                    )

            return EvalResult(
                name=self.name, passed=True,
                detail="pre_compact_review=False skips review",
            )


class PreCompactInjectsVerdictAsSystemReminder(EvalCase):
    name = "review-pre-compact-injects-verdict-as-system-reminder"
    description = "verdict is injected as a [system-reminder] user message"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-verdict", "name": "V", "description": "d", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            config = _config(pre_compact_review=True)
            messages: list = []

            with patch("loom.agent.review.run_review", return_value=("[review: pass] All good.", None)):
                with patch.object(loop_mod, "WORKDIR", tmp):
                    _run_pre_compact_review(messages, config)

            if len(messages) == 0:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="no message was appended",
                )
            content = messages[0]["content"]
            if not content.startswith("[system-reminder] PreCompact review verdict"):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"content does not start with system-reminder label: {content[:80]}",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail="verdict injected as [system-reminder] user message",
            )


class PreCompactFailClosed(EvalCase):
    name = "review-pre-compact-fail-closed"
    description = "run_review raises -> no verdict appended, warning logged"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-crash", "name": "C", "description": "d", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            config = _config(pre_compact_review=True)
            messages: list = []
            warning_found = False

            with patch("loom.agent.review.run_review", side_effect=Exception("boom")), \
                 patch("loom.agent.loop.logger.warning") as mock_warn:
                with patch.object(loop_mod, "WORKDIR", tmp):
                    _run_pre_compact_review(messages, config)
                if mock_warn.call_count > 0:
                    for args in mock_warn.call_args_list:
                        if "_run_pre_compact_review: failed:" in str(args[0]):
                            warning_found = True

            if len(messages) > 0:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"verdict appended despite exception ({len(messages)} messages)",
                )
            if not warning_found:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="logger.warning not called with '_run_pre_compact_review: failed:'",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail="fail-closed: no verdict appended, warning logged",
            )
