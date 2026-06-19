from __future__ import annotations

import json
from pathlib import Path

from loom.detect import (
    detect_package_manager,
    detect_project,
    init_script_content,
    verification_commands,
)


class TestDetectProject:
    def test_empty_directory_is_generic(self, tmp_path: Path) -> None:
        info = detect_project(tmp_path)
        assert info.stack == "generic"
        assert info.package_json is None
        assert info.package_manager == ""

    def test_python_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        info = detect_project(tmp_path)
        assert info.stack == "python"

    def test_python_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("flask\n")
        info = detect_project(tmp_path)
        assert info.stack == "python"

    def test_go(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module x\n")
        info = detect_project(tmp_path)
        assert info.stack == "go"

    def test_rust(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        info = detect_project(tmp_path)
        assert info.stack == "rust"

    def test_maven(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text("<project/>")
        info = detect_project(tmp_path)
        assert info.stack == "java-maven"

    def test_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").write_text("// gradle")
        info = detect_project(tmp_path)
        assert info.stack == "java-gradle"

    def test_dotnet(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text("<Project/>")
        info = detect_project(tmp_path)
        assert info.stack == "dotnet"

    def test_node_basic(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "x", "version": "0.0.1"}))
        info = detect_project(tmp_path)
        assert info.stack == "node"

    def test_node_typescript(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "x", "devDependencies": {"typescript": "^5.0"}})
        )
        info = detect_project(tmp_path)
        assert info.stack == "typescript"

    def test_node_react(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "x", "dependencies": {"react": "^18.0"}})
        )
        info = detect_project(tmp_path)
        assert info.stack == "typescript-react"

    def test_nested_pyproject_detected(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        info = detect_project(tmp_path)
        assert info.stack == "python"


class TestDetectPackageManager:
    def test_explicit_wins(self, tmp_path: Path) -> None:
        assert detect_package_manager(tmp_path, "pnpm") == "pnpm"

    def test_npm_default(self, tmp_path: Path) -> None:
        assert detect_package_manager(tmp_path) == "npm"

    def test_pnpm(self, tmp_path: Path) -> None:
        (tmp_path / "pnpm-lock.yaml").write_text("")
        assert detect_package_manager(tmp_path) == "pnpm"

    def test_yarn(self, tmp_path: Path) -> None:
        (tmp_path / "yarn.lock").write_text("")
        assert detect_package_manager(tmp_path) == "yarn"

    def test_bun(self, tmp_path: Path) -> None:
        (tmp_path / "bun.lockb").write_bytes(b"")
        assert detect_package_manager(tmp_path) == "bun"


class TestVerificationCommands:
    def test_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("")
        info = detect_project(tmp_path)
        cmds = verification_commands(info)
        assert "python -m pytest" in cmds
        assert "python -m compileall ." in cmds

    def test_go(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("")
        info = detect_project(tmp_path)
        assert verification_commands(info) == ["go test ./..."]

    def test_rust(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("")
        info = detect_project(tmp_path)
        assert verification_commands(info) == ["cargo test"]

    def test_generic_returns_placeholder(self, tmp_path: Path) -> None:
        info = detect_project(tmp_path)
        cmds = verification_commands(info)
        assert len(cmds) == 1
        assert "No package manifest detected" in cmds[0]

    def test_node_uses_detected_pm(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "x",
                    "scripts": {"test": "vitest", "lint": "eslint ."},
                }
            )
        )
        (tmp_path / "pnpm-lock.yaml").write_text("")
        info = detect_project(tmp_path)
        cmds = verification_commands(info)
        assert "pnpm install" in cmds
        assert "pnpm test" in cmds
        assert "pnpm run lint" in cmds
        assert "npm install" not in cmds

    def test_node_with_explicit_pm_override(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "x", "scripts": {"test": "vitest"}})
        )
        info = detect_project(tmp_path)
        cmds = verification_commands(info, explicit_pm="npm")
        assert "npm install" in cmds
        assert "npm test" in cmds


class TestInitScriptContent:
    def test_basic(self) -> None:
        out = init_script_content(["pytest -q"])
        assert "set -e" in out
        assert "=== pytest -q ===" in out
        assert "pytest -q" in out
        assert "./init.sh" not in out
        assert "Harness Initialization" in out

    def test_multiple_commands(self) -> None:
        out = init_script_content(["first_cmd", "second_cmd", "third_cmd"])
        assert "=== first_cmd ===" in out
        assert "=== second_cmd ===" in out
        assert "=== third_cmd ===" in out
        assert out.index("first_cmd") < out.index("second_cmd") < out.index("third_cmd")
