"""``loom`` CLI entry point.

Subcommands:
- ``init``  — generate a minimum 5-file harness in a target directory
- ``audit`` — score an existing harness on the 5 subsystems
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from loguru import logger


def _run_evals_with_baseline(workdir: Path, args) -> tuple[int, list]:
    """Wrap run_evals to support --baseline / --diff-baseline flags."""
    from loom.eval import run_evals as _run_evals
    from loom.eval.baseline import diff_against_baseline, save_baseline

    score = _run_evals(
        workdir=workdir,
        html_output=args.html,
        case_filter=args.case_filter,
        kind=args.kind,
    )
    from loom.eval.runner import run_all
    _, results = run_all(case_filter=args.case_filter, kind=args.kind)
    if args.diff_baseline:
        diff = diff_against_baseline(workdir, results)
        if diff is None:
            print("(no baseline at .minicode/eval-baseline.json — run with --baseline to create one)")
        else:
            print(diff.summary())
            if diff.regressed:
                print(f"\nFAIL: {len(diff.regressed)} case(s) regressed vs baseline")
                score = min(score, 99)
    if args.baseline:
        save_baseline(workdir, results)
        print(f"\nBaseline saved to {workdir / '.minicode' / 'eval-baseline.json'}")
    return score, results

from loom import __version__

_MAX_LOOP_CALL_DEPTH = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loom",
        description="loom — minimal coding agent + harness tooling",
    )
    parser.add_argument("--version", action="version", version=f"loom {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Generate a minimum harness in TARGET")
    init_p.add_argument("target", type=Path, help="Project directory to scaffold")
    init_p.add_argument(
        "--agent-file",
        default="AGENTS.md",
        help="Agent instruction file name (default: AGENTS.md; use CLAUDE.md for Claude projects)",
    )
    init_p.add_argument(
        "--package-manager",
        choices=("npm", "pnpm", "yarn", "bun"),
        help="Override detected package manager (Node stacks only)",
    )
    init_p.add_argument(
        "--commands",
        help='Comma-separated verification commands (overrides stack detection)',
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files. Use with care.",
    )

    audit_p = sub.add_parser("audit", help="Score an existing harness on the 5 subsystems")
    audit_p.add_argument("target", type=Path, nargs="?", default=Path("."), help="Project directory to audit")
    audit_p.add_argument("--json", dest="json_output", action="store_true", help="Emit JSON instead of text")
    audit_p.add_argument("--html", type=Path, default=None, help="Write an HTML report to FILE")
    audit_p.add_argument("--min-score", type=int, default=70, help="Exit non-zero if overall < N (default 70)")
    audit_p.add_argument(
        "--skip-self-test",
        action="store_true",
        help="Skip the self-test (eval runner) dimension",
    )

    run_p = sub.add_parser("run", help="Run the loom coding agent REPL")
    run_p.add_argument("--resume", action="store_true", help="Resume from checkpoint if present")

    tui_p = sub.add_parser("tui", help="Launch the Textual TUI")
    tui_p.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    tui_p.add_argument("--model", default=None, help="Override LLM model")

    trace_p = sub.add_parser("trace", help="Inspect the structured agent trace (.minicode/trace.jsonl)")
    trace_sub = trace_p.add_subparsers(dest="trace_command", required=True)
    trace_show = trace_sub.add_parser("show", help="Show recent trace events")
    trace_show.add_argument("--limit", "-n", type=int, default=20, help="How many recent events")
    trace_show.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")
    trace_path = trace_sub.add_parser("path", help="Print the trace file path")
    trace_path.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")

    eval_p = sub.add_parser("eval", help="Run the eval suite and report")
    eval_p.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")
    eval_p.add_argument("--html", type=Path, default=None, help="Write HTML report to FILE")
    eval_p.add_argument("--fail-under", type=int, default=100, help="Exit non-zero if score < N (default 100)")
    eval_p.add_argument("--benchmark", choices=["resume"], default=None, help="Run a named benchmark instead of the regular eval suite")
    eval_p.add_argument("--filter", dest="case_filter", default=None, help="Substring match against case name/description (case-insensitive). Runs only matching cases — for fast dev cycles.")
    eval_p.add_argument("--kind", choices=["harness", "agent-quality"], default=None, help="Run only one kind of eval. 'harness' = infrastructure mechanics (default mix), 'agent-quality' = end-to-end agent behavior (real LLM calls).")
    eval_p.add_argument("--baseline", action="store_true", help="Save pass/fail status to .minicode/eval-baseline.json as new baseline")
    eval_p.add_argument("--diff-baseline", action="store_true", help="Show added/removed/fixed/regressed cases vs saved baseline")
    eval_sub = eval_p.add_subparsers(dest="eval_command")
    eval_init = eval_sub.add_parser("init", help="Scaffold a starter eval harness in TARGET")
    eval_init.add_argument("workdir", type=Path, default=Path("."), nargs="?", help="Target directory (default: cwd)")
    eval_init.add_argument("--force", action="store_true", help="Overwrite existing files")

    tdd_p = sub.add_parser("tdd", help="Run a single failing test in TDD mode and print the focused prompt")
    tdd_p.add_argument("test_path", type=Path, help="Path to the failing test (e.g. tests/test_foo.py)")
    tdd_p.add_argument("--max-iterations", type=int, default=5, help="Max fix iterations to suggest in the prompt (default 5)")
    tdd_p.add_argument("--timeout", type=float, default=120.0, help="Pytest subprocess timeout in seconds (default 120)")
    tdd_p.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir for pytest invocation")
    tdd_p.add_argument("--run", action="store_true", help="Run pytest once and exit (don't print the prompt)")

    export_p = sub.add_parser("export", help="Export the latest checkpoint session transcript")
    export_p.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")
    export_p.add_argument("--output", "-o", type=Path, required=True, help="Output file path")
    export_p.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    export_p.add_argument("--redact", action="store_true", help="Replace API keys and emails with [REDACTED]")

    return parser


def main(argv: list[str] | None = None) -> int:
    depth = int(os.environ.get("LOOP_CALL_DEPTH", "0"))
    if depth >= _MAX_LOOP_CALL_DEPTH:
        logger.error(
            "LOOP_CALL_DEPTH={} >= {} — refusing to recurse further. "
            "Set LOOP_CALL_DEPTH=0 if this is intentional.",
            depth, _MAX_LOOP_CALL_DEPTH,
        )
        return 1
    os.environ["LOOP_CALL_DEPTH"] = str(depth + 1)

    # only needed for subcommands that need WIP=1 enforcement
    if ("--help" not in (argv or sys.argv) and "--version" not in (argv or sys.argv)):
        from loom.agent.scope import check_wip1
        check_wip1(Path.cwd())

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        from loom.detect import detect_project  # only needed for init subcommand
        from loom.init_cmd import format_results, init  # only needed for init subcommand
        custom: list[str] | None = None
        if args.commands:
            custom = [c.strip() for c in args.commands.split(",") if c.strip()]
        results = init(
            args.target,
            agent_file=args.agent_file,
            package_manager=args.package_manager,
            custom_commands=custom,
            force=args.force,
        )
        project = detect_project(args.target.resolve())
        print(format_results(project, results))
        return 0

    if args.command == "audit":
        from loom.audit_cmd import audit  # only needed for audit subcommand
        try:
            audit(
                args.target,
                min_score=args.min_score,
                json_output=args.json_output,
                html_output=args.html,
                skip_self_test=args.skip_self_test,
            )
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 1
        return 0

    if args.command == "run":
        from loom.agent import run_repl  # only needed for run subcommand
        run_repl(resume=args.resume)
        return 0

    if args.command == "tui":
        from loom.tui.app import AgentTUIApp
        AgentTUIApp(resume=args.resume, model=args.model).run()
        return 0

    if args.command == "trace":
        from loom.agent.trace import Trace, default_path_for  # only needed for trace subcommand
        workdir = args.workdir.resolve()
        if args.trace_command == "path":
            print(default_path_for(workdir))
            return 0
        if args.trace_command == "show":
            tr = Trace(workdir, session_id="cli-show")
            events = tr.recent(n=args.limit)
            if not events:
                print(f"(no trace events at {tr.path})")
                return 0
            for e in events:
                ts = e.get("ts", "?")
                ev = e.get("event", "?")
                sid = e.get("session_id", "?")
                rest = {k: v for k, v in e.items() if k not in ("ts", "event", "session_id")}
                print(f"{ts} {sid} {ev} {rest}")
            return 0

    if args.command == "eval":
        if getattr(args, "eval_command", None) == "init":
            from loom.init_cmd import init
            results = init(args.workdir.resolve(), force=args.force)
            print(f"\nGenerated {sum(1 for r in results if r.status == 'written')} files "
                  f"({sum(1 for r in results if r.status == 'skipped')} skipped)")
            return 0
        if args.benchmark:
            os.environ["LOOM_BENCHMARK"] = args.benchmark
        workdir = args.workdir.resolve()
        try:
            score, results = _run_evals_with_baseline(workdir, args)
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 1
        if score < args.fail_under:
            return 1
        return 0

    if args.command == "tdd":
        from loom.agent.tdd import build_focused_prompt, is_test_file, run_pytest
        if not is_test_file(args.test_path):
            print(
                f"Error: {args.test_path} does not look like a test file. "
                "TDD mode requires a single failing test path.",
                file=__import__("sys").stderr,
            )
            return 1
        run = run_pytest(args.test_path, cwd=args.workdir, timeout=args.timeout)
        if run.passed:
            print(f"Test already passes: {args.test_path} (exit={run.exit_code})")
            return 0
        if args.run:
            print(f"FAIL ({'timeout' if run.timed_out else f'exit={run.exit_code}'})")
            print(run.tail)
            return 1
        prompt = build_focused_prompt(
            args.test_path,
            run.tail,
            max_iterations=args.max_iterations,
        )
        print(f"# Test failed: {args.test_path}")
        print(f"# Exit code: {run.exit_code} (timed_out={run.timed_out})")
        print(f"# Pytest tail ({len(run.tail.splitlines())} lines):")
        print()
        print(prompt)
        return 1

    if args.command == "export":
        from loom.agent.checkpoint import exists, load
        from loom.agent.cost import get_session_cost
        from loom.agent.export import ExportMetadata, to_json, to_markdown, write_export
        workdir = args.workdir.resolve()
        if not exists(workdir):
            print(f"Error: no checkpoint found at {workdir}. Run `loom run` first to create one.", file=__import__("sys").stderr)
            return 1
        ckpt = load(workdir) or {}
        messages = ckpt.get("messages", [])
        meta = ExportMetadata(
            model=ckpt.get("model", "?"),
            session_id=ckpt.get("saved_at", "?"),
            workdir=str(workdir),
            tool_call_count=ckpt.get("tool_call_count", 0),
            started_at=ckpt.get("saved_at", "?"),
            ended_at=ckpt.get("saved_at", "?"),
            session_cost=get_session_cost(),
        )
        if args.format == "json":
            content = to_json(messages, meta)
        else:
            content = to_markdown(messages, meta)
        path = write_export(content, args.output, redact=args.redact)
        print(f"Exported {len(messages)} messages to {path}")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
