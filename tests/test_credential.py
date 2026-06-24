"""Tests for loom/agent/credential.py.

Verifies:
  - File permissions (0o600 for file, 0o700 for directory)
  - 2-layer priority: LOOM_AUTH_CONTENT > auth.json
  - set/remove with atomic writes
  - Malformed/missing file tolerance
  - Provider ID normalization
  - all() merge across sources
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from loom.agent.credential import CredentialInfo, CredentialManager


@pytest.fixture(autouse=True)
def _clear_loom_auth_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOOM_AUTH_CONTENT", raising=False)


# ---------------------------------------------------------------------------
# File permission tests
# ---------------------------------------------------------------------------


def test_credential_manager_writes_file_0o600(tmp_path: Path) -> None:
    """Auth.json must be chmod 0o600."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    info = CredentialInfo(provider_id="test", kind="api", api_key="key")
    m.set("test", info)
    mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {mode:o}"


def test_credential_manager_dir_chmod_0o700(tmp_path: Path) -> None:
    """Parent dir of auth.json must be chmod 0o700."""
    auth_path = tmp_path / ".loom" / "auth.json"
    m = CredentialManager(auth_path=auth_path)
    info = CredentialInfo(provider_id="test", kind="api", api_key="key")
    m.set("test", info)
    mode = stat.S_IMODE(auth_path.parent.stat().st_mode)
    assert mode == 0o700, f"Expected 0o700, got {mode:o}"


# ---------------------------------------------------------------------------
# Get from file (no env override)
# ---------------------------------------------------------------------------


def test_credential_manager_get_from_file(tmp_path: Path) -> None:
    """Must read from auth.json when no LOOM_AUTH_CONTENT."""
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
    m = CredentialManager(auth_path=auth_path)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-file-key"
    assert cred.source == "file"


# ---------------------------------------------------------------------------
# LOOM_AUTH_CONTENT over file
# ---------------------------------------------------------------------------


def test_credential_manager_loom_auth_content_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOOM_AUTH_CONTENT must take priority over auth.json."""
    monkeypatch.setenv(
        "LOOM_AUTH_CONTENT",
        json.dumps({"anthropic": {"api_key": "sk-lac-key"}}),
    )
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
    m = CredentialManager(auth_path=auth_path)
    cred = m.get("anthropic")
    assert cred is not None
    assert cred.api_key == "sk-lac-key"
    assert cred.source == "loom_auth_content"


# ---------------------------------------------------------------------------
# Set (atomic)
# ---------------------------------------------------------------------------


def test_credential_manager_set_writes_atomic(tmp_path: Path) -> None:
    """set() must write file."""
    auth_path = tmp_path / "auth.json"
    m = CredentialManager(auth_path=auth_path)
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
    m.set("test", CredentialInfo(provider_id="test", kind="api", api_key="key"))
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
    m = CredentialManager(auth_path=auth_path)
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
    """all() must overlay LOOM_AUTH_CONTENT on top of file."""
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "anthropic": {
                    "provider_id": "anthropic",
                    "api_key": "sk-file",
                    "kind": "api",
                }
            }
        )
    )
    monkeypatch.setenv(
        "LOOM_AUTH_CONTENT",
        json.dumps({"anthropic": {"api_key": "sk-lac"}}),
    )
    m = CredentialManager(auth_path=auth_path)
    all_creds = m.all()
    assert "anthropic" in all_creds
    assert all_creds["anthropic"].api_key == "sk-lac"  # env beats file
    assert all_creds["anthropic"].source == "loom_auth_content"
