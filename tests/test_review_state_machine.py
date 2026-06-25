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


class TestP1FlipStatusOnPass:
    """P1-1: run_review(flips_status_on_pass=True) flips feature_list.json
    status to 'done' when verdict=pass and fd.action=['none'].

    This is the 'hard guarantee' path — even if the Orchestrator LLM forgets
    to call edit_file, the status flips correctly inside run_review.
    """

    def test_pass_with_none_action_flips_to_done(self, tmp_path):
        """verdict=pass + action=[none] → feature_list.json status becomes 'done'."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(json.dumps({
            "features": [
                {"id": "f-flip", "name": "Flip", "description": "flip test",
                 "status": "in-progress", "verification": "echo"},
            ]
        }), encoding="utf-8")

        from unittest.mock import patch
        mock_return = (
            '<verdict>{"status":"pass","summary":"OK","evidence":[],"recommendations":[]}</verdict>\n'
            '<feedback_directive>\naction: none\ntarget_files: []\n'
            'target_lines: []\nretry_review: false\nnotes: "ok"\n</feedback_directive>'
        )
        with patch("loom.agent.tools.spawn_subagent", return_value=mock_return):
            from loom.agent.review import run_review
            run_review("f-flip", "flip test", workdir=tmp_path, flip_status_on_pass=True)

        data = json.loads(fl.read_text(encoding="utf-8"))
        assert data["features"][0]["status"] == "done"
        assert "completed_at" in data["features"][0]

    def test_pass_without_flip_flag_does_not_flip(self, tmp_path):
        """flip_status_on_pass=False (default) → status stays 'in-progress'."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(json.dumps({
            "features": [
                {"id": "f-noflip", "name": "NoFlip", "description": "no flip",
                 "status": "in-progress", "verification": "echo"},
            ]
        }), encoding="utf-8")

        from unittest.mock import patch
        mock_return = (
            '<verdict>{"status":"pass","summary":"OK","evidence":[],"recommendations":[]}</verdict>\n'
            '<feedback_directive>\naction: none\ntarget_files: []\n'
            'target_lines: []\nretry_review: false\nnotes: "ok"\n</feedback_directive>'
        )
        with patch("loom.agent.tools.spawn_subagent", return_value=mock_return):
            from loom.agent.review import run_review
            run_review("f-noflip", "no flip", workdir=tmp_path)  # flip_status_on_pass=False

        data = json.loads(fl.read_text(encoding="utf-8"))
        assert data["features"][0]["status"] == "in-progress"

    def test_fail_verdict_does_not_flip(self, tmp_path):
        """verdict=fail → status stays 'in-progress' even with flip_status_on_pass=True."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(json.dumps({
            "features": [
                {"id": "f-fail", "name": "Fail", "description": "fail test",
                 "status": "in-progress", "verification": "echo"},
            ]
        }), encoding="utf-8")

        from unittest.mock import patch
        mock_return = (
            '<verdict>{"status":"fail","summary":"bug","evidence":["a.py:1"],"recommendations":["fix"]}</verdict>\n'
            '<feedback_directive>\naction: fix_bug\ntarget_files: ["a.py"]\n'
            'target_lines: ["1"]\nretry_review: true\nnotes: "fix"\n</feedback_directive>'
        )
        with patch("loom.agent.tools.spawn_subagent", return_value=mock_return):
            from loom.agent.review import run_review
            run_review("f-fail", "fail test", workdir=tmp_path, flip_status_on_pass=True)

        data = json.loads(fl.read_text(encoding="utf-8"))
        assert data["features"][0]["status"] == "in-progress"

    def test_already_done_does_not_re_flip(self, tmp_path):
        """Feature already 'done' → no re-flip, no completed_at overwrite."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(json.dumps({
            "features": [
                {"id": "f-done", "name": "Done", "description": "already done",
                 "status": "done", "verification": "echo", "completed_at": "original"},
            ]
        }), encoding="utf-8")

        from unittest.mock import patch
        mock_return = (
            '<verdict>{"status":"pass","summary":"OK","evidence":[],"recommendations":[]}</verdict>\n'
            '<feedback_directive>\naction: none\ntarget_files: []\n'
            'target_lines: []\nretry_review: false\nnotes: "ok"\n</feedback_directive>'
        )
        with patch("loom.agent.tools.spawn_subagent", return_value=mock_return):
            from loom.agent.review import run_review
            run_review("f-done", "already done", workdir=tmp_path, flip_status_on_pass=True)

        data = json.loads(fl.read_text(encoding="utf-8"))
        assert data["features"][0]["status"] == "done"
        assert data["features"][0]["completed_at"] == "original"  # not overwritten
