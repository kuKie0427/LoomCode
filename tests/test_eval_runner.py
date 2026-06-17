from __future__ import annotations

from loop.eval.runner import (
    EvalCase,
    EvalResult,
    discover_evals,
    format_report,
    html_report,
    run_one,
)


class _Pass(EvalCase):
    name = "pass-case"
    description = "always passes"

    def run(self) -> EvalResult:
        return EvalResult(name=self.name, passed=True, detail="ok")


class _Fail(EvalCase):
    name = "fail-case"
    description = "always fails"

    def run(self) -> EvalResult:
        return EvalResult(name=self.name, passed=False, detail="nope")


class _Raises(EvalCase):
    name = "raises-case"
    description = "raises in run()"

    def run(self) -> EvalResult:
        raise RuntimeError("boom")


class _SetupFails(EvalCase):
    name = "setup-fail"
    description = "setup raises"

    def setup(self) -> None:
        raise ValueError("setup boom")

    def run(self) -> EvalResult:
        return EvalResult(name=self.name, passed=True)


class _TeardownFails(EvalCase):
    name = "teardown-fail"
    description = "teardown raises after run"

    def run(self) -> EvalResult:
        return EvalResult(name=self.name, passed=True, detail="ran")

    def teardown(self) -> None:
        raise OSError("teardown boom")


def test_discover_evals_includes_known_cases():
    cases = discover_evals()
    names = {c.name for c in cases}
    assert "init-smoke-empty-dir" in names
    assert "audit-text-mentions-all-subsystems" in names
    assert "bash-deny-list-blocks-rm-rf" in names


def test_run_one_pass_returns_passed():
    result = run_one(_Pass)
    assert result.passed is True
    assert result.detail == "ok"
    assert result.duration_ms >= 0


def test_run_one_fail_returns_failed():
    result = run_one(_Fail)
    assert result.passed is False
    assert "nope" in result.detail


def test_run_one_catches_exception():
    result = run_one(_Raises)
    assert result.passed is False
    assert "RuntimeError" in result.detail
    assert "boom" in result.detail


def test_run_one_setup_exception_does_not_crash_runner():
    result = run_one(_SetupFails)
    assert result.passed is False


def test_run_one_teardown_exception_swallowed():
    result = run_one(_TeardownFails)
    assert result.passed is True
    assert result.detail == "ran"


def test_format_report_counts_pass_fail():
    results = [
        EvalResult(name="a", passed=True, duration_ms=10),
        EvalResult(name="b", passed=False, detail="bad", duration_ms=20),
    ]
    text = format_report(1, results)
    assert "1/2 passed" in text
    assert "[PASS] a" in text
    assert "[FAIL] b" in text
    assert "bad" in text


def test_html_report_contains_all_results():
    results = [
        EvalResult(name="alpha", passed=True, duration_ms=5),
        EvalResult(name="beta", passed=False, detail="oh no", duration_ms=8),
    ]
    html = html_report(1, results, title="Smoke")
    assert "<html" in html.lower()
    assert "Smoke" in html
    assert "alpha" in html
    assert "beta" in html
    assert "1/2" in html
    assert "oh no" in html
    assert "li class=\"fail\"" in html or "li class='fail'" in html


def test_html_report_escapes_angle_brackets_in_detail():
    results = [
        EvalResult(name="x", passed=False, detail="<script>alert(1)</script>", duration_ms=0),
    ]
    html = html_report(0, results)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html