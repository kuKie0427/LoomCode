"""Shared utilities for eval cases: workdir creation + CLI runner."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

EXPECTED_HARNESS_FILES = (
    "AGENTS.md",
    "init.sh",
    "feature_list.json",
    "progress.md",
    "session-handoff.md",
    "harness.toml",
)


def _root_tmp() -> Path:
    """A pytest-independent tmpdir root. Tests use a single shared dir to avoid
    cleanup races; each call returns a unique subdir."""
    root = Path(tempfile.gettempdir()) / "loop-eval"
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_empty_workdir(name: str) -> Path:
    path = _root_tmp() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_workdir_with_file(name: str, filename: str, content: str) -> Path:
    wd = make_empty_workdir(name)
    (wd / filename).write_text(content, encoding="utf-8")
    return wd


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    workdir: Path | None


def run_loop_cli(*args: str, target_name: str | None = None,
                 existing_workdir: str | None = None,
                 setup: str | None = None) -> CliResult:
    """Invoke `python -m loop.cli ...` in a tempdir (or existing).

    Args:
        *args: CLI args after the subcommand (or full args).
        target_name: Subdir name to scaffold (when running init). Appended as positional.
        existing_workdir: Path to use as-is (skips creating a new tmpdir).
        setup: If set, a stub file with this name is pre-created in the workdir.

    Returns: CliResult with stdout/stderr/returncode.
    """
    workdir: Path
    if existing_workdir is not None:
        workdir = Path(existing_workdir)
    else:
        if target_name is not None:
            workdir = make_empty_workdir(target_name)
        else:
            workdir = make_empty_workdir("loop-eval-generic")

    if setup:
        (workdir / setup).parent.mkdir(parents=True, exist_ok=True)
        (workdir / setup).write_text("", encoding="utf-8")

    cmd_args = list(args)
    if target_name is not None:
        cmd_args.append(str(workdir.resolve()))
    full_args = [sys.executable, "-m", "loop.cli", *cmd_args]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        full_args,
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return CliResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        workdir=workdir,
    )
