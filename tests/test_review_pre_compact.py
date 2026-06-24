"""Comprehensive unit tests for the PreCompact auto-review hook (f-pre-compact-review).

Covers _run_pre_compact_review opt-in/opt-out behavior, verdict injection,
fail-closed semantics, single-review-per-feature dedup, and session-start cache reset.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import loom.agent.loop as loop_mod
from loom.agent.config import CheckpointConfig, HarnessConfig, ReviewConfig
from loom.agent.loop import _run_pre_compact_review
from loom.agent.permissions import DEFAULT_POLICY


def _make_config(pre_compact_review: bool = False, enabled: bool = True) -> HarnessConfig:
    return HarnessConfig(
        policy=DEFAULT_POLICY,
        checkpoint=CheckpointConfig.from_defaults(),
        disabled_tools=frozenset(),
        review=ReviewConfig(enabled=enabled, pre_compact_review=pre_compact_review),
    )


class TestPreCompactReview:
    """_run_pre_compact_review opt-in guard, verdict injection, fail-closed."""

    def setup_method(self) -> None:
        """Reset the module-level cache before each test."""
        loop_mod._LAST_REVIEWED_FEATURE_ID = None

    def test_pre_compact_review_false_skips(self, tmp_path, monkeypatch):
        """pre_compact_review=False returns without appending a verdict message."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=False)
        messages: list = []
        _run_pre_compact_review(messages, config)
        assert len(messages) == 0

    def test_pre_compact_review_true_calls_review(self, tmp_path, monkeypatch):
        """pre_compact_review=True causes verdict to be appended as user message."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        with patch("loom.agent.review.run_review", return_value=("[review: pass] OK", None)):
            _run_pre_compact_review(messages, config)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "[system-reminder] PreCompact review verdict" in str(messages[0]["content"])

    def test_pre_compact_review_fail_closed(self, tmp_path, monkeypatch):
        """run_review raises -> no verdict appended, warning logged."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        with patch("loom.agent.review.run_review", side_effect=Exception("boom")), \
             patch("loom.agent.loop.logger.warning") as mock_warn:
            _run_pre_compact_review(messages, config)
        assert len(messages) == 0
        mock_warn.assert_called_once()
        assert "_run_pre_compact_review: failed:" in str(mock_warn.call_args[0])

    def test_verdict_message_has_system_reminder_label(self, tmp_path, monkeypatch):
        """Appended message content starts with '[system-reminder]'."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        with patch("loom.agent.review.run_review", return_value=("[review: pass] OK", None)):
            _run_pre_compact_review(messages, config)
        assert messages[0]["content"].startswith("[system-reminder]")


class TestPreCompactReviewTP4:
    """TP-4 upgrades: C10 fix (verdict + feedback_directive in reminder) and I9 persistence."""

    def setup_method(self) -> None:
        loop_mod._LAST_REVIEWED_FEATURE_ID = None

    def test_system_reminder_includes_feedback_directive_block(self, tmp_path, monkeypatch):
        """C10 fix: when run_review returns a FeedbackDirective, the system-reminder
        must include the serialized <feedback_directive>...</feedback_directive> block
        alongside verdict_str. This is what lets the Orchestrator LLM see the action
        it must execute (scope_trim / fix_bug / etc.).
        """
        from loom.agent.triangle_protocol import FeedbackDirective

        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({"features": [
                {"id": "f-c10", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
            ]}),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        fd = FeedbackDirective(
            action=("fix_bug",),
            target_files=("src/x.py",),
            target_lines=("10-20",),
            retry_review=False,
            notes="fix the bug",
        )
        with patch("loom.agent.review.run_review", return_value=("[review: fail] bug found", fd)):
            _run_pre_compact_review(messages, config)
        content = messages[0]["content"]
        assert "[system-reminder]" in content
        assert "[review: fail] bug found" in content
        # C10 fix: feedback_directive block must be present
        assert "<feedback_directive>" in content
        assert "fix_bug" in content
        assert "src/x.py" in content

    def test_increments_review_attempts_in_feature_list(self, tmp_path, monkeypatch):
        """I9 persistence: review_attempts counter in feature_list.json is incremented
        after each PreCompact review (TP-4 upgrade — counter survives autocompact).
        """
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({"features": [
                {"id": "f-i9", "name": "T", "description": "d", "status": "in-progress",
                 "verification": "echo", "review_attempts": 0},
            ]}),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        with patch("loom.agent.review.run_review", return_value=("[review: pass] OK", None)):
            _run_pre_compact_review(messages, config)
        # Read feature_list.json back, verify review_attempts was bumped to 1
        updated = json.loads(fl.read_text(encoding="utf-8"))
        assert updated["features"][0]["review_attempts"] == 1

    def test_increments_review_attempts_accumulates_across_sessions(self, tmp_path, monkeypatch):
        """I9: two reviews for the same feature → review_attempts = 2, not reset to 1.

        Guard against regression where someone re-implements counter as in-memory
        and resets per-session (losing the cross-session safety bound).
        """
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({"features": [
                {"id": "f-i9-2", "name": "T", "description": "d", "status": "in-progress",
                 "verification": "echo", "review_attempts": 5},
            ]}),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        with patch("loom.agent.review.run_review", return_value=("[review: fail] still bad", None)):
            # First "session" — increment to 6
            _run_pre_compact_review([], config)
        # Simulate a new session by clearing the in-memory dedup cache
        loop_mod._LAST_REVIEWED_FEATURE_ID = None
        with patch("loom.agent.review.run_review", return_value=("[review: fail] again", None)):
            # Second "session" — should be 7 (persisted from JSON, not reset to 0)
            _run_pre_compact_review([], config)
        updated = json.loads(fl.read_text(encoding="utf-8"))
        assert updated["features"][0]["review_attempts"] == 7


class TestPreCompactNotDoubleReview:
    """_LAST_REVIEWED_FEATURE_ID prevents re-reviewing the same feature."""

    def test_same_feature_not_reviewed_twice(self, tmp_path, monkeypatch):
        """Calling _run_pre_compact_review twice with the same feature skips the second."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        loop_mod._LAST_REVIEWED_FEATURE_ID = None

        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-same", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = _make_config(pre_compact_review=True)
        messages: list = []
        with patch("loom.agent.review.run_review", return_value=("[review: pass] OK", None)) as mock_run:
            _run_pre_compact_review(messages, config)
            _run_pre_compact_review(messages, config)
        assert mock_run.call_count == 1

    def test_different_feature_reviewed_again(self, tmp_path, monkeypatch):
        """Changing the active feature between calls triggers a new review."""
        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        loop_mod._LAST_REVIEWED_FEATURE_ID = None

        fl = tmp_path / "feature_list.json"
        config = _make_config(pre_compact_review=True)
        messages: list = []

        with patch("loom.agent.review.run_review", return_value=("[review: pass] OK", None)) as mock_run:
            # First call with feature A
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-first", "name": "A", "description": "first", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            _run_pre_compact_review(messages, config)

            # Second call with feature B — cache still holds "f-first",
            # so this call should re-review because "f-second" != "f-first".
            fl.write_text(
                json.dumps({
                    "features": [
                        {"id": "f-second", "name": "B", "description": "second", "status": "in-progress", "verification": "echo"},
                    ]
                }),
                encoding="utf-8",
            )
            _run_pre_compact_review(messages, config)

        assert mock_run.call_count == 2


class TestSessionStartResetsCache:
    """_LAST_REVIEWED_FEATURE_ID is reset at session start (top of agent_loop)."""

    def test_session_start_resets_last_reviewed(self):
        """agent_loop() entry unconditionally resets _LAST_REVIEWED_FEATURE_ID to None.

        Uses AST inspection to verify the assignment exists in the function
        body, so we don't need a complex agent_loop() integration test.
        """
        import ast
        import inspect

        source = inspect.getsource(loop_mod.agent_loop)
        tree = ast.parse(source)
        func_body = tree.body[0].body

        found_reset = any(
            isinstance(stmt, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_LAST_REVIEWED_FEATURE_ID"
                for t in stmt.targets
            )
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is None
            for stmt in func_body
        )
        assert found_reset, (
            "agent_loop() must assign _LAST_REVIEWED_FEATURE_ID = None "
            "at the top of the function body"
        )
