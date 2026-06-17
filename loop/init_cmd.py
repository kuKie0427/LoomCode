"""``loop init`` — generate a minimum 5-file harness in a target project.

Ported from ``harness-creator/scripts/create-harness.mjs``. The output mirrors
the 5-file set shipped by the reference: ``AGENTS.md`` (or ``CLAUDE.md``) +
``feature_list.json`` + ``feature_list.schema.json`` + ``init.sh`` +
``progress.md`` + ``session-handoff.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from loop.detect import ProjectInfo, detect_project, init_script_content, verification_commands

TEMPLATES_DIR = files("loop").joinpath("templates")


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
    commands = custom_commands if custom_commands else verification_commands(project)
    return init_script_content(commands)


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

    init_path = target / "init.sh"
    if init_path.exists() and not force:
        results.append(FileResult(init_path, "skipped", "exists"))
    else:
        content = _render_init_sh(project, custom_commands)
        _write(init_path, content, executable=True)
        results.append(FileResult(init_path, "written"))

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
