"""Integration tests for triangle protocol integration (Tasks 1-4).

Covers: spawn_subagent protocol header injection, I4 soft enforcement,
run_review delta_report injection, pre-validation bypass (I7/I8),
feedback_directive parsing (I5/I6), backward-compat wrapper.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import TextBlock

from loom.agent.tools import spawn_subagent
from loom.agent.triangle_protocol import (
    DeltaReport,
    FeatureCard,
    FileChange,
    ScopeEnvelope,
)


@pytest.fixture
def mock_subagent_client(monkeypatch) -> MagicMock:
    """Create a mock LLM client that returns a single end_turn response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [
        TextBlock(type="text", text="Task completed successfully."),
    ]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 50
    mock_client.invoke = MagicMock(return_value=mock_response)
    return mock_client


class TestSpawnSubagentProtocolInjection:
    """TP-2: feature_card and scope injection into subagent description."""

    def test_spawn_subagent_with_feature_card_injects_block(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """serialize_feature_card is prepended to the user message."""
        card = FeatureCard(
            id="f-001", name="Test",
            description="A test feature", verification="pytest",
        )
        spawn_subagent("Do X", feature_card=card, llm_client=mock_subagent_client)
        call_kwargs = mock_subagent_client.invoke.call_args[1]
        first_msg = call_kwargs["messages"][0]["content"]
        assert "<feature_card>" in first_msg
        assert first_msg.startswith("<feature_card>")

    def test_spawn_subagent_with_scope_injects_block(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """serialize_scope_envelope is prepended to the user message."""
        scope = ScopeEnvelope(
            allow_paths=("src/**",), deny_paths=(),
            allow_actions=("read",), deny_actions=(),
        )
        spawn_subagent("Do X", scope=scope, llm_client=mock_subagent_client)
        call_kwargs = mock_subagent_client.invoke.call_args[1]
        first_msg = call_kwargs["messages"][0]["content"]
        assert "<scope_envelope>" in first_msg
        assert first_msg.startswith("<scope_envelope>")

    def test_spawn_subagent_with_both_injects_in_order(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """feature_card appears before scope_envelope when both provided."""
        card = FeatureCard(
            id="f-001", name="Test",
            description="A test feature", verification="pytest",
        )
        scope = ScopeEnvelope(
            allow_paths=("src/**",), deny_paths=(),
            allow_actions=("read",), deny_actions=(),
        )
        spawn_subagent(
            "Do X", feature_card=card, scope=scope,
            llm_client=mock_subagent_client,
        )
        call_kwargs = mock_subagent_client.invoke.call_args[1]
        first_msg = call_kwargs["messages"][0]["content"]
        fc_pos = first_msg.index("<feature_card>")
        se_pos = first_msg.index("<scope_envelope>")
        assert fc_pos < se_pos, (
            "feature_card should appear before scope_envelope"
        )

    def test_spawn_subagent_without_protocol_args_backward_compat(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """No feature_card/scope: description passed as-is (legacy mode)."""
        spawn_subagent("Do X", llm_client=mock_subagent_client)
        call_kwargs = mock_subagent_client.invoke.call_args[1]
        first_msg = call_kwargs["messages"][0]["content"]
        assert first_msg == "Do X"


class TestSpawnSubagentProtocolEnforcement:
    """I4 soft enforcement: <delta_report> reminder."""

    def test_spawn_subagent_appends_reminder_on_missing_delta(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """feature_card provided but no <delta_report> in result: violation reminder."""
        card = FeatureCard(
            id="f-001", name="Test",
            description="A test feature", verification="pytest",
        )
        result = spawn_subagent(
            "Do X", feature_card=card, llm_client=mock_subagent_client,
        )
        assert "<system-reminder>" in result
        assert "triangle-protocol violation" in result
        assert "Task completed successfully." in result

    def test_spawn_subagent_no_check_without_feature_card(
        self, mock_subagent_client: MagicMock,
    ) -> None:
        """No feature_card: no protocol check, no reminder appended."""
        result = spawn_subagent("Do X", llm_client=mock_subagent_client)
        assert "<system-reminder>" not in result


class TestRunReviewDeltaReport:
    """TP-2: delta_report injection into Reviewer prompt."""

    def test_run_review_with_delta_report_injects_block(self) -> None:
        """When delta_report is provided, it is serialized into the prompt."""
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="foo.py", lines_added=1, lines_deleted=0, summary="test"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="pytest",
            verification_result="pass",
            escalations=(),
        )
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"OK",'
                '"evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review

            run_review("f-test", "desc", delta_report=delta)

            prompt = mock_spawn.call_args[0][0]
            assert "<delta_report>" in prompt
            assert "foo.py" in prompt


class TestRunReviewReturnType:
    """run_review tuple return type."""

    def test_run_review_returns_tuple(self) -> None:
        """run_review returns (str, FeedbackDirective | None)."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"OK",'
                '"evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review

            result = run_review("f-test", "desc")
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], str)
            # No <feedback_directive> in mock response: fd is None
            assert result[1] is None


class TestRunReviewPreValidation:
    """I7/I8: pre-validation bypasses LLM on scope/git diff violations."""

    def test_run_review_pre_validation_scope_violation_bypasses_llm(
        self,
    ) -> None:
        """Scope violation triggers early return without LLM call."""
        scope = ScopeEnvelope(
            allow_paths=("src/**",),
            deny_paths=("secrets/**",),
            allow_actions=("read", "write"),
            deny_actions=(),
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(
                    path="secrets/passwords.py",
                    lines_added=1, lines_deleted=0, summary="leak",
                ),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = "should not be called"
            from loom.agent.review import run_review

            result = run_review(
                "f-test", "desc", delta_report=delta, scope=scope,
            )
            assert "unknown" in result[0]
            assert "预校验失败" in result[0]
            mock_spawn.assert_not_called()

    def test_run_review_pre_validation_git_diff_mismatch_bypasses_llm(
        self, tmp_path: Path,
    ) -> None:
        """Non-matching git diff triggers early return without LLM call."""
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="foo.py", lines_added=1, lines_deleted=0, summary="change"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = "should not be called"
            from loom.agent.review import run_review

            # tmp_path has no git repo: git diff fails → violations
            result = run_review(
                "f-test", "desc", delta_report=delta, workdir=tmp_path,
            )
            assert "unknown" in result[0]
            assert "预校验失败" in result[0]
            mock_spawn.assert_not_called()

    def test_run_review_pre_validation_pass_proceeds_to_llm(self) -> None:
        """No delta_report: pre-validation skipped, LLM is called."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"OK",'
                '"evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review

            run_review("f-test", "desc")
            mock_spawn.assert_called_once()


class TestRunReviewFeedbackDirective:
    """I5/I6: feedback_directive parsing and validation."""

    def test_run_review_feedback_directive_invalid_combination_returns_none_fd(
        self,
    ) -> None:
        """Invalid action combination (none + fix_bug) results in fd=None."""
        invalid_result = (
            '<verdict>{"status":"pass","summary":"OK",'
            '"evidence":[],"recommendations":[]}</verdict>\n'
            '<feedback_directive>\n'
            'action: [none, fix_bug]\n'
            'retry_review: false\n'
            '</feedback_directive>'
        )
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = invalid_result
            from loom.agent.review import run_review

            result = run_review("f-test", "desc")
            assert result[1] is None


class TestRunReviewLegacyStr:
    """Backward-compat wrapper for run_review_legacy_str."""

    def test_run_review_legacy_str_backward_compat(self) -> None:
        """Legacy wrapper returns a plain str (not tuple)."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"OK",'
                '"evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review_legacy_str

            result = run_review_legacy_str("f-test", "desc")
            assert isinstance(result, str)
