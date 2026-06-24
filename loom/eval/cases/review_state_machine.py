"""Harness eval cases for f-review-state-machine: review-pending status recognition."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class ReviewStateMachineSchemaHasReviewPending(EvalCase):
    name = "review-state-machine-schema-has-review-pending"
    description = "feature_list.schema.json status enum contains review-pending"

    def run(self) -> EvalResult:
        schema = json.loads(Path("feature_list.schema.json").read_text(encoding="utf-8"))
        statuses = schema["properties"]["features"]["items"]["properties"]["status"]["enum"]
        if "review-pending" not in statuses:
            return EvalResult(
                name=self.name, passed=False,
                detail="review-pending not in status enum",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="review-pending is in feature_list.schema.json status enum",
        )


class ReviewStateMachineScopeWip1CountsReviewPending(EvalCase):
    name = "review-state-machine-scope-wip1-counts-review-pending"
    description = "check_wip1 counts review-pending features alongside in-progress"

    def run(self) -> EvalResult:
        from loom.agent.scope import check_wip1

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fl = tmp / "feature_list.json"
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-a", "name": "A", "description": "desc", "status": "in-progress", "verification": "echo"},
                        {"id": "f-b", "name": "B", "description": "desc", "status": "review-pending", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            result = check_wip1(tmp)
            if "f-a" in result and "f-b" in result and len(result) == 2:
                return EvalResult(
                    name=self.name, passed=True,
                    detail="both in-progress and review-pending are counted as active",
                )
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 2 features (f-a, f-b), got {result}",
            )


class ReviewStateMachineAuditRecognizesReviewPending(EvalCase):
    name = "review-state-machine-audit-recognizes-review-pending"
    description = "audit report state dimension recognizes review-pending"

    def run(self) -> EvalResult:
        from loom.audit_cmd import HarnessFile, score_harness

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            feature_list = json.dumps({
                "project": "test",
                "features": [
                    {"id": "f-test", "name": "Test", "description": "desc", "status": "review-pending", "verification": "echo"},
                ],
            })
            files = [
                HarnessFile(path="feature_list.json", content=feature_list),
                HarnessFile(path="progress.md", content="# Progress\n## Current State\n"),
            ]
            result = score_harness(files, target=tmp, skip_self_test=True)
            state_checks = result.subsystems["state"].checks
            check_msgs = [c.message for c in state_checks]
            if any("review-pending" in msg for msg in check_msgs):
                return EvalResult(
                    name=self.name, passed=True,
                    detail="audit state dimension recognizes review-pending",
                )
            return EvalResult(
                name=self.name, passed=False,
                detail=f"no check mentions review-pending: {check_msgs}",
            )
