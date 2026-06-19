"""``loom audit`` — score an existing harness on the 5 subsystems.

Ported from ``harness-creator/scripts/validate-harness.mjs`` and the
``scoreHarness`` / ``htmlReport`` / ``formatScoreReport`` functions in
``harness-creator/scripts/lib/harness-utils.mjs``. The five subsystems
(Instructions / State / Verification / Scope / Lifecycle) are scored
1-5 each, rolled up to an overall 0-100. The lowest-scoring subsystem
is reported as the candidate bottleneck.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path

SUBSYSTEMS: tuple[str, ...] = (
    "instructions",
    "state",
    "verification",
    "scope",
    "lifecycle",
    "self-test",
)

HARNESS_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "feature_list.json",
    "feature-list.json",
    "progress.md",
    "session-handoff.md",
    "init.sh",
)


@dataclass
class CheckResult:
    pass_: bool
    message: str

    @property
    def passed(self) -> bool:
        return self.pass_


@dataclass
class SubsystemScore:
    name: str
    score: int
    passed: int
    total: int
    checks: list[CheckResult]


@dataclass
class HarnessScore:
    overall: int
    bottleneck: str
    subsystems: dict[str, SubsystemScore]


@dataclass
class HarnessFile:
    path: str
    content: str


def _has_file(by_path: dict[str, str], names: Iterable[str]) -> bool:
    return any(name in by_path for name in names)


def _text_has(text: str, needles: Iterable[str]) -> bool:
    lower = text.lower()
    return any(needle.lower() in lower for needle in needles)


def _json_feature_list_valid(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    features = parsed.get("features")
    if not isinstance(features, list):
        return False
    return all(
        isinstance(f.get("id"), str)
        and isinstance(f.get("name"), str)
        and isinstance(f.get("description"), str)
        and isinstance(f.get("status"), str)
        for f in features
    )


def _file_check(by_path: dict[str, str], names: Iterable[str], message: str) -> CheckResult:
    return CheckResult(pass_=_has_file(by_path, names), message=message)


def _text_check(text: str, needles: Iterable[str], message: str) -> CheckResult:
    return CheckResult(pass_=_text_has(text, needles), message=message)


def _run_self_test(workdir: Path) -> tuple[int, int, str]:
    """Run ``loom eval`` in workdir; return (passed, total, stderr_excerpt)."""
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "loom.cli", "eval", "--fail-under", "0"],
            cwd=workdir, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return 0, 0, "timed out"
    except FileNotFoundError:
        return 0, 0, "python not found"
    if proc.returncode != 0 and not proc.stdout:
        return 0, 0, proc.stderr[:200]
    for line in proc.stdout.splitlines():
        if "Eval results:" in line:
            parts = line.split("/")
            if len(parts) >= 2:
                try:
                    passed = int(parts[0].split()[-1])
                    total = int(parts[1].split()[0])
                    return passed, total, ""
                except (ValueError, IndexError):
                    pass
    return 0, 0, "could not parse Eval results line"


def score_harness(files: list[HarnessFile], *, target: Path, skip_self_test: bool = False) -> HarnessScore:
    by_path = {f.path: f.content for f in files}
    all_text = "\n\n".join(f"{f.path}\n{f.content}" for f in files)
    agents = by_path.get("AGENTS.md") or by_path.get("CLAUDE.md") or ""
    feature_list = by_path.get("feature_list.json") or by_path.get("feature-list.json") or ""
    progress = by_path.get("progress.md") or ""
    init_sh = by_path.get("init.sh") or ""
    handoff = by_path.get("session-handoff.md") or ""

    checks = {
        "instructions": [
            _file_check(by_path, ("AGENTS.md", "CLAUDE.md"), "Agent instruction file exists"),
            _text_check(agents, ("Startup Workflow", "Before writing code"), "Startup workflow documented"),
            _text_check(agents, ("Definition of Done", "done only when"), "Definition of done documented"),
            _text_check(agents, ("Verification Commands", "./init.sh", "test", "verify"),
                        "Verification commands discoverable"),
            _text_check(agents, ("feature_list.json", "progress.md"), "State artifacts routed from instructions"),
        ],
        "state": [
            _file_check(by_path, ("feature_list.json", "feature-list.json"), "Feature tracker exists"),
            CheckResult(pass_=_json_feature_list_valid(feature_list),
                        message="Feature tracker is valid and has feature fields"),
            _file_check(by_path, ("progress.md",), "Progress log exists"),
            _text_check(progress, ("Current State", "What", "Next"), "Progress log supports restart"),
            _text_check((handoff or progress), ("Blockers", "Files", "Next Session"),
                        "Handoff captures blockers/files/next step"),
        ],
        "verification": [
            _file_check(by_path, ("init.sh",), "Verification entrypoint exists"),
            _text_check(init_sh, ("set -e",), "Verification fails fast"),
            _text_check(init_sh + agents,
                        ("test", "pytest", "vitest", "cargo test", "go test", "dotnet test"),
                        "Test command documented"),
            _text_check(init_sh + agents,
                        ("build", "type", "lint", "compile"),
                        "Static/build check documented"),
            _text_check(all_text, ("Evidence", "Verification Evidence", "command and output"),
                        "Verification evidence is recorded"),
        ],
        "scope": [
            _text_check(agents, ("One feature at a time", "one-feature-at-a-time"),
                        "One-feature-at-a-time rule exists"),
            _text_check(feature_list, ("dependencies",), "Feature dependencies are tracked"),
            _text_check(agents + feature_list, ("status",), "Feature status is explicit"),
            _text_check(agents, ("Stay in scope", "scope"), "Scope boundary documented"),
            _text_check(agents, ("Definition of Done",), "Completion gate limits scope closure"),
        ],
        "lifecycle": [
            _file_check(by_path, ("init.sh",), "Startup script exists"),
            _text_check(agents, ("End of Session", "Before ending"), "End-of-session procedure exists"),
            _file_check(by_path, ("session-handoff.md",), "Session handoff template exists"),
            _text_check(progress + handoff,
                        ("Last Updated", "Current Objective", "Recommended Next Step"),
                        "Session restart markers exist"),
            _text_check(agents + init_sh, ("restartable", "clean", "Next steps"),
                        "Clean restart path documented"),
        ],
    }

    subsystems: dict[str, SubsystemScore] = {}
    for name, sub_checks in checks.items():
        passed = sum(1 for c in sub_checks if c.pass_)
        score = max(1, round((passed / len(sub_checks)) * 5))
        subsystems[name] = SubsystemScore(
            name=name,
            score=score,
            passed=passed,
            total=len(sub_checks),
            checks=sub_checks,
        )

    # --- 6th dimension: self-test ---
    if not skip_self_test and files:
        passed_evals, total_evals, err = _run_self_test(target)
        if total_evals > 0:
            self_test_score = max(1, round(passed_evals * 5 / total_evals))
            self_test_checks = [
                CheckResult(pass_=passed_evals > 0, message=f"Eval results: {passed_evals}/{total_evals} passed"),
            ]
            if err:
                self_test_checks.append(CheckResult(pass_=False, message=f"Stderr: {err[:100]}"))
        else:
            self_test_score = 1
            self_test_checks = [CheckResult(pass_=False, message=f"Self-test could not run: {err[:100] if err else 'no evals found'}")]
    else:
        self_test_score = 0
        self_test_checks = [CheckResult(pass_=True, message=f"Self-test N/A{' (skipped by flag)' if skip_self_test else ' — no harness files found'}")]

    subsystems["self-test"] = SubsystemScore(
        name="self-test",
        score=self_test_score,
        passed=sum(1 for c in self_test_checks if c.pass_),
        total=len(self_test_checks),
        checks=self_test_checks,
    )

    total = sum(s.score for s in subsystems.values())
    overall = round((total / (len(SUBSYSTEMS) * 5)) * 100)
    bottleneck = min(subsystems.items(), key=lambda kv: kv[1].score)[0]

    return HarnessScore(overall=overall, bottleneck=bottleneck, subsystems=subsystems)


def load_harness_files(root: Path) -> list[HarnessFile]:
    files: list[HarnessFile] = []
    for name in HARNESS_FILES:
        path = root / name
        if path.is_file():
            files.append(HarnessFile(path=name, content=path.read_text(encoding="utf-8")))
    return files


def format_score_report(result: HarnessScore, root: str = ".") -> str:
    lines = [
        f"Harness validation for {root}",
        f"Overall: {result.overall}/100",
        f"Bottleneck: {result.bottleneck}",
        "",
    ]
    for name in SUBSYSTEMS:
        sub = result.subsystems[name]
        lines.append(f"{name}: {sub.score}/5 ({sub.passed}/{sub.total})")
        for check in sub.checks:
            mark = "PASS" if check.pass_ else "FAIL"
            lines.append(f"  {mark} {check.message}")
        lines.append("")
    return "\n".join(lines)


def html_report(result: HarnessScore, title: str = "Harness Assessment") -> str:
    sections: list[str] = []
    for name in SUBSYSTEMS:
        sub = result.subsystems[name]
        items = "\n".join(
            f'<li class="{"pass" if c.pass_ else "fail"}">'
            f'{"PASS" if c.pass_ else "FAIL"} {html_escape(c.message)}</li>'
            for c in sub.checks
        )
        sections.append(
            f"<section><h2>{html_escape(name)} <span>{sub.score}/5</span></h2>"
            f"<ul>{items}</ul></section>"
        )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{html_escape(title)}</title>\n"
        "  <style>\n"
        "    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 32px; color: #172026; background: #f7f8fa; }\n"
        "    main { max-width: 960px; margin: 0 auto; }\n"
        "    header { margin-bottom: 24px; }\n"
        "    h1 { margin: 0 0 8px; font-size: 32px; }\n"
        "    .summary { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }\n"
        "    .metric { background: white; border: 1px solid #d9dee5; border-radius: 8px; padding: 16px 18px; min-width: 180px; }\n"
        "    .metric strong { display: block; font-size: 28px; margin-top: 4px; }\n"
        "    section { background: white; border: 1px solid #d9dee5; border-radius: 8px; margin: 14px 0; padding: 16px 18px; }\n"
        "    h2 { margin: 0 0 10px; font-size: 20px; display: flex; justify-content: space-between; }\n"
        "    ul { margin: 0; padding-left: 20px; }\n"
        "    li { margin: 6px 0; }\n"
        "    .pass { color: #126c43; }\n"
        "    .fail { color: #a23020; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <main>\n"
        f"    <header><h1>{html_escape(title)}</h1>\n"
        "      <p>Five-subsystem harness validation report.</p>\n"
        '      <div class="summary">\n'
        f'        <div class="metric">Overall<strong>{result.overall}/100</strong></div>\n'
        f'        <div class="metric">Bottleneck<strong>{html_escape(result.bottleneck)}</strong></div>\n'
        "      </div>\n"
        "    </header>\n"
        + "\n".join(sections) + "\n"
        "  </main>\n"
        "</body>\n"
        "</html>\n"
    )


def audit(
    target: Path,
    *,
    min_score: int = 70,
    json_output: bool = False,
    html_output: Path | None = None,
    skip_self_test: bool = False,
) -> HarnessScore:
    target = target.resolve()
    files = load_harness_files(target)
    result = score_harness(files, target=target, skip_self_test=skip_self_test)
    title = f"Harness Assessment: {target.name}"

    if json_output:
        payload = {
            "overall": result.overall,
            "bottleneck": result.bottleneck,
            "subsystems": {
                name: {
                    "score": sub.score,
                    "passed": sub.passed,
                    "total": sub.total,
                    "checks": [
                        {"pass": c.pass_, "message": c.message} for c in sub.checks
                    ],
                }
                for name, sub in result.subsystems.items()
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_score_report(result, str(target)))

    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(html_report(result, title), encoding="utf-8")
        print(f"HTML report written to {html_output}")

    if result.overall < min_score:
        raise SystemExit(1)
    return result
