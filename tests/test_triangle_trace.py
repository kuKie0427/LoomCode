"""Tests for Triangle Protocol trace events (TP-3).

Covers:
- triangle.delegate fires on run_task entry (with feature_card)
- triangle.delta fires on run_task return (with parse result)
- triangle.review fires on run_review return (with attempt counter)
- triangle.feedback fires on _execute_feedback_directive
- C8 fix: run_review (which calls spawn_subagent internally) does NOT
  fire triangle.delegate — only triangle.review
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from loom.agent.review import _REVIEW_ATTEMPT_COUNTER
from loom.agent.triangle_protocol import (
    FeatureCard,
    FeedbackDirective,
    ScopeEnvelope,
)


@pytest.fixture
def trace_workdir(tmp_path: Path) -> Path:
    """tmp_path with an active Trace set as current()."""
    from loom.agent.trace import start, stop
    stop()
    start(tmp_path, session_id="test-triangle-trace")
    yield tmp_path
    stop()


def _read_events(workdir: Path) -> list[dict]:
    """Read all trace events from .minicode/trace.jsonl."""
    p = workdir / ".minicode" / "trace.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _reset_attempt_counter() -> None:
    _REVIEW_ATTEMPT_COUNTER.clear()


def test_trace_constants_exported() -> None:
    """trace.py exports 4 triangle.* event name constants."""
    from loom.agent import trace
    assert trace.TRIANGLE_DELEGATE == "triangle.delegate"
    assert trace.TRIANGLE_DELTA == "triangle.delta"
    assert trace.TRIANGLE_REVIEW == "triangle.review"
    assert trace.TRIANGLE_FEEDBACK == "triangle.feedback"


def test_trace_records_triangle_delegate_on_run_task(trace_workdir: Path) -> None:
    """run_task with feature_card → trace contains triangle.delegate with role=generator."""
    from loom.agent.tools import run_task

    card = FeatureCard(id="f-trace-1", name="trace test", description="t", verification="echo")
    scope = ScopeEnvelope(
        allow_paths=("src/**",),
        deny_paths=(),
        allow_actions=("read", "write"),
        deny_actions=(),
    )

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = "<delta_report>no changes</delta_report>"
        run_task("do the thing", feature_card=card, scope=scope)

    events = _read_events(trace_workdir)
    delegate_events = [e for e in events if e["event"] == "triangle.delegate"]
    assert len(delegate_events) == 1
    e = delegate_events[0]
    assert e["feature_id"] == "f-trace-1"
    assert e["role"] == "generator"
    assert e["scope_paths_allow"] == ["src/**"]
    assert e["scope_paths_deny"] == []
    assert e["max_turns"] == 30


def test_trace_records_triangle_delta_on_run_task_return(trace_workdir: Path) -> None:
    """run_task returns and parse_delta_report succeeds → triangle.delta with parse_success=True."""
    from loom.agent.tools import run_task

    card = FeatureCard(id="f-trace-2", name="delta test", description="t", verification="echo")

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = (
            "<delta_report>\n"
            "status: complete\n"
            "files_modified:\n"
            "  - path: src/x.py\n"
            "    lines_added: 5\n"
            "    lines_deleted: 2\n"
            "    summary: changed x\n"
            "files_created: ()\n"
            "files_considered_but_not_changed: ()\n"
            "verification_run: echo ok\n"
            "verification_result: |\n"
            "  ok\n"
            "escalations: ()\n"
            "</delta_report>"
        )
        run_task("do work", feature_card=card)

    events = _read_events(trace_workdir)
    delta_events = [e for e in events if e["event"] == "triangle.delta"]
    assert len(delta_events) == 1
    e = delta_events[0]
    assert e["feature_id"] == "f-trace-2"
    assert e["status"] == "complete"
    assert e["files_modified_count"] == 1
    assert e["files_created_count"] == 0
    assert e["escalation_count"] == 0
    assert e["parse_success"] is True


def test_trace_records_triangle_delta_parse_failure(trace_workdir: Path) -> None:
    """parse_delta_report returns None → triangle.delta with parse_success=False and counts=0."""
    from loom.agent.tools import run_task

    card = FeatureCard(id="f-trace-3", name="delta fail", description="t", verification="echo")

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        # No <delta_report> in result — parse will return None
        mock_spawn.return_value = "just some plain text, no delta_report block"
        run_task("bad work", feature_card=card)

    events = _read_events(trace_workdir)
    delta_events = [e for e in events if e["event"] == "triangle.delta"]
    assert len(delta_events) == 1
    e = delta_events[0]
    assert e["feature_id"] == "f-trace-3"
    assert e["parse_success"] is False
    assert e["status"] == "unknown"
    assert e["files_modified_count"] == 0
    assert e["files_created_count"] == 0
    assert e["escalation_count"] == 0


def test_trace_records_triangle_review_on_run_review(trace_workdir: Path) -> None:
    """run_review returns → triangle.review with role=reviewer, verdict_status, feedback_action."""
    _reset_attempt_counter()
    from loom.agent.review import run_review

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = (
            '<verdict>{"status":"pass","summary":"ok","evidence":[],"recommendations":[]}</verdict>\n'
            "<feedback_directive>\n"
            "action: [scope_trim]\n"
            "target_files:\n"
            "  - src/x.py\n"
            "target_lines:\n"
            "  - 10-20\n"
            "retry_review: false\n"
            "notes: trim it\n"
            "</feedback_directive>"
        )
        run_review("f-trace-4", "feature desc", scope_hint="")

    events = _read_events(trace_workdir)
    review_events = [e for e in events if e["event"] == "triangle.review"]
    assert len(review_events) == 1
    e = review_events[0]
    assert e["feature_id"] == "f-trace-4"
    assert e["role"] == "reviewer"
    assert e["verdict_status"] == "pass"
    assert e["feedback_action"] == ["scope_trim"]
    assert e["retry_review"] is False
    assert e["attempt"] == 1


def test_trace_review_attempt_counter_increments(trace_workdir: Path) -> None:
    """Calling run_review twice for the same feature → attempt field is 1, then 2."""
    _reset_attempt_counter()
    from loom.agent.review import run_review

    spawn_response = (
        '<verdict>{"status":"pass","summary":"ok","evidence":[],"recommendations":[]}</verdict>'
    )
    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = spawn_response
        run_review("f-trace-5", "feature desc")
        run_review("f-trace-5", "feature desc")

    events = _read_events(trace_workdir)
    review_events = [e for e in events if e["event"] == "triangle.review"]
    assert [e["attempt"] for e in review_events] == [1, 2]


def test_trace_records_triangle_feedback_on_execute(trace_workdir: Path) -> None:
    """_execute_feedback_directive → triangle.feedback with action list + retry_count."""
    _reset_attempt_counter()
    _REVIEW_ATTEMPT_COUNTER["f-trace-6"] = 2
    from loom.agent.loop import _execute_feedback_directive

    fd = FeedbackDirective(
        action=("fix_bug",),
        target_files=("src/x.py", "src/y.py"),
        target_lines=("10-20",),
        retry_review=False,
        notes="fix it",
    )
    _execute_feedback_directive("f-trace-6", fd)

    events = _read_events(trace_workdir)
    fb_events = [e for e in events if e["event"] == "triangle.feedback"]
    assert len(fb_events) == 1
    e = fb_events[0]
    assert e["feature_id"] == "f-trace-6"
    assert e["action"] == ["fix_bug"]
    assert e["target_files"] == ["src/x.py", "src/y.py"]
    assert e["retry_count"] == 2


def test_trace_no_delegate_on_run_review(trace_workdir: Path) -> None:
    """C8 fix: run_review internally calls spawn_subagent but should NOT fire triangle.delegate.

    Only triangle.review should appear. This guards against accidentally moving
    the triangle.delegate record inside spawn_subagent, which would double-fire
    on every run_review (since spawn_subagent is shared between run_task and
    run_review's Reviewer spawn).
    """
    _reset_attempt_counter()
    from loom.agent.review import run_review

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = (
            '<verdict>{"status":"pass","summary":"c8 test","evidence":[],"recommendations":[]}</verdict>'
        )
        run_review("f-trace-7", "c8 guard")

    events = _read_events(trace_workdir)
    delegate_events = [e for e in events if e["event"] == "triangle.delegate"]
    review_events = [e for e in events if e["event"] == "triangle.review"]
    assert len(delegate_events) == 0
    assert len(review_events) == 1
    assert review_events[0]["role"] == "reviewer"


def test_trace_no_events_when_no_feature_card(trace_workdir: Path) -> None:
    """run_task without feature_card → no triangle.* events (legacy mode)."""
    from loom.agent.tools import run_task

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = "plain legacy result"
        run_task("legacy mode")

    events = _read_events(trace_workdir)
    triangle_events = [e for e in events if e["event"].startswith("triangle.")]
    assert triangle_events == []


def test_trace_record_failure_does_not_propagate(trace_workdir: Path) -> None:
    """If trace.record() raises, run_task still succeeds (best-effort semantics)."""
    from loom.agent.tools import run_task

    card = FeatureCard(id="f-trace-8", name="resilience", description="t", verification="echo")

    with patch("loom.agent.tools.spawn_subagent") as mock_spawn, \
         patch("loom.agent.trace.Trace.record", side_effect=RuntimeError("disk full")):
        mock_spawn.return_value = "<delta_report>no changes</delta_report>"
        # Should NOT raise — trace is best-effort
        result = run_task("work", feature_card=card)
    assert "<delta_report>" in result
