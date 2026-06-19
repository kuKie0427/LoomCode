"""Public eval API.

Usage:
    from loom.eval import run_evals
    score, results = run_evals(workdir=Path("."))

The eval cases live under `loom.eval.cases`. They are auto-discovered on
first import via `loom.eval.cases.__init__`. Each case subclasses
`EvalCase` and declares `name` + `description` + `run()`.

Per docs/harness-roadmap.md::Phase 5, the suite should hit ≥ 30 cases.
"""

from loom.eval.runner import (
    EvalCase,
    EvalResult,
    discover_evals,
    format_report,
    html_report,
    run_all,
    run_one,
)


def run_evals(workdir=None, html_output=None) -> int:
    """Run all evals; optionally write an HTML report. Returns pass rate 0-100."""
    import os

    benchmark = os.environ.get("LOOP_BENCHMARK")
    if benchmark == "resume":
        from loom.eval.benchmarks.resume import run_resume_benchmark

        report = run_resume_benchmark(trials=10)
        if report.passed(threshold_pct=90):
            print(f"benchmark: resume {report.successes}/{report.trials} ({report.rate_pct}%)")
            return 100
        print(f"benchmark: resume {report.successes}/{report.trials} ({report.rate_pct}%) < 90% threshold")
        return 1

    passed, results = run_all()
    print(format_report(passed, results))
    if html_output is not None:
        from pathlib import Path as _P
        path = _P(html_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_report(passed, results, title="Loom Eval Report"), encoding="utf-8")
        print(f"\nHTML report written to {path}")
    total = len(results)
    score = int(passed * 100 / total) if total else 0
    return score


__all__ = [
    "EvalCase",
    "EvalResult",
    "discover_evals",
    "format_report",
    "html_report",
    "run_all",
    "run_evals",
    "run_one",
]
