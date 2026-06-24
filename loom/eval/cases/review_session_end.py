"""Harness eval cases for f-review-session-end-hook: auto-review on SessionEnd."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from loom.agent.config import CheckpointConfig, HarnessConfig, ReviewConfig
from loom.agent.loop import _run_session_end_review
from loom.agent.permissions import DEFAULT_POLICY
from loom.eval.runner import EvalCase, EvalResult


class ReviewSessionEndConfigParses(EvalCase):
    name = "review-session-end-config-parses"
    description = "harness.toml [review] section parsing works"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            ht = tmp / "harness.toml"
            ht.write_text(
                "[review]\n"
                "session_end_review = false\n",
                encoding="utf-8",
            )
            from loom.agent.config import load_config

            config = load_config(tmp)

            if config.review.session_end_review is not False:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected session_end_review=False, got {config.review.session_end_review}",
                )
            if config.review.enabled is not True:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected enabled=True default, got {config.review.enabled}",
                )
            if config.review.pre_compact_review is not False:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected pre_compact_review=False default, got {config.review.pre_compact_review}",
                )
            if config.review.max_turns != 15:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected max_turns=15 default, got {config.review.max_turns}",
                )

        return EvalResult(
            name=self.name, passed=True,
            detail="session_end_review=false parsed; other fields match defaults",
        )


class ReviewSessionEndFiresWhenActiveFeature(EvalCase):
    name = "review-session-end-fires-when-active-feature"
    description = "in-progress feature triggers review and verdict is written to progress.md"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-eval", "name": "EvalTest", "description": "eval test", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )

            config = HarnessConfig(
                policy=DEFAULT_POLICY,
                checkpoint=CheckpointConfig.from_defaults(),
                disabled_tools=frozenset(),
                review=ReviewConfig(enabled=True, session_end_review=True),
            )

            done = threading.Event()
            expected_verdict = "[review: pass]\nAll checks passed."

            with patch("loom.agent.review.run_review") as mock_run:
                def _mock_run(feature_id, description="", scope_hint=""):
                    done.set()
                    return (expected_verdict, None)
                mock_run.side_effect = _mock_run

                _run_session_end_review(tmp, config, [])

                if not done.wait(timeout=3.0):
                    return EvalResult(
                        name=self.name, passed=False,
                        detail="daemon thread did not complete in 3s",
                    )

                mock_run.assert_called_once_with("f-eval", "eval test", "EvalTest")

            progress = tmp / "progress.md"
            if not progress.exists():
                return EvalResult(
                    name=self.name, passed=False,
                    detail="progress.md was not created",
                )

            content = progress.read_text(encoding="utf-8")
            if "## Final Review" not in content:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="progress.md is missing '## Final Review' section",
                )

        return EvalResult(
            name=self.name, passed=True,
            detail="review fired; verdict written to progress.md",
        )


class ReviewSessionEndSkipsWhenNoActiveFeature(EvalCase):
    name = "review-session-end-skips-when-no-active-feature"
    description = "no in-progress/review-pending features -> review is skipped"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-done", "name": "Done", "description": "completed", "status": "done", "verification": "echo"},
                        {"id": "f-blocked", "name": "Blocked", "description": "stuck", "status": "blocked", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )

            config = HarnessConfig(
                policy=DEFAULT_POLICY,
                checkpoint=CheckpointConfig.from_defaults(),
                disabled_tools=frozenset(),
                review=ReviewConfig(enabled=True, session_end_review=True),
            )

            with patch("loom.agent.review.run_review") as mock_run:
                _run_session_end_review(tmp, config, [])
                time.sleep(0.3)  # Let daemon thread pass

                if mock_run.call_count > 0:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail=f"run_review was called {mock_run.call_count} times (expected 0)",
                    )

        return EvalResult(
            name=self.name, passed=True,
            detail="review skipped when no active feature found",
        )


class ReviewSessionEndFailClosedOnException(EvalCase):
    name = "review-session-end-fail-closed-on-exception"
    description = "run_review raises -> progress.md not written; warning logged"

    def run(self) -> EvalResult:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-crash", "name": "Crash", "description": "will raise", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )

            config = HarnessConfig(
                policy=DEFAULT_POLICY,
                checkpoint=CheckpointConfig.from_defaults(),
                disabled_tools=frozenset(),
                review=ReviewConfig(enabled=True, session_end_review=True),
            )

            warning_logged = threading.Event()

            with patch("loom.agent.review.run_review", side_effect=Exception("boom")), \
                 patch("loom.agent.loop.logger.warning") as mock_warn:
                mock_warn.side_effect = lambda *a, **kw: (warning_logged.set() if "SessionEnd review failed" in str(a) else None)

                _run_session_end_review(tmp, config, [])

                # Wait for daemon thread to hit the exception
                if not warning_logged.wait(timeout=3.0):
                    return EvalResult(
                        name=self.name, passed=False,
                        detail="logger.warning was not called with 'SessionEnd review failed'",
                    )

            progress = tmp / "progress.md"
            if progress.exists():
                content = progress.read_text(encoding="utf-8")
                if "## Final Review" in content:
                    return EvalResult(
                        name=self.name, passed=False,
                        detail="progress.md contains 'Final Review' despite review failure",
                    )

        return EvalResult(
            name=self.name, passed=True,
            detail="fail-closed: no progress.md written, warning logged",
        )
