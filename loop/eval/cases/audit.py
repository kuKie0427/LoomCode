from loop.eval._util import make_empty_workdir, run_loop_cli
from loop.eval.runner import EvalCase, EvalResult


def _seed_minimal_harness(workdir) -> None:
    """Populate a workdir with the bare minimum harness so `loop audit` returns >0.

    The 5 file paths must exist with non-trivial content for the audit check.
    """
    (workdir / "AGENTS.md").write_text(
        "# Project\n\n"
        "## Working Rules\n\n"
        "1. WIP=1\n"
        "2. Verification required\n"
        "3. Real evidence only\n"
        "4. Update artifacts\n"
        "5. Stay in scope\n"
        "6. No self-declared passing\n\n"
        "## Definition of Done\n\n"
        "- [ ] Target behavior implemented\n"
        "- [ ] Verification exits 0\n"
        "- [ ] feature_list.json updated\n"
        "- [ ] No unrelated files modified\n",
        encoding="utf-8",
    )
    (workdir / "init.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "uv run ruff check .\n"
        "uv run mypy main.py\n"
        "uv run pytest -v\n",
        encoding="utf-8",
    )
    (workdir / "feature_list.json").write_text(
        '{"features":[{"id":"f1","name":"smoke","description":"d","status":"done",'
        '"verification":"v","evidence":"e","dependencies":[]}]}',
        encoding="utf-8",
    )
    (workdir / "progress.md").write_text(
        "# Progress\n\n## Session 1\n\n- [x] Smoke test passes\n\n## Next\n\n- nothing\n",
        encoding="utf-8",
    )
    (workdir / "session-handoff.md").write_text(
        "# Session Handoff\n\n"
        "## Blockers\n\nNone.\n\n## Files Touched\n\n- feature_list.json\n\n## Next Step\n\nContinue with next feature.\n",
        encoding="utf-8",
    )


class AuditTextMentionsAllSubsystems(EvalCase):
    name = "audit-text-mentions-all-subsystems"
    description = "loop audit text output mentions all 5 subsystems"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("audit-text")
        _seed_minimal_harness(wd)
        r = run_loop_cli("audit", str(wd))
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        text = r.stdout
        for sub in ("instructions", "state", "verification", "scope", "lifecycle"):
            if sub not in text:
                return EvalResult(name=self.name, passed=False, detail=f"missing subsystem: {sub}")
        return EvalResult(name=self.name, passed=True, detail="all 5 subsystems present")


class AuditJsonIsValid(EvalCase):
    name = "audit-json-is-valid"
    description = "loop audit --json produces valid JSON with overall + bottleneck"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("audit-json")
        _seed_minimal_harness(wd)
        r = run_loop_cli("audit", "--json", str(wd))
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        import json
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"not JSON: {exc}")
        if "overall" not in data or "bottleneck" not in data:
            return EvalResult(name=self.name, passed=False, detail="missing overall/bottleneck keys")
        if not isinstance(data["overall"], int):
            return EvalResult(name=self.name, passed=False, detail=f"overall not int: {data['overall']!r}")
        return EvalResult(name=self.name, passed=True, detail=f"overall={data['overall']} bottleneck={data['bottleneck']}")


class AuditHtmlIsValid(EvalCase):
    name = "audit-html-is-valid"
    description = "loop audit --html writes valid HTML containing the 5 subsystems"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("audit-html")
        _seed_minimal_harness(wd)
        report = wd / "harness-assessment.html"
        r = run_loop_cli("audit", "--html", str(report), str(wd))
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        if not report.exists():
            return EvalResult(name=self.name, passed=False, detail=f"HTML report not found at {report}")
        html = report.read_text(encoding="utf-8")
        if "<html" not in html.lower():
            return EvalResult(name=self.name, passed=False, detail="missing <html>")
        for sub in ("instructions", "state", "verification", "scope", "lifecycle"):
            if sub not in html.lower():
                return EvalResult(name=self.name, passed=False, detail=f"missing subsystem: {sub}")
        return EvalResult(name=self.name, passed=True, detail="HTML valid")


class AuditExitsNonZeroWhenBelowMin(EvalCase):
    name = "audit-exits-non-zero-when-below-min"
    description = "loop audit exits non-zero when overall < min-score"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("audit-fail")
        _seed_minimal_harness(wd)
        r = run_loop_cli("audit", "--min-score", "999", str(wd))
        if r.returncode == 0:
            return EvalResult(name=self.name, passed=False, detail="expected non-zero exit, got 0")
        return EvalResult(name=self.name, passed=True, detail=f"exit {r.returncode} as expected")