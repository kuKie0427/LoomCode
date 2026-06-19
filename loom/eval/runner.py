"""Eval runner: discover + execute EvalCase subclasses, format report."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar


@dataclass
class EvalResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: int = 0


class EvalCase:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def run(self) -> EvalResult:
        raise NotImplementedError


def _walk_subclasses(cls: type) -> list[type]:
    seen: set[type] = set()
    out: list[type] = []

    def walk(klass: type) -> None:
        for sub in klass.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            out.append(sub)
            walk(sub)

    walk(cls)
    return out


def discover_evals() -> list[type[EvalCase]]:
    import loom.eval.cases  # noqa: F401 — side effect: register all cases

    return _walk_subclasses(EvalCase)


def run_one(case: type[EvalCase]) -> EvalResult:
    instance = case()
    t0 = time.monotonic()
    case_name = getattr(case, "name", case.__name__)
    try:
        instance.setup()
    except Exception as exc:
        result = EvalResult(
            name=case_name,
            passed=False,
            detail=f"setup raised {type(exc).__name__}: {exc}"[:300],
        )
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result
    try:
        result = instance.run()
    except Exception as exc:
        result = EvalResult(
            name=case_name,
            passed=False,
            detail=f"raised {type(exc).__name__}: {exc}"[:300],
        )
    finally:
        try:
            instance.teardown()
        except Exception:
            pass
    result.duration_ms = int((time.monotonic() - t0) * 1000)
    return result


def run_all() -> tuple[int, list[EvalResult]]:
    cases = discover_evals()
    results = [run_one(c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    return passed, results


def format_report(passed: int, results: list[EvalResult]) -> str:
    total = len(results)
    lines = [
        f"Eval results: {passed}/{total} passed",
        "",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        line = f"  [{mark}] {r.name} ({r.duration_ms}ms)"
        if r.detail:
            line += f" — {r.detail[:120]}"
        lines.append(line)
    return "\n".join(lines)


def html_report(passed: int, results: list[EvalResult], title: str = "Eval Report") -> str:
    total = len(results)
    rows: list[str] = []
    for r in results:
        cls = "pass" if r.passed else "fail"
        detail = r.detail[:200].replace("<", "&lt;").replace(">", "&gt;")
        rows.append(
            f'<li class="{cls}"><b>{"PASS" if r.passed else "FAIL"}</b> '
            f"{r.name} ({r.duration_ms}ms)"
            + (f" — <code>{detail}</code>" if detail else "")
            + "</li>"
        )
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        f"  <title>{title}</title>\n"
        "  <style>\n"
        "    body { font-family: -apple-system, sans-serif; margin: 32px; background: #f7f8fa; color: #172026; }\n"
        "    main { max-width: 960px; margin: 0 auto; }\n"
        "    .summary { display: flex; gap: 16px; margin: 20px 0; }\n"
        "    .metric { background: white; border: 1px solid #d9dee5; border-radius: 8px; padding: 16px 18px; }\n"
        "    .metric strong { display: block; font-size: 28px; margin-top: 4px; }\n"
        "    ul { background: white; border: 1px solid #d9dee5; border-radius: 8px; padding: 16px 32px; list-style: none; }\n"
        "    li { margin: 6px 0; padding: 4px 8px; border-radius: 4px; }\n"
        "    li.pass { color: #126c43; }\n"
        "    li.fail { color: #a23020; background: #fff0ee; }\n"
        "    code { font-family: monospace; font-size: 12px; color: #555; }\n"
        "  </style>\n</head>\n<body>\n<main>\n"
        f"  <h1>{title}</h1>\n"
        "  <div class=\"summary\">\n"
        f"    <div class=\"metric\">Passed<strong>{passed}/{total}</strong></div>\n"
        f"    <div class=\"metric\">Score<strong>{int(passed * 100 / total) if total else 0}%</strong></div>\n"
        "  </div>\n"
        "  <ul>\n"
        + "\n".join(rows) + "\n"
        "  </ul>\n"
        "</main>\n</body>\n</html>\n"
    )
