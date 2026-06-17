"""``loop`` CLI entry point.

Subcommands:
- ``init``  — generate a minimum 5-file harness in a target directory
- ``audit`` — score an existing harness on the 5 subsystems
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loop import __version__
from loop.agent import run_repl
from loop.audit_cmd import audit
from loop.detect import detect_project
from loop.init_cmd import format_results, init


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

    run_p = sub.add_parser("run", help="Run the loop coding agent REPL")
    run_p.add_argument("--resume", action="store_true", help="Resume from checkpoint if present")

    return parser


def main(argv: list[str] | None = None) -> int:
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
            )
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 1
        return 0

    if args.command == "run":
        run_repl(resume=args.resume)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
