"""Tests for loom/agent/credential.py.

Verifies:
  - File permissions (0o600 for file, 0o700 for directory)
  - Priority chain: keyring > LOOM_AUTH_CONTENT > env > file
  - set/remove with atomic writes
  - Malformed/missing file tolerance
  - Provider ID normalization
  - all() merge across sources
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from loom.agent.credential import CredentialInfo, CredentialManager


@pytest.fixture(autouse=True)
def _clear_provider_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all provider env vars before each credential test.

    loom.agent.loop imports dotenv.load_dotenv() at module level, which
    loads ANTHROPIC_API_KEY (and other provider env vars) from .env into
    os.environ. This fixture ensures a clean slate for every credential
    test so env-var and file tests don't accidentally pick up the real
    key from the user's .env or shell.
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OLLAMA_API_KEY",
        "OPENROUTER_API_KEY",
        "LOOM_AUTH_CONTENT",
    ):
        monkeypatch.delenv(var, raising=False)

# ---------------------------------------------------------------------------
# File permission tests
# ---------------------------------------------------------------------------


def test_credential_manager_writes_file_0o600(tmp_path: Path) -> None:
    """Auth.json must be chmod 0o600."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    info = CredentialInfo(provider_id="test", kind="api", api_key="key")
    with patch.object(m, "_try_keyring_set"):
        m.set("test", info)
    mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {mode:o}"


def test_credential_manager_dir_chmod_0o700(tmp_path: Path) -> None:
    """Parent dir of auth.json must be chmod 0o700."""
    auth_path = tmp_path / ".loom" / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    info = CredentialInfo(provider_id="test", kind="api", api_key="key")
    with patch.object(m, "_try_keyring_set"):
        m.set("test", info)
    mode = stat.S_IMODE(auth_path.parent.stat().st_mode)
    assert mode == 0o700, f"Expected 0o700, got {mode:o}"


# ---------------------------------------------------------------------------
# Get from env var
# ---------------------------------------------------------------------------


def test_credential_manager_get_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Must read ANTHROPIC_API_KEY from env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    m = CredentialManager(auth_path=tmp_path / "auth.json", use_keyring=False)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-env-key"
    assert cred.source == "env"


# ---------------------------------------------------------------------------
# Get from file
# ---------------------------------------------------------------------------


def test_credential_manager_get_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must read from auth.json when no env var."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "anthropic": {
                    "provider_id": "anthropic",
                    "api_key": "sk-file-key",
                    "kind": "api",
                }
            }
        )
    )
    m = CredentialManager(auth_path=auth_path, use_keyring=False)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-file-key"
    assert cred.source == "file"


# ---------------------------------------------------------------------------
# Priority: env over file
# ---------------------------------------------------------------------------


def test_credential_manager_priority_env_over_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env var must take priority over file."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "anthropic": {
                    "provider_id": "anthropic",
                    "api_key": "sk-file-key",
                    "kind": "api",
                }
            }
        )
    )
    m = CredentialManager(auth_path=auth_path, use_keyring=False)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-env-key"
    assert cred.source == "env"


# ---------------------------------------------------------------------------
# LOOM_AUTH_CONTENT
# ---------------------------------------------------------------------------


def test_credential_manager_loom_auth_content_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOOM_AUTH_CONTENT must be checked before per-provider env var."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setenv(
        "LOOM_AUTH_CONTENT",
        json.dumps({"anthropic": {"api_key": "sk-lac-key"}}),
    )
    m = CredentialManager(auth_path=tmp_path / "auth.json", use_keyring=False)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-lac-key"
    assert cred.source == "loom_auth_content"


# ---------------------------------------------------------------------------
# Keyring
# ---------------------------------------------------------------------------


def test_credential_manager_get_from_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyring must be checked before LOOM_AUTH_CONTENT."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("loom.agent.credential._HAS_KEYRING", True):
        m = CredentialManager(auth_path=tmp_path / "auth.json")
        with patch.object(m, "_try_keyring_get", return_value="sk-keyring-key"):
            cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-keyring-key"
    assert cred.source == "keyring"


# ---------------------------------------------------------------------------
# Set (atomic)
# ---------------------------------------------------------------------------


def test_credential_manager_set_writes_atomic(tmp_path: Path) -> None:
    """set() must write file."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    with patch.object(m, "_try_keyring_set"):
        info = CredentialInfo(provider_id="test", kind="api", api_key="sk-set-key")
        m.set("test", info)
    assert auth_path.exists()
    data = json.loads(auth_path.read_text())
    assert data["test"]["api_key"] == "sk-set-key"


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_credential_manager_remove_cleans_file(tmp_path: Path) -> None:
    """remove() must delete entry from file."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    with patch.object(m, "_try_keyring_set"):
        m.set("test", CredentialInfo(provider_id="test", kind="api", api_key="key"))
    with patch.object(m, "_try_keyring_delete"):
        m.remove("test")
    data = json.loads(auth_path.read_text())
    assert "test" not in data


# ---------------------------------------------------------------------------
# Malformed file
# ---------------------------------------------------------------------------


def test_credential_manager_malformed_file_backs_up(tmp_path: Path) -> None:
    """Malformed JSON must be backed up and empty returned."""
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("{invalid")
    m = CredentialManager(auth_path=auth_path)
    creds = m._load_from_file()
    assert creds == {}
    backups = list(tmp_path.glob("auth.bak.*.json"))
    assert len(backups) >= 1
    assert backups[0].read_text() == "{invalid"


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


def test_credential_manager_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing file must return empty dict."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    creds = m._load_from_file()
    assert creds == {}


# ---------------------------------------------------------------------------
# Provider ID normalization
# ---------------------------------------------------------------------------


def test_credential_manager_provider_id_normalized(tmp_path: Path) -> None:
    """Provider IDs must be lowercased and stripped."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path, use_keyring=False)
    info = CredentialInfo(provider_id="TestProvider", kind="api", api_key="key")
    m.set("  TestProvider  ", info)
    cred = m.get("testprovider")
    assert cred is not None
    assert cred.api_key == "key"


# ---------------------------------------------------------------------------
# all() merge
# ---------------------------------------------------------------------------


def test_credential_manager_all_merges_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """all() must return merged view across all sources."""
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "openai": {
                    "provider_id": "openai",
                    "api_key": "sk-file",
                    "kind": "api",
                }
            }
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    m = CredentialManager(auth_path=auth_path, use_keyring=False)
    all_creds = m.all()
    assert "openai" in all_creds
    assert all_creds["openai"].api_key == "sk-env"  # env beats file
    assert all_creds["openai"].source == "env"
