"""Comprehensive unit tests for the review tool module (loom/agent/review.py).

Covers ReviewVerdict dataclass, _parse_verdict(), run_review(), REVIEW_TOOLS
whitelist, and TOOL_REGISTRY/SUB_TOOLS integration.
"""

from unittest.mock import patch

from loom.agent.review import ReviewVerdict, _parse_verdict
from loom.agent.tools import REVIEW_TOOLS, SUB_TOOLS, TOOL_REGISTRY


class TestReviewVerdictDataclass:
    """ReviewVerdict dataclass: to_json / from_json roundtrip and defaults."""

    def test_to_json_from_json_roundtrip(self):
        v = ReviewVerdict(
            status="pass",
            summary="OK",
            evidence=["a.py:1"],
            recommendations=["add test"],
        )
        j = v.to_json()
        v2 = ReviewVerdict.from_json(j)
        assert v2.status == "pass"
        assert v2.summary == "OK"
        assert v2.evidence == ["a.py:1"]
        assert v2.recommendations == ["add test"]

    def test_unknown_status_default(self):
        """status='unknown' with no other fields produces empty defaults."""
        v = ReviewVerdict(status="unknown")
        assert v.status == "unknown"
        assert v.summary == ""
        assert v.evidence == []
        assert v.recommendations == []
        assert v.raw_response == ""

    def test_evidence_list_serialization(self):
        """Multiple evidence items survive JSON roundtrip."""
        v = ReviewVerdict(
            status="quality_issue",
            summary="Some quality concerns",
            evidence=[
                "src/foo.py:10: unused variable",
                "src/bar.py:42: missing docstring",
            ],
            recommendations=["Remove unused variable", "Add docstring"],
        )
        j = v.to_json()
        v2 = ReviewVerdict.from_json(j)
        assert v2.evidence == [
            "src/foo.py:10: unused variable",
            "src/bar.py:42: missing docstring",
        ]

    def test_recommendations_serialization(self):
        """Recommendations with special characters survive roundtrip."""
        v = ReviewVerdict(
            status="fail",
            summary="Bug found",
            evidence=["src/bug.py:5"],
            recommendations=[
                "Fix off-by-one error in range()",
                "Add unit tests for edge cases (null input, empty list)",
            ],
        )
        j = v.to_json()
        v2 = ReviewVerdict.from_json(j)
        assert v2.recommendations == [
            "Fix off-by-one error in range()",
            "Add unit tests for edge cases (null input, empty list)",
        ]
        assert v2.status == "fail"


class TestParseVerdict:
    """_parse_verdict: <verdict> tag extraction and JSON parsing."""

    def test_valid_verdict_tag(self):
        """Normal <verdict>...</verdict> tag returns correct ReviewVerdict."""
        text = (
            'Some preamble\n'
            '<verdict>{"status":"pass","summary":"Looks good","evidence":["x.py:1:ok"],"recommendations":[]}</verdict>\n'
            'Some trailing text'
        )
        v = _parse_verdict(text)
        assert v.status == "pass"
        assert v.summary == "Looks good"
        assert v.evidence == ["x.py:1:ok"]
        assert v.recommendations == []

    def test_missing_tags(self):
        """No <verdict> tag at all → status=unknown, raw_response preserved."""
        text = "just some text without any verdict tags"
        v = _parse_verdict(text)
        assert v.status == "unknown"
        assert v.raw_response == text

    def test_malformed_json_inside_tags(self):
        """Malformed JSON inside <verdict> tags → status=unknown."""
        text = '<verdict>{bad}</verdict>'
        v = _parse_verdict(text)
        assert v.status == "unknown"
        assert v.raw_response == text

    def test_non_enum_status_value(self):
        """VerdictStatus is Literal (runtime-unenforced); non-enum values are still accepted as-is."""
        text = '<verdict>{"status":"probably_ok_i_guess","summary":"hmm"}</verdict>'
        v = _parse_verdict(text)
        # Literal is not enforced at runtime — whatever the JSON contains is stored
        assert v.status == "probably_ok_i_guess"
        assert v.summary == "hmm"

    def test_empty_response(self):
        """Empty string → no <verdict> tag → status=unknown."""
        v = _parse_verdict("")
        assert v.status == "unknown"
        assert v.raw_response == ""


class TestRunReviewHandler:
    """run_review(): spawn_subagent integration, truncation, error handling."""

    def test_happy_path(self):
        """Mock spawn_subagent returns valid verdict → result contains [review: pass]."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '[done: 3 turns, 5 tool calls]\n'
                '<verdict>{"status":"pass","summary":"All good","evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review_legacy_str
            result = run_review_legacy_str("f-test", "test feature")
            assert "[review: pass]" in result
            assert "All good" in result

    def test_llm_failure(self):
        """spawn_subagent raises Exception → result contains 'unknown'."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.side_effect = Exception("API error")
            from loom.agent.review import run_review_legacy_str
            result = run_review_legacy_str("f-test", "test feature")
            assert "unknown" in result
            assert "API error" in result

    def test_long_description_truncation(self):
        """feature_description >= 2000 chars → truncated to 2000 + suffix."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"truncation test","evidence":[],"recommendations":[]}</verdict>'
            )
            long_desc = "A" * 2500
            from loom.agent.review import run_review
            run_review("f-trunc", long_desc)
            # Check the prompt passed to spawn_subagent was truncated
            call_kwargs = mock_spawn.call_args[1]
            prompt = call_kwargs.get("description", mock_spawn.call_args[0][0])
            # The feature_description should appear truncated in the prompt
            assert "... (truncated)" in prompt

    def test_scope_hint_optional(self):
        """scope_hint not provided still works (defaults to '')."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"no scope hint","evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review_legacy_str
            result = run_review_legacy_str("f-scope", "test feature")  # no scope_hint
            assert "[review: pass]" in result

    def test_max_turns_15(self):
        """run_review passes max_turns=15 to spawn_subagent."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"pass","summary":"turns check","evidence":[],"recommendations":[]}</verdict>'
            )
            from loom.agent.review import run_review
            run_review("f-turns", "test turns")
            call_kwargs = mock_spawn.call_args[1]
            assert call_kwargs.get("max_turns") == 15

    def test_verdict_string_format(self):
        """Result string starts with [review: {status}] where status comes from the verdict."""
        with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
            mock_spawn.return_value = (
                '<verdict>{"status":"quality_issue","summary":"Needs cleanup","evidence":["a.py:5"],"recommendations":["fix style"]}</verdict>'
            )
            from loom.agent.review import run_review_legacy_str
            result = run_review_legacy_str("f-format", "test format")
            assert "[review: quality_issue]" in result
            assert "Needs cleanup" in result
            assert "a.py:5" in result
            assert "fix style" in result


class TestReviewToolsWhitelist:
    """REVIEW_TOOLS whitelist contains only read-only tools."""

    def test_contains_allowed_tools(self):
        """REVIEW_TOOLS contains read_file, grep, glob, bash."""
        names = [t["name"] for t in REVIEW_TOOLS]
        assert "read_file" in names
        assert "grep" in names
        assert "glob" in names
        assert "bash" in names

    def test_does_not_contain_forbidden_tools(self):
        """REVIEW_TOOLS excludes write/mutation tools."""
        names = [t["name"] for t in REVIEW_TOOLS]
        forbidden = [
            "write_file", "edit_file", "multi_edit", "edit_lines",
            "task", "review", "memory_write", "cold_archive",
        ]
        for tool_name in forbidden:
            assert tool_name not in names, f"{tool_name!r} should NOT be in REVIEW_TOOLS"

    def test_is_tuple(self):
        """REVIEW_TOOLS is a tuple (immutable)."""
        assert isinstance(REVIEW_TOOLS, tuple)


class TestRegistry:
    """TOOL_REGISTRY and SUB_TOOLS integration for the review tool."""

    def test_review_tool_registered(self):
        """'review' tool is registered in TOOL_REGISTRY with is_read_only=True."""
        tool = TOOL_REGISTRY.get("review")
        assert tool is not None
        assert tool.is_read_only is True
        assert tool.name == "review"

    def test_review_not_in_sub_tools(self):
        """'review' is NOT in SUB_TOOLS — prevents R1 recursion (review subagent cannot spawn another review)."""
        sub_names = [t["name"] for t in SUB_TOOLS]
        assert "review" not in sub_names
