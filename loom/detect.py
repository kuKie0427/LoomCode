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


@dataclass(frozen=True)
class VerificationPlan:
    """Two-tier verification commands for init.sh generation.
    
    quick: dev cycle commands (lint + smart test subset, target <10s)
    full:  closeout commands (full test + build, target whatever it takes)
    """
    quick: tuple[str, ...]
    full: tuple[str, ...]
    
    @property
    def all_commands(self) -> list[str]:
        """Back-compat: flat list of full-tier commands for verification_commands()."""
        return list(self.full)


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


def _node_or_generic_commands(project: ProjectInfo, explicit_pm: str | None = None) -> list[str]:
    """Node/generic verification commands — extracted for two-tier plan reuse."""
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


def verification_plan(project: ProjectInfo, explicit_pm: str | None = None) -> VerificationPlan:
    """Return two-tier verification plan for the project's stack."""
    if project.stack == "python":
        quick_list = ["python -m pytest -x -q -m 'not slow and not snapshot' --tb=short"]
        full_list = ["python -m pytest", "python -m compileall ."]

        pyproject = project.root / "pyproject.toml"
        if pyproject.is_file():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "[tool.ruff]" in content or "[tool.ruff." in content:
                    quick_list.insert(0, "ruff check .")
                    full_list.insert(0, "ruff check .")
                if "[tool.mypy]" in content or "[tool.mypy." in content:
                    quick_list.insert(0, "mypy .")
                    full_list.insert(0, "mypy .")
            except (OSError, UnicodeDecodeError):
                pass

        return VerificationPlan(
            quick=tuple(quick_list),
            full=tuple(full_list),
        )
    if project.stack == "go":
        return VerificationPlan(
            quick=("go test -count=1 -run Unit ./...",),
            full=("go test ./...",),
        )
    if project.stack == "rust":
        return VerificationPlan(
            quick=("cargo test --lib --quiet",),
            full=("cargo test",),
        )
    if project.stack == "java-maven":
        return VerificationPlan(quick=("mvn test",), full=("mvn test",))
    if project.stack == "java-gradle":
        return VerificationPlan(quick=("./gradlew test",), full=("./gradlew test",))
    if project.stack == "dotnet":
        return VerificationPlan(quick=("dotnet test --filter Category=Unit",), full=("dotnet test",))

    # generic / node
    if project.package_json is None:
        # generic: skeleton TODO placeholders
        skeleton = [
            'echo "=== STEP 1: tests ==="\n# TODO: replace with your test command (e.g., pytest / go test / cargo test)\n# (replace me)',
            'echo "=== STEP 2: lint ==="\n# TODO: replace with your lint command (e.g., ruff / golangci-lint / clippy)\n# (replace me)',
            'echo "=== STEP 3: build ==="\n# TODO: replace with your build command (e.g., cargo build / go build / tsc)\n# (replace me)',
        ]
        return VerificationPlan(quick=tuple(skeleton), full=tuple(skeleton))
    # node: quick = lint + typecheck (if available), full = install + all scripts
    full = _node_or_generic_commands(project, explicit_pm)
    scripts = project.package_json.get("scripts", {}) or {}
    pm = explicit_pm or project.package_manager or "npm"
    quick_cmds = [c for c in [
        (f"{pm} run lint" if scripts.get("lint") else None),
        (f"{pm} run typecheck" if scripts.get("typecheck") else None),
    ] if isinstance(c, str)]
    if not quick_cmds:
        quick_cmds = full  # no lint/typecheck → fallback to full
    return VerificationPlan(quick=tuple(quick_cmds), full=tuple(full))


def verification_commands(project: ProjectInfo, explicit_pm: str | None = None) -> list[str]:
    """Back-compat: return full-tier commands as flat list. Old callers unaffected."""
    return verification_plan(project, explicit_pm).all_commands


def init_script_content(plan: VerificationPlan) -> str:
    """Generate two-tier init.sh with MODE flag (quick|full)."""
    
    def render_block(commands: tuple[str, ...]) -> str:
        parts: list[str] = []
        for cmd in commands:
            if "\n" in cmd:
                # multi-line command: emit as-is (already contains echo header + TODO)
                parts.append(cmd)
            else:
                parts.append(f'echo "=== {escape(cmd)} ==="\n{cmd}')
        return "\n\n".join(parts)
    
    def escape(value: str) -> str:
        return value.replace('"', '\\"')
    
    return f"""#!/bin/bash
set -e

# init.sh — two-tier verification
# Usage:
#   ./init.sh           # full verification (closeout, before marking feature done)
#   ./init.sh quick     # quick verification (dev cycle, <10s target)
#   MODE=quick ./init.sh
#
# Unix-only. See docs/init-sh.md for customization.

MODE="${{1:-${{MODE:-full}}}}"
cd "$(dirname "$0")"

echo "=== Harness Initialization ($MODE) ==="

case "$MODE" in
  quick)
{render_block(plan.quick)}
    ;;
  full)
{render_block(plan.full)}
    ;;
  *)
    echo "Usage: ./init.sh [quick|full]"
    echo "  quick  — dev cycle (lint + smart test subset, <10s)"
    echo "  full   — closeout (full tests + build, default)"
    exit 1
    ;;
esac

echo "=== Verification Complete ($MODE) ==="
echo ""
echo "Next steps:"
echo "1. Read feature_list.json to see current feature state"
echo "2. Pick ONE unfinished feature to work on"
echo "3. Implement only that feature"
echo "4. Run ./init.sh (full) before claiming done"
"""
