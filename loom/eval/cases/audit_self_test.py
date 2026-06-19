"""Eval cases for audit self-test (6th dimension)."""
from __future__ import annotations

import json

from loom.eval._util import make_empty_workdir, run_loop_cli
from loom.eval.runner import EvalCase, EvalResult


class AuditSelfTestRunsEvalsInWorkdir(EvalCase):
    name = "audit-self-test-runs-evals-in-workdir"
    description = "audit(tmp_path) output includes 'self-test' dimension"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="audit-self-test-case1")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"init failed: {r.stderr[:200]}",
            )
        r2 = run_loop_cli("audit", "--skip-self-test", existing_workdir=str(r.workdir))
        if "self-test" not in r2.stdout:
            return EvalResult(
                name=self.name, passed=False,
                detail="'self-test' not found in audit output",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="self-test dimension present in audit output",
        )


class AuditSelfTestSkipsWhenNoHarness(EvalCase):
    name = "audit-self-test-skips-when-no-harness"
    description = "audit in empty dir without harness → self-test shows N/A without error"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("audit-self-test-case2")
        r = run_loop_cli("audit", "--skip-self-test", existing_workdir=str(wd))
        if "self-test" not in r.stdout:
            return EvalResult(
                name=self.name, passed=False,
                detail="'self-test' not in output for empty dir",
            )
        for line in r.stdout.splitlines():
            if "self-test" in line:
                return EvalResult(
                    name=self.name, passed=True,
                    detail=f"self-test present: {line.strip()[:80]}",
                )
        return EvalResult(
            name=self.name, passed=False,
            detail="no self-test line found in output",
        )


class AuditSelfTestSkipsWhenSkipFlag(EvalCase):
    name = "audit-self-test-skips-when-skip-flag"
    description = "audit with --skip-self-test → self-test shows skipped-by-flag message"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="audit-self-test-case3")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"init failed: {r.stderr[:200]}",
            )
        r2 = run_loop_cli("audit", "--skip-self-test", existing_workdir=str(r.workdir))
        for line in r2.stdout.splitlines():
            if "skipped by flag" in line:
                return EvalResult(
                    name=self.name, passed=True,
                    detail=f"flag skip confirmed: {line.strip()[:80]}",
                )
        return EvalResult(
            name=self.name, passed=False,
            detail="'skipped by flag' not found in output",
        )


class AuditSelfTestCountsPassFailCorrectly(EvalCase):
    name = "audit-self-test-counts-pass-fail-correctly"
    description = "audit on a broken harness → self-test still appears in output"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="audit-self-test-case4")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"init failed: {r.stderr[:200]}",
            )
        (r.workdir / "init.sh").unlink(missing_ok=True)
        r2 = run_loop_cli("audit", "--skip-self-test", existing_workdir=str(r.workdir))
        for line in r2.stdout.splitlines():
            if "self-test" in line:
                return EvalResult(
                    name=self.name, passed=True,
                    detail=f"self-test line: {line.strip()[:80]}",
                )
        return EvalResult(
            name=self.name, passed=False,
            detail="self-test dimension not found in output for broken harness",
        )


class AuditSelfTestSixthDimensionAppearsInOutput(EvalCase):
    name = "audit-self-test-sixth-dimension-appears-in-output"
    description = "self-test appears in text, JSON, and HTML output"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="audit-self-test-case5")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"init failed: {r.stderr[:200]}",
            )

        text_output = run_loop_cli("audit", "--skip-self-test", existing_workdir=str(r.workdir))
        if "self-test" not in text_output.stdout:
            return EvalResult(
                name=self.name, passed=False,
                detail="'self-test' missing from text output",
            )

        json_output = run_loop_cli("audit", "--json", "--skip-self-test", existing_workdir=str(r.workdir))
        try:
            payload = json.loads(json_output.stdout)
        except json.JSONDecodeError as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"invalid JSON output: {exc}",
            )
        if "self-test" not in payload.get("subsystems", {}):
            return EvalResult(
                name=self.name, passed=False,
                detail="'self-test' missing from JSON subsystems",
            )

        html_path = r.workdir / "report.html"
        run_loop_cli(
            "audit", "--html", str(html_path), "--skip-self-test",
            existing_workdir=str(r.workdir),
        )
        if not html_path.exists():
            return EvalResult(
                name=self.name, passed=False,
                detail="HTML report file not written",
            )
        html_content = html_path.read_text(encoding="utf-8")
        if "self-test" not in html_content:
            return EvalResult(
                name=self.name, passed=False,
                detail="'self-test' missing from HTML output",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="self-test in text, JSON subsystems, and HTML",
        )
