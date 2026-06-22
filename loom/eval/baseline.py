"""Minimal baseline diff for f-harness-as-product-polish-p4.

Saves the pass/fail status of each eval case to `.minicode/eval-baseline.json`.
On subsequent runs, compares current results to baseline and reports
added/removed/fixed/regressed cases.

Scope (deliberately small): one baseline file, per-case name -> passed boolean,
text-only diff report. NOT in scope: per-case cost/latency diffing (the harness
case mix doesn't have meaningful cost), interactive bisect, multi-baseline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

BASELINE_FILENAME = "eval-baseline.json"


@dataclass
class BaselineDiff:
    added: list[str]
    removed: list[str]
    fixed: list[str]
    regressed: list[str]

    @property
    def is_clean(self) -> bool:
        return not self.regressed

    def summary(self) -> str:
        lines = [f"baseline diff: +{len(self.added)} -{len(self.removed)} "
                 f"fixed:{len(self.fixed)} regressed:{len(self.regressed)}"]
        if self.regressed:
            lines.append("  REGRESSED:")
            for n in self.regressed:
                lines.append(f"    - {n}")
        if self.fixed:
            lines.append("  FIXED:")
            for n in self.fixed:
                lines.append(f"    + {n}")
        if self.added:
            lines.append("  ADDED:")
            for n in self.added:
                lines.append(f"    + {n}")
        if self.removed:
            lines.append("  REMOVED:")
            for n in self.removed:
                lines.append(f"    - {n}")
        return "\n".join(lines)


def _baseline_path(workdir: Path) -> Path:
    return workdir / ".minicode" / BASELINE_FILENAME


def save_baseline(workdir: Path, results: list) -> None:
    """Persist current pass/fail status per case name to .minicode/eval-baseline.json."""
    path = _baseline_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "cases": {r.name: bool(r.passed) for r in results},
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_baseline(workdir: Path) -> dict[str, bool] | None:
    """Read previous baseline, or return None if not yet saved."""
    path = _baseline_path(workdir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("cases", {})


def diff_against_baseline(workdir: Path, results: list) -> BaselineDiff | None:
    """Compare current results to saved baseline. None if no baseline."""
    baseline = load_baseline(workdir)
    if baseline is None:
        return None
    current = {r.name: bool(r.passed) for r in results}
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    fixed = sorted(n for n in current if n in baseline and baseline[n] is False and current[n] is True)
    regressed = sorted(n for n in current if n in baseline and baseline[n] is True and current[n] is False)
    return BaselineDiff(added=added, removed=removed, fixed=fixed, regressed=regressed)