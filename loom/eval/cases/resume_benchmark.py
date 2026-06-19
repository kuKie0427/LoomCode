from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class ResumeSuccessRateBenchmark(EvalCase):
    name = "resume-success-rate-benchmark"
    description = "Phase 5 §6 metric: 10× kill-and-restart, ≥ 90% must recover. Synthetic (fixture-driven)."

    def run(self) -> EvalResult:
        from loom.eval.benchmarks.resume import run_resume_benchmark

        report = run_resume_benchmark(trials=10)
        per_trial_lines = "\n".join(
            f"    trial {t.trial}: {'PASS' if t.success else 'FAIL'} — {t.reason} {t.detail}"
            for t in report.per_trial
        )
        if not report.passed(threshold_pct=90):
            return EvalResult(
                name=self.name, passed=False,
                detail=(
                    f"{report.successes}/{report.trials} ({report.rate_pct}%) < 90% threshold\n"
                    + per_trial_lines
                ),
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"{report.successes}/{report.trials} ({report.rate_pct}%) ≥ 90% threshold",
        )