"""``loom init`` — generate a minimum 5-file harness in a target project.

Ported from ``harness-creator/scripts/create-harness.mjs``. The output mirrors
the 5-file set shipped by the reference: ``AGENTS.md`` (or ``CLAUDE.md``) +
``feature_list.json`` + ``feature_list.schema.json`` + ``init.sh`` +
``progress.md`` + ``session-handoff.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from loom.detect import (
    ProjectInfo,
    VerificationPlan,
    detect_project,
    init_script_content,
    verification_commands,
    verification_plan,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = files("loom").joinpath("templates")


@dataclass
class FileResult:
    path: Path
    status: str
    reason: str = ""


def _read_template(name: str) -> str:
    return TEMPLATES_DIR.joinpath(name).read_text(encoding="utf-8")


def _render(template: str, replacements: dict[str, str]) -> str:
    out = template
    for key, value in replacements.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _write(path: Path, content: str, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _render_init_sh(project: ProjectInfo, custom_commands: list[str] | None) -> str:
    if custom_commands:
        plan = VerificationPlan(quick=tuple(custom_commands), full=tuple(custom_commands))
    else:
        plan = verification_plan(project)
    return init_script_content(plan)


def _render_verify_quick_sh(project: ProjectInfo, custom_commands: list[str] | None) -> str:
    if custom_commands:
        static_commands = "\n".join(custom_commands)
        pytest_cmd = custom_commands[0] if custom_commands else "python -m pytest"
    else:
        static_commands = ""
        pytest_cmd = "python -m pytest"

    return f"""#!/bin/bash
# scripts/verify-quick.sh — fast dev-cycle verification.
# Smart test subset from git diff. Skips slow/snapshot markers if configured.
# Usage: scripts/verify-quick.sh [test_file...] [--no-skip-snapshot]
set -e
cd "$(dirname "$0")/.."

SKIP_SNAPSHOT=1
EXPLICIT_TESTS=()
for arg in "$@"; do
    case "$arg" in
        --no-skip-snapshot) SKIP_SNAPSHOT=0 ;;
        -*) ;;
        *) EXPLICIT_TESTS+=("$arg") ;;
    esac
done

echo "=== verify-quick: static checks ==="
{static_commands}

echo ""
echo "=== verify-quick: pytest (smart subset) ==="
MARKER_ARGS=()
if [ "$SKIP_SNAPSHOT" = "1" ]; then
    MARKER_ARGS=(-m "not snapshot")
fi

if [ ${{#EXPLICIT_TESTS[@]}} -gt 0 ]; then
    {pytest_cmd} "${{EXPLICIT_TESTS[@]}}" -q "${{MARKER_ARGS[@]}}"
else
    changed=$(git diff --name-only HEAD 2>/dev/null | grep -E '\\.py$' | sort -u || true)
    if [ -n "$changed" ]; then
        test_files=()
        for f in $changed; do
            base=$(basename "$f" .py)
            if [ -f "tests/test_${{base}}.py" ]; then
                test_files+=("tests/test_${{base}}.py")
            fi
        done
        if [ ${{#test_files[@]}} -gt 0 ]; then
            echo "Scope: ${{test_files[*]}}"
            {pytest_cmd} "${{test_files[@]}}" -q "${{MARKER_ARGS[@]}}"
        else
            echo "No test files inferred — running smoke"
            {pytest_cmd} -q "${{MARKER_ARGS[@]}}"
        fi
    else
        {pytest_cmd} -q "${{MARKER_ARGS[@]}}"
    fi
fi

echo ""
echo "=== verify-quick: done. For full verification run ./init.sh ==="
"""


def _agents_file_replacements(project: ProjectInfo, agent_file: str,
                              custom_commands: list[str] | None) -> dict[str, str]:
    if project.stack == "generic":
        purpose = "Project harness for reliable agent-assisted development."
    else:
        purpose = (
            f"Project harness for reliable agent-assisted development in a "
            f"{project.stack} codebase."
        )
    commands = custom_commands if custom_commands else verification_commands(project)
    bullets = "\n".join(f"- `{c}`" for c in commands)
    return {
        "AGENT_FILE_NAME": agent_file,
        "PROJECT_PURPOSE": purpose,
        "VERIFICATION_COMMANDS": bullets,
        "PRIMARY_VERIFICATION_COMMAND": "./init.sh",
    }


def _maybe_inject_pytest_markers(target: Path, force: bool) -> FileResult | None:
    pyproject = target / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        content = pyproject.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read %s for marker injection", pyproject)
        return None

    # Conservative: skip entirely if section already exists.
    if "[tool.pytest.ini_options]" in content:
        return None

    markers_section = """

[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \\"not slow\\"')",
    "snapshot: visual snapshot tests (deselect with '-m \\"not snapshot\\"')",
    "integration: end-to-end integration tests",
]
"""
    try:
        pyproject.write_text(content + markers_section, encoding="utf-8")
    except OSError:
        logger.warning("Could not write markers to %s", pyproject)
        return None

    return FileResult(pyproject, "written", "markers injected")


def init(
    target: Path,
    *,
    agent_file: str = "AGENTS.md",
    package_manager: str | None = None,
    custom_commands: list[str] | None = None,
    force: bool = False,
) -> list[FileResult]:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    project = detect_project(target)

    results: list[FileResult] = []

    if agent_file.endswith(".md"):
        agents_name = agent_file
    else:
        agents_name = agent_file + ".md"

    replacements = _agents_file_replacements(project, agents_name, custom_commands)

    static_templates = (
        ("agents.md", agents_name, False),
        ("feature-list.json", "feature_list.json", False),
        ("feature-list.schema.json", "feature_list.schema.json", False),
        ("progress.md", "progress.md", False),
        ("session-handoff.md", "session-handoff.md", False),
    )
    for src_name, dst_name, executable in static_templates:
        dst = target / dst_name
        if dst.exists() and not force:
            results.append(FileResult(dst, "skipped", "exists"))
            continue
        content = _render(_read_template(src_name), replacements)
        _write(dst, content, executable)
        results.append(FileResult(dst, "written"))

    workflow_dir = target / ".github" / "workflows"
    workflow_path = workflow_dir / "loom-eval.yml"
    if workflow_path.exists() and not force:
        results.append(FileResult(workflow_path, "skipped", "exists"))
    else:
        workflow_dir.mkdir(parents=True, exist_ok=True)
        workflow_content = _render(_read_template("loom-eval.yml"), {"FAIL_UNDER": "100"})
        _write(workflow_path, workflow_content, False)
        results.append(FileResult(workflow_path, "written"))

    init_path = target / "init.sh"
    if init_path.exists() and not force:
        results.append(FileResult(init_path, "skipped", "exists"))
    else:
        content = _render_init_sh(project, custom_commands)
        _write(init_path, content, executable=True)
        results.append(FileResult(init_path, "written"))

    quick_path = target / "scripts" / "verify-quick.sh"
    if quick_path.exists() and not force:
        results.append(FileResult(quick_path, "skipped", "exists"))
    else:
        quick_content = _render_verify_quick_sh(project, custom_commands)
        _write(quick_path, quick_content, executable=True)
        results.append(FileResult(quick_path, "written"))

    from loom.agent.config import write_default_config
    harness_path = target / "harness.toml"
    if harness_path.exists() and not force:
        results.append(FileResult(harness_path, "skipped", "exists"))
    else:
        write_default_config(target)
        results.append(FileResult(harness_path, "written"))

    marker_result = _maybe_inject_pytest_markers(target, force)
    if marker_result:
        results.append(marker_result)

    return results


def format_results(project: ProjectInfo, results: Iterable[FileResult]) -> str:
    lines = [
        f"Generated harness for {project.root}",
        f"Detected stack: {project.stack}",
        f"Verification commands: {', '.join(verification_commands(project)) or '(none)'}",
        "",
    ]
    for r in results:
        suffix = f" ({r.reason})" if r.reason else ""
        lines.append(f"{r.status.upper():>8s} {r.path}{suffix}")
    return "\n".join(lines)
