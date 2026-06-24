"""Unit tests for f-review-state-machine: review-pending status in schema, scope, audit, and system prompt."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


class TestSchema:
    """feature_list.schema.json status enum includes review-pending."""

    def test_schema_enum_contains_review_pending(self):
        """feature_list.schema.json status enum contains review-pending."""
        schema = json.loads(Path("feature_list.schema.json").read_text(encoding="utf-8"))
        statuses = schema["properties"]["features"]["items"]["properties"]["status"]["enum"]
        assert "review-pending" in statuses
        # Verify order: before done, after blocked
        assert statuses.index("review-pending") < statuses.index("done")
        assert statuses.index("blocked") < statuses.index("review-pending")

    def test_templates_schema_sync(self):
        """loom/templates/feature-list.schema.json has same enum."""
        schema = json.loads(
            Path("loom/templates/feature-list.schema.json").read_text(encoding="utf-8")
        )
        statuses = schema["properties"]["features"]["items"]["properties"]["status"]["enum"]
        assert "review-pending" in statuses
        assert statuses.index("review-pending") < statuses.index("done")


class TestScopeWip1:
    """check_wip1 treats review-pending as an active state alongside in-progress."""

    def test_in_progress_and_review_pending_both_active(self, tmp_path):
        """Both in-progress and review-pending count as active."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-a", "name": "A", "description": "desc", "status": "in-progress", "verification": "echo"},
                    {"id": "f-b", "name": "B", "description": "desc", "status": "review-pending", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        from loom.agent.scope import check_wip1

        result = check_wip1(tmp_path)
        assert "f-a" in result
        assert "f-b" in result

    def test_only_review_pending_triggers_warn_when_gt_one(self, tmp_path):
        """Multiple review-pending features both appear (WIP=1 warning surface)."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-a", "name": "A", "description": "desc", "status": "review-pending", "verification": "echo"},
                    {"id": "f-b", "name": "B", "description": "desc", "status": "review-pending", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        from loom.agent.scope import check_wip1

        result = check_wip1(tmp_path)
        assert len(result) == 2

    def test_blocked_not_active(self, tmp_path):
        """blocked does NOT count as active."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-a", "name": "A", "description": "desc", "status": "blocked", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        from loom.agent.scope import check_wip1

        result = check_wip1(tmp_path)
        assert "f-a" not in result

    def test_silent_on_missing(self, tmp_path):
        """No crash when feature_list.json is missing."""
        from loom.agent.scope import check_wip1

        result = check_wip1(tmp_path)
        assert result == []


class TestAuditStateMachine:
    """audit_cmd.py state dimension recognizes review-pending."""

    def test_audit_report_contains_review_pending(self, tmp_path):
        """State dimension recognizes review-pending status."""
        from loom.audit_cmd import HarnessFile, score_harness

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
        result = score_harness(files, target=tmp_path, skip_self_test=True)
        state_checks = result.subsystems["state"].checks
        check_msgs = [c.message for c in state_checks]
        assert any("review-pending" in msg for msg in check_msgs)

    def test_review_pending_does_not_deduct_score(self, tmp_path):
        """review-pending is a normal intermediate state, no penalty."""
        from loom.audit_cmd import HarnessFile, score_harness

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
        result = score_harness(files, target=tmp_path, skip_self_test=True)
        assert result.subsystems["state"].score >= 1  # Not penalized to 0

    def test_old_done_features_grandfathered(self, tmp_path):
        """Old done features do NOT require review_verdict (grandfather clause)."""
        from loom.audit_cmd import HarnessFile, score_harness

        feature_list = json.dumps({
            "project": "test",
            "features": [
                {"id": "f-old", "name": "Old", "description": "desc", "status": "done", "verification": "echo", "evidence": "works"},
            ],
        })
        files = [
            HarnessFile(path="feature_list.json", content=feature_list),
            HarnessFile(path="progress.md", content="# Progress\n## Current State\n"),
        ]
        result = score_harness(files, target=tmp_path, skip_self_test=True)
        state_checks = result.subsystems["state"].checks
        valid_check = [c for c in state_checks if "valid" in c.message.lower()]
        assert all(c.pass_ for c in valid_check)


class TestSystemPrompt:
    """SystemPrompt static tier contains review-priority rule."""

    def test_static_contains_review_priority(self):
        """SystemPrompt static tier contains '审查优先'."""
        from loom.agent.system_prompt import build_fresh

        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_fresh(Path(tmp))
        assert "审查优先" in prompt

    def test_static_contains_must_review_before_done(self):
        """Static rule says must review before marking done."""
        from loom.agent.system_prompt import build_fresh

        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_fresh(Path(tmp))
        assert "标 feature 为 done 前必须调 review" in prompt
