"""Tests for loom.eval.baseline (f-harness-as-product-polish-p4)."""

from __future__ import annotations

import pytest

from loom.eval.baseline import (
    BASELINE_FILENAME,
    diff_against_baseline,
    load_baseline,
    save_baseline,
)
from loom.eval.runner import EvalResult


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "wd"
    d.mkdir()
    return d


def _result(name: str, passed: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, passed=passed, detail=detail, duration_ms=10)


def test_save_baseline_writes_to_minicode(workdir):
    save_baseline(workdir, [_result("a", True), _result("b", False)])
    p = workdir / ".minicode" / BASELINE_FILENAME
    assert p.exists()
    import json
    data = json.loads(p.read_text())
    assert data["cases"]["a"] is True
    assert data["cases"]["b"] is False


def test_save_baseline_creates_minicode_dir(workdir):
    assert not (workdir / ".minicode").exists()
    save_baseline(workdir, [_result("x", True)])
    assert (workdir / ".minicode").is_dir()


def test_load_baseline_returns_dict_when_saved(workdir):
    save_baseline(workdir, [_result("a", True)])
    loaded = load_baseline(workdir)
    assert loaded == {"a": True}


def test_load_baseline_returns_none_when_missing(workdir):
    assert load_baseline(workdir) is None


def test_load_baseline_returns_none_on_corrupted_file(workdir):
    p = workdir / ".minicode" / BASELINE_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{", encoding="utf-8")
    assert load_baseline(workdir) is None


def test_diff_returns_none_when_no_baseline(workdir):
    diff = diff_against_baseline(workdir, [_result("a", True)])
    assert diff is None


def test_diff_detects_regression(workdir):
    save_baseline(workdir, [_result("a", True), _result("b", True)])
    diff = diff_against_baseline(workdir, [_result("a", True), _result("b", False)])
    assert diff is not None
    assert diff.regressed == ["b"]
    assert diff.is_clean is False


def test_diff_detects_fixed(workdir):
    save_baseline(workdir, [_result("a", False)])
    diff = diff_against_baseline(workdir, [_result("a", True)])
    assert diff is not None
    assert diff.fixed == ["a"]


def test_diff_detects_added_cases(workdir):
    save_baseline(workdir, [_result("a", True)])
    diff = diff_against_baseline(workdir, [_result("a", True), _result("b", True)])
    assert diff is not None
    assert diff.added == ["b"]


def test_diff_detects_removed_cases(workdir):
    save_baseline(workdir, [_result("a", True), _result("b", True)])
    diff = diff_against_baseline(workdir, [_result("a", True)])
    assert diff is not None
    assert diff.removed == ["b"]


def test_diff_summary_includes_regressed_names(workdir):
    save_baseline(workdir, [_result("auth-login", True)])
    diff = diff_against_baseline(workdir, [_result("auth-login", False)])
    summary = diff.summary()
    assert "regressed:1" in summary
    assert "auth-login" in summary


def test_baseline_module_public_api():
    from loom.eval import baseline
    for name in ("save_baseline", "load_baseline", "diff_against_baseline", "BaselineDiff", "BASELINE_FILENAME"):
        assert hasattr(baseline, name), f"missing {name}"