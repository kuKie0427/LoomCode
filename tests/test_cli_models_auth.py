"""Tests for CLI models/auth subcommands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LOOP_CALL_DEPTH", None)
    return env


def _run_cli(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "loom.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )


class TestCliModels:
    def test_lists_all_providers(self) -> None:
        result = _run_cli("models")
        assert result.returncode == 0
        assert "anthropic" in result.stdout
        assert "openai" in result.stdout
        assert "deepseek" in result.stdout

    def test_filtered_shows_one_provider(self) -> None:
        result = _run_cli("models", "anthropic")
        assert result.returncode == 0
        assert "anthropic" in result.stdout
        assert "openai" not in result.stdout

    def test_verbose_shows_pricing(self) -> None:
        result = _run_cli("models", "openai", "--verbose")
        assert result.returncode == 0
        assert "$" in result.stdout

    def test_unknown_provider_shows_message(self) -> None:
        result = _run_cli("models", "nonexistent")
        assert result.returncode == 0
        assert "No provider" in result.stdout


class TestCliAuth:
    def test_login_saves_credential(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "sk-test-key-123")

        from loom import cli as cli_mod

        rc = cli_mod.main(["auth", "login", "testprovider"])
        assert rc == 0
        auth_path = tmp_path / ".loom" / "auth.json"
        assert auth_path.exists()
        data = json.loads(auth_path.read_text())
        assert "testprovider" in data
        assert data["testprovider"]["api_key"] == "sk-test-key-123"

    def test_login_with_base_url(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "sk-custom-url")

        from loom import cli as cli_mod

        rc = cli_mod.main(
            ["auth", "login", "customurl_provider", "--base-url", "https://custom.api.com/v1"]
        )
        assert rc == 0
        auth_path = tmp_path / ".loom" / "auth.json"
        assert auth_path.exists()
        data = json.loads(auth_path.read_text())
        assert data["customurl_provider"]["api_key"] == "sk-custom-url"
        assert data["customurl_provider"]["base_url"] == "https://custom.api.com/v1"

    def test_list_outputs_table(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("LOOP_CALL_DEPTH", raising=False)
        auth_dir = tmp_path / ".loom"
        auth_dir.mkdir(parents=True)
        auth_file = auth_dir / "auth.json"
        auth_file.write_text(
            json.dumps(
                {
                    "listtest": {
                        "provider_id": "listtest",
                        "kind": "api",
                        "api_key": "sk-list-key",
                    },
                },
            )
        )

        from loom import cli as cli_mod

        rc = cli_mod.main(["auth", "list"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "PROVIDER" in captured.out
        assert "SOURCE" in captured.out
        assert "listtest" in captured.out

    def test_logout_removes_entry(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("LOOP_CALL_DEPTH", raising=False)
        auth_dir = tmp_path / ".loom"
        auth_dir.mkdir(parents=True)
        auth_file = auth_dir / "auth.json"
        auth_file.write_text(
            json.dumps(
                {
                    "removeme": {
                        "provider_id": "removeme",
                        "kind": "api",
                        "api_key": "sk-remove-key",
                    },
                },
            )
        )

        from loom import cli as cli_mod

        rc_list = cli_mod.main(["auth", "list"])
        assert rc_list == 0
        captured = capsys.readouterr()
        assert "removeme" in captured.out

        rc_logout = cli_mod.main(["auth", "logout", "removeme"])
        assert rc_logout == 0

        data = json.loads(auth_file.read_text())
        assert "removeme" not in data
