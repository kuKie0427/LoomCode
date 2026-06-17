from __future__ import annotations

import json
from pathlib import Path

import pytest

from loop.audit_cmd import (
    HarnessFile,
    audit,
    format_score_report,
    html_report,
    load_harness_files,
    score_harness,
)
from loop.init_cmd import init


def _make_minimal_harness(root: Path) -> None:
    init(root, custom_commands=["pytest -q"])


def _scored_text(root: Path) -> str:
    files = load_harness_files(root)
    result = score_harness(files, target=root, skip_self_test=True)
    return format_score_report(result, str(root))


class TestLoadHarnessFiles:
    def test_loads_existing_files(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# AGENTS\n")
        (tmp_path / "init.sh").write_text("#!/bin/bash\n")
        files = load_harness_files(tmp_path)
        assert {f.path for f in files} == {"AGENTS.md", "init.sh"}

    def test_skips_missing(self, tmp_path: Path) -> None:
        files = load_harness_files(tmp_path)
        assert files == []


class TestScoreHarness:
    def test_minimal_harness_scores_high(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        assert result.overall >= 70
        core = {n: s for n, s in result.subsystems.items() if n != "self-test"}
        assert all(s.passed >= 3 for s in core.values())

    def test_empty_harness_scores_low(self, tmp_path: Path) -> None:
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        assert result.overall < 70
        for name, sub in result.subsystems.items():
            if name == "self-test":
                continue
            assert sub.passed == 0
            assert sub.score == 1

    def test_six_subsystems(self, tmp_path: Path) -> None:
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        assert set(result.subsystems) == {
            "instructions", "state", "verification", "scope", "lifecycle", "self-test",
        }

    def test_bottleneck_is_lowest_subsystem(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text(
            "# AGENTS\n\nDefinition of Done\nDone only when X\n"
            "One feature at a time\nStay in scope\n"
        )
        (tmp_path / "init.sh").write_text("#!/bin/bash\nset -e\npytest\n")
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        assert result.bottleneck in result.subsystems

    def test_overall_math(self) -> None:
        files = [
            HarnessFile(path="AGENTS.md", content="Definition of Done\nOne feature at a time\nStay in scope\nEnd of Session\n"),
            HarnessFile(path="feature_list.json", content=json.dumps({"features": [{"id": "f1", "name": "n", "description": "d", "status": "not-started", "dependencies": []}]})),
            HarnessFile(path="progress.md", content="Current State\nWhat\nNext\nLast Updated\nBlockers\nFiles\n"),
            HarnessFile(path="session-handoff.md", content="Current Objective\nRecommended Next Step\n"),
            HarnessFile(path="init.sh", content="#!/bin/bash\nset -e\npytest\n"),
        ]
        result = score_harness(files, target=Path("."), skip_self_test=True)
        assert 0 <= result.overall <= 100


class TestFormatScoreReport:
    def test_contains_overall(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        out = _scored_text(tmp_path)
        assert "Harness validation" in out
        assert "Overall:" in out
        assert "Bottleneck:" in out

    def test_contains_subsystems(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        out = _scored_text(tmp_path)
        for sub in ("instructions", "state", "verification", "scope", "lifecycle", "self-test"):
            assert sub in out


class TestHtmlReport:
    def test_well_formed_html(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        html = html_report(result, "Test")
        assert html.startswith("<!doctype html>")
        assert "</html>" in html
        assert "Test" in html
        for sub in ("instructions", "state", "verification", "scope", "lifecycle", "self-test"):
            assert sub in html

    def test_contains_overall_metric(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        result = score_harness(load_harness_files(tmp_path), target=tmp_path, skip_self_test=True)
        html = html_report(result)
        assert f"{result.overall}/100" in html
        assert result.bottleneck in html


class TestAuditEntry:
    def test_audit_text_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _make_minimal_harness(tmp_path)
        result = audit(tmp_path, min_score=0, skip_self_test=True)
        assert result.overall >= 70
        out = capsys.readouterr().out
        assert "Overall:" in out

    def test_audit_text_includes_self_test(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _make_minimal_harness(tmp_path)
        audit(tmp_path, min_score=0, skip_self_test=True)
        out = capsys.readouterr().out
        assert "self-test" in out

    def test_audit_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _make_minimal_harness(tmp_path)
        audit(tmp_path, min_score=0, json_output=True, skip_self_test=True)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert "overall" in payload
        assert "subsystems" in payload
        assert "self-test" in payload["subsystems"]
        assert payload["overall"] >= 70

    def test_audit_writes_html(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        html_path = tmp_path / "report.html"
        audit(tmp_path, min_score=0, html_output=html_path, skip_self_test=True)
        assert html_path.exists()
        content = html_path.read_text()
        assert content.startswith("<!doctype html>")
        assert "instructions" in content

    def test_audit_exits_1_when_below_min(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            audit(tmp_path, min_score=99, skip_self_test=True)
        assert exc_info.value.code == 1

    def test_audit_exits_0_when_at_or_above_min(self, tmp_path: Path) -> None:
        _make_minimal_harness(tmp_path)
        result = audit(tmp_path, min_score=70, skip_self_test=True)
        assert result.overall >= 70
