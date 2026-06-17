"""Project stack detection and verification command generation.

Ported from ``harness-creator/scripts/lib/harness-utils.mjs::detectProject``
and ``verificationCommands``. Stays as a pure-functional module: no I/O side
effects beyond reading the target directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIRS = frozenset({
    ".git", "node_modules", "dist", "build", ".next",
    ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "target",
})

STACKS_WITH_MANIFEST: tuple[str, ...] = (
    "python", "go", "rust", "java-maven", "java-gradle", "dotnet", "node",
)


@dataclass
class ProjectInfo:
    root: Path
    stack: str
    package_manager: str = ""
    package_json: dict | None = None
    files: list[str] = field(default_factory=list)

    @property
    def has_manifest(self) -> bool:
        return self.stack in STACKS_WITH_MANIFEST


def _walk(root: Path, max_files: int = 800) -> list[str]:
    results: list[str] = []
    try:
        entries = list(root.iterdir())
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return results

    def visit(directory: Path, prefix: str) -> None:
        if len(results) >= max_files:
            return
        try:
            children = list(directory.iterdir())
        except (PermissionError, NotADirectoryError):
            return
        for child in children:
            if len(results) >= max_files:
                return
            if child.name in IGNORED_DIRS:
                continue
            rel = f"{prefix}{child.name}" if prefix else child.name
            if child.is_dir():
                visit(child, f"{rel}/")
            elif child.is_file():
                results.append(rel)

    for entry in entries:
        if len(results) >= max_files:
            break
        if entry.name in IGNORED_DIRS:
            continue
        if entry.is_dir():
            visit(entry, f"{entry.name}/")
        elif entry.is_file():
            results.append(entry.name)

    results.sort()
    return results


def _has(files: list[str], name: str) -> bool:
    return any(f == name or f.endswith(f"/{name}") for f in files)


def _has_prefix(files: list[str], prefix: str) -> bool:
    return any(f.startswith(prefix) for f in files)


def _has_extension(files: list[str], suffix: str) -> bool:
    return any(f.endswith(suffix) for f in files)


def _load_package_json(root: Path) -> dict | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def detect_package_manager(root: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect_project(root: Path) -> ProjectInfo:
    files = _walk(root)
    package_json = _load_package_json(root)

    if package_json is not None:
        deps = {**package_json.get("dependencies", {}), **package_json.get("devDependencies", {})}
        if "react" in deps or _has_prefix(files, "src/renderer/"):
            stack = "typescript-react"
        elif "typescript" in deps or _has(files, "tsconfig.json"):
            stack = "typescript"
        else:
            stack = "node"
    elif _has(files, "pyproject.toml") or _has(files, "requirements.txt"):
        stack = "python"
    elif _has(files, "go.mod"):
        stack = "go"
    elif _has(files, "Cargo.toml"):
        stack = "rust"
    elif _has(files, "pom.xml"):
        stack = "java-maven"
    elif _has(files, "build.gradle") or _has(files, "build.gradle.kts"):
        stack = "java-gradle"
    elif _has_extension(files, ".csproj") or _has_extension(files, ".sln"):
        stack = "dotnet"
    else:
        stack = "generic"

    package_manager = detect_package_manager(root) if package_json else ""

    return ProjectInfo(
        root=root,
        stack=stack,
        package_manager=package_manager,
        package_json=package_json,
        files=files,
    )


def verification_commands(project: ProjectInfo, explicit_pm: str | None = None) -> list[str]:
    """Return the verification commands for the project's stack.

    These are the commands written into the generated ``init.sh``. For Node
    stacks the package manager is honored; for Python/Go/Rust/Java/.NET the
    canonical tool is used. For ``generic`` stacks a placeholder is returned
    so the user is forced to replace it.
    """
    if project.stack == "python":
        return ["python -m pytest", "python -m compileall ."]

    if project.stack == "go":
        return ["go test ./..."]
    if project.stack == "rust":
        return ["cargo test"]
    if project.stack == "java-maven":
        return ["mvn test"]
    if project.stack == "java-gradle":
        return ["./gradlew test"]
    if project.stack == "dotnet":
        return ["dotnet test"]

    if project.package_json is None:
        return [
            'echo "No package manifest detected; replace this line with your project verification command."'
        ]

    pm = explicit_pm or project.package_manager or "npm"

    def run(script: str) -> str:
        if pm == "npm":
            return f"npm run {script}"
        if pm == "yarn":
            return f"yarn {script}"
        return f"{pm} run {script}"

    scripts = project.package_json.get("scripts", {}) or {}
    install = "npm install" if pm == "npm" else ("yarn install" if pm == "yarn" else f"{pm} install")
    candidates = [
        run("check") if scripts.get("check") else None,
        run("typecheck") if scripts.get("typecheck") else None,
        run("type-check") if scripts.get("type-check") else None,
        run("lint") if scripts.get("lint") else None,
        ("npm test" if pm == "npm" else f"{pm} test") if scripts.get("test") else None,
        run("build") if scripts.get("build") else None,
    ]
    filtered: list[str] = [c for c in candidates if isinstance(c, str)]
    deduped: list[str] = []
    for c in filtered:
        if c not in deduped:
            deduped.append(c)
    return [install, *deduped]


def init_script_content(commands: list[str]) -> str:

    def escape(value: str) -> str:
        return value.replace('"', '\\"')

    body = "\n\n".join(
        f'echo "=== {escape(cmd)} ==="\n{cmd}' for cmd in commands
    )
    return f"""#!/bin/bash
set -e

echo "=== Harness Initialization ==="

{body}

echo "=== Verification Complete ==="
echo ""
echo "Next steps:"
echo "1. Read feature_list.json to see current feature state"
echo "2. Pick ONE unfinished feature to work on"
echo "3. Implement only that feature"
echo "4. Re-run verification before claiming done"
"""
