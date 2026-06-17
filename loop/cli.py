"""``loop`` CLI entry point.

Subcommands:
- ``init``  — generate a minimum 5-file harness in a target directory
- ``audit`` — score an existing harness on the 5 subsystems
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from loguru import logger

from loop import __version__
from loop.agent import run_repl
from loop.agent.trace import Trace, default_path_for
from loop.audit_cmd import audit
from loop.detect import detect_project
from loop.init_cmd import format_results, init

_MAX_LOOP_CALL_DEPTH = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loop",
        description="loop — minimal coding agent + harness tooling",
    )
    parser.add_argument("--version", action="version", version=f"loop {__version__}")
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

    run_p = sub.add_parser("run", help="Run the loop coding agent REPL")
    run_p.add_argument("--resume", action="store_true", help="Resume from checkpoint if present")

    trace_p = sub.add_parser("trace", help="Inspect the structured agent trace (.minicode/trace.jsonl)")
    trace_sub = trace_p.add_subparsers(dest="trace_command", required=True)
    trace_show = trace_sub.add_parser("show", help="Show recent trace events")
    trace_show.add_argument("--limit", "-n", type=int, default=20, help="How many recent events")
    trace_show.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")
    trace_sub.add_parser("path", help="Print the trace file path")

    eval_p = sub.add_parser("eval", help="Run the eval suite and report")
    eval_p.add_argument("--workdir", type=Path, default=Path("."), help="Project workdir")
    eval_p.add_argument("--html", type=Path, default=None, help="Write HTML report to FILE")
    eval_p.add_argument("--fail-under", type=int, default=100, help="Exit non-zero if score < N (default 100)")
    eval_p.add_argument("--benchmark", choices=["resume"], default=None, help="Run a named benchmark instead of the regular eval suite")

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

    from loop.agent.scope import check_wip1
    check_wip1(Path.cwd())

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
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
        run_repl(resume=args.resume)
        return 0

    if args.command == "trace":
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
        if args.benchmark:
            os.environ["LOOP_BENCHMARK"] = args.benchmark
        from loop.eval import run_evals
        workdir = args.workdir.resolve()
        try:
            score = run_evals(workdir=workdir, html_output=args.html)
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 1
        if score < args.fail_under:
            return 1
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
