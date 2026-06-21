"""Tests for the agent-quality eval framework.

These tests verify the framework mechanics (workspace creation, diff capture,
subprocess invocation, judging) WITHOUT calling the real LLM. Each test
either uses a mock prompt (no agent run needed) or replaces `run_agent` with
a stub that produces a synthetic outcome.

The full agent-quality baseline is measured separately via
`loom eval --kind agent-quality` and is NOT a unit test (it costs tokens
and time).
"""

from __future__ import annotations

from unittest.mock import patch

from loom.eval.agent_quality import (
    AgentQualityCase,
    AgentRunOutcome,
    capture_diff,
    diff_contains,
    file_contains,
    file_lacks,
    make_agent_workspace,
)
from loom.eval.runner import discover_evals, run_all


def test_make_agent_workspace_creates_isolated_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loom.eval.agent_quality._agent_quality_root",
        lambda: tmp_path,
    )
    wd = make_agent_workspace("test-case", {"hello.py": "print('hi')\n"})
    assert (wd / "hello.py").read_text() == "print('hi')\n"
    assert (wd / ".git").is_dir()


def test_make_agent_workspace_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loom.eval.agent_quality._agent_quality_root",
        lambda: tmp_path,
    )
    wd1 = make_agent_workspace("idem", {"a.py": "x = 1\n"})
    (wd1 / "stale.py").write_text("should be wiped\n")
    wd2 = make_agent_workspace("idem", {"a.py": "x = 1\n"})
    assert wd1 == wd2
    assert not (wd2 / "stale.py").exists()


def test_capture_diff_returns_empty_when_no_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loom.eval.agent_quality._agent_quality_root",
        lambda: tmp_path,
    )
    wd = make_agent_workspace("clean", {"x.py": "1\n"})
    assert capture_diff(wd) == ""


def test_capture_diff_reports_modified_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loom.eval.agent_quality._agent_quality_root",
        lambda: tmp_path,
    )
    wd = make_agent_workspace("modified", {"x.py": "original\n"})
    (wd / "x.py").write_text("changed\n")
    diff = capture_diff(wd)
    assert "-original" in diff
    assert "+changed" in diff


def test_capture_diff_reports_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "loom.eval.agent_quality._agent_quality_root",
        lambda: tmp_path,
    )
    wd = make_agent_workspace("newfile", {"x.py": "1\n"})
    (wd / "added.py").write_text("brand new\n")
    diff = capture_diff(wd)
    assert "added.py" in diff
    assert "brand new" in diff


def _outcome(diff: str = "", files: dict[str, str] | None = None) -> AgentRunOutcome:
    from pathlib import Path
    return AgentRunOutcome(
        returncode=0,
        stdout="",
        stderr="",
        diff=diff,
        workspace=Path("/tmp/fake"),
        elapsed_s=0.1,
        files_after=files or {},
    )


def test_diff_contains_passes_when_all_needles_present():
    ok, detail = diff_contains(_outcome(diff="+ foo\n+ bar\n"), "foo", "bar")
    assert ok
    assert "2" in detail


def test_diff_contains_fails_listing_missing_needles():
    ok, detail = diff_contains(_outcome(diff="+ foo\n"), "foo", "missing")
    assert not ok
    assert "missing" in detail


def test_file_contains_returns_false_for_missing_file():
    ok, detail = file_contains(_outcome(files={}), "absent.py", "anything")
    assert not ok
    assert "absent.py" in detail


def test_file_contains_passes_when_all_needles_present():
    ok, _ = file_contains(_outcome(files={"a.py": "x = 1\ny = 2\n"}), "a.py", "x = 1", "y = 2")
    assert ok


def test_file_lacks_flags_unexpected_content():
    ok, detail = file_lacks(_outcome(files={"a.py": "old_name = 1\n"}), "a.py", "old_name")
    assert not ok
    assert "old_name" in detail


def test_agentqualitycase_rejects_empty_user_prompt():
    class Empty(AgentQualityCase):
        name = "test-empty-prompt"
        description = "should fail validation"
        files = {"x.py": "1\n"}
        user_prompt = ""

        def judge(self, outcome):
            return True, "n/a"

    result = Empty().run()
    assert not result.passed
    assert "empty user_prompt" in result.detail


def test_agentqualitycase_rejects_empty_files():
    class NoFiles(AgentQualityCase):
        name = "test-empty-files"
        description = "should fail validation"
        files = {}
        user_prompt = "do nothing"

        def judge(self, outcome):
            return True, "n/a"

    result = NoFiles().run()
    assert not result.passed
    assert "empty files fixture" in result.detail


def test_agentqualitycase_routes_judge_failure_to_eval_result():
    fake_outcome = _outcome(diff="", files={"x.py": "1\n"})

    class StubCase(AgentQualityCase):
        name = "test-judge-failure"
        description = "judge returns False"
        files = {"x.py": "1\n"}
        user_prompt = "stub"

        def judge(self, outcome):
            return False, "intentional failure for testing"

    with patch("loom.eval.agent_quality.run_agent", return_value=fake_outcome):
        result = StubCase().run()
    assert not result.passed
    assert "intentional failure" in result.detail


def test_agentqualitycase_routes_judge_success_with_meta():
    fake_outcome = _outcome(diff="", files={"x.py": "1\n"})
    fake_outcome.elapsed_s = 1.5
    fake_outcome.returncode = 0

    class StubCase(AgentQualityCase):
        name = "test-judge-success"
        description = "judge returns True"
        files = {"x.py": "1\n"}
        user_prompt = "stub"

        def judge(self, outcome):
            return True, "all good"

    with patch("loom.eval.agent_quality.run_agent", return_value=fake_outcome):
        result = StubCase().run()
    assert result.passed
    assert "all good" in result.detail
    assert "rc=0" in result.detail


def test_agentqualitycase_handles_judge_exception():
    fake_outcome = _outcome(diff="", files={"x.py": "1\n"})

    class CrashyCase(AgentQualityCase):
        name = "test-crashy-judge"
        description = "judge raises"
        files = {"x.py": "1\n"}
        user_prompt = "stub"

        def judge(self, outcome):
            raise RuntimeError("boom")

    with patch("loom.eval.agent_quality.run_agent", return_value=fake_outcome):
        result = CrashyCase().run()
    assert not result.passed
    assert "RuntimeError" in result.detail
    assert "boom" in result.detail


def test_discover_evals_finds_agent_quality_cases():
    cases = discover_evals()
    aq = [c for c in cases if getattr(c, "kind", "harness") == "agent-quality"]
    names = {c.name for c in aq}
    expected = {
        "aq-edit-change-constant",
        "aq-edit-add-function",
        "aq-edit-fix-divide-by-zero",
        "aq-search-rename-symbol",
        "aq-search-add-import",
        "aq-search-delete-dead-code",
        "aq-bug-keyerror",
        "aq-bug-typeerror",
        "aq-tdd-fix-failing-assertion",
        "aq-tdd-implement-missing-function",
    }
    assert expected.issubset(names), f"missing: {expected - names}"
    assert len(aq) == 10


def test_run_all_kind_filter_restricts_to_agent_quality(monkeypatch):
    discovered_kinds: list[str] = []

    def fake_run_one(case_cls):
        discovered_kinds.append(getattr(case_cls, "kind", "harness"))
        from loom.eval.runner import EvalResult
        return EvalResult(name=case_cls.name, passed=True, detail="stub")

    monkeypatch.setattr("loom.eval.runner.run_one", fake_run_one)
    passed, results = run_all(kind="agent-quality")

    assert all(k == "agent-quality" for k in discovered_kinds)
    assert len(results) == 10
    assert passed == 10


def test_run_all_kind_filter_excludes_agent_quality_when_kind_is_harness(monkeypatch):
    discovered_kinds: list[str] = []

    def fake_run_one(case_cls):
        discovered_kinds.append(getattr(case_cls, "kind", "harness"))
        from loom.eval.runner import EvalResult
        return EvalResult(name=case_cls.name, passed=True, detail="stub")

    monkeypatch.setattr("loom.eval.runner.run_one", fake_run_one)
    passed, results = run_all(kind="harness")

    assert all(k == "harness" for k in discovered_kinds)
    assert len(results) >= 200
