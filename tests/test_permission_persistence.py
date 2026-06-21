"""Tests for f-permission-persistence-p2.

Verifies persistent grant storage, expiration, workspace-write
safety carve-out, and revoke behavior.
"""

from __future__ import annotations

import time

from loom.agent.permission_store import (
    WORKSPACE_WRITE_TOOLS,
    PermissionGrant,
    _canonicalize_pattern,
    _key,
    grant,
    is_granted,
    list_grants,
    load_grants,
    revoke,
    save_grants,
)


def test_key_format():
    assert _key("bash", "ls") == "bash::ls"
    assert _key("read_file", "src/main.py") == "read_file::src/main.py"


def test_canonicalize_bash_collapses_whitespace():
    assert _canonicalize_pattern("bash", {"command": "ls   -la   /tmp"}) == "ls -la /tmp"


def test_canonicalize_bash_strips():
    assert _canonicalize_pattern("bash", {"command": "  echo hi  "}) == "echo hi"


def test_canonicalize_read_file_uses_path():
    assert _canonicalize_pattern("read_file", {"path": "src/main.py"}) == "src/main.py"


def test_canonicalize_glob_uses_pattern():
    assert _canonicalize_pattern("glob", {"pattern": "*.py"}) == "*.py"


def test_canonicalize_unknown_tool_json_serializes():
    a = _canonicalize_pattern("future_tool", {"z": 1, "a": 2})
    b = _canonicalize_pattern("future_tool", {"a": 2, "z": 1})
    assert a == b


def test_grant_writes_to_disk(tmp_path):
    g = grant(tmp_path, "bash", {"command": "ls"})
    assert g is not None
    assert g.tool == "bash"
    assert (tmp_path / ".minicode" / "permissions.json").exists()


def test_is_granted_after_grant(tmp_path):
    grant(tmp_path, "bash", {"command": "ls"})
    assert is_granted(tmp_path, "bash", {"command": "ls"})


def test_is_granted_returns_false_for_unknown(tmp_path):
    assert not is_granted(tmp_path, "bash", {"command": "ls"})


def test_workspace_write_tools_never_persisted(tmp_path):
    for tool in WORKSPACE_WRITE_TOOLS:
        g = grant(tmp_path, tool, {"path": "foo.py"})
        assert g is None, f"{tool} should not be grantable"
        assert not is_granted(tmp_path, tool, {"path": "foo.py"})


def test_grant_ttl_days(tmp_path):
    g = grant(tmp_path, "bash", {"command": "ls"}, ttl_days=7)
    assert g.expires_at - g.granted_at == 7 * 86400


def test_expired_grant_does_not_load(tmp_path):
    g = PermissionGrant(
        tool="bash", pattern="ls",
        granted_at=time.time() - 100,
        expires_at=time.time() - 10,
    )
    grants = {_key("bash", "ls"): g}
    save_grants(tmp_path, grants)
    loaded = load_grants(tmp_path)
    assert "bash::ls" not in loaded


def test_active_grant_loads(tmp_path):
    g = PermissionGrant(
        tool="bash", pattern="ls",
        granted_at=time.time(),
        expires_at=time.time() + 1000,
    )
    grants = {_key("bash", "ls"): g}
    save_grants(tmp_path, grants)
    loaded = load_grants(tmp_path)
    assert "bash::ls" in loaded


def test_load_returns_empty_when_no_file(tmp_path):
    assert load_grants(tmp_path) == {}


def test_load_handles_corrupt_file(tmp_path):
    (tmp_path / ".minicode").mkdir()
    (tmp_path / ".minicode" / "permissions.json").write_text("not json", encoding="utf-8")
    assert load_grants(tmp_path) == {}


def test_revoke_removes_grant(tmp_path):
    grant(tmp_path, "bash", {"command": "ls"})
    assert is_granted(tmp_path, "bash", {"command": "ls"})
    assert revoke(tmp_path, "bash", {"command": "ls"})
    assert not is_granted(tmp_path, "bash", {"command": "ls"})


def test_revoke_returns_false_for_unknown(tmp_path):
    assert not revoke(tmp_path, "bash", {"command": "never_granted"})


def test_list_grants_returns_all_active(tmp_path):
    grant(tmp_path, "bash", {"command": "ls"})
    grant(tmp_path, "bash", {"command": "pwd"})
    grants = list_grants(tmp_path)
    assert len(grants) == 2
    patterns = {g.pattern for g in grants}
    assert patterns == {"ls", "pwd"}


def test_persistence_survives_reload(tmp_path):
    grant(tmp_path, "bash", {"command": "ls"})
    grant(tmp_path, "read_file", {"path": "foo.py"})
    assert is_granted(tmp_path, "bash", {"command": "ls"})
    assert is_granted(tmp_path, "read_file", {"path": "foo.py"})
    assert not is_granted(tmp_path, "bash", {"command": "rm"})


def test_grant_overwrites_existing_same_key(tmp_path):
    grant(tmp_path, "bash", {"command": "ls"}, ttl_days=1)
    g1 = list_grants(tmp_path)[0]
    assert g1.expires_at - g1.granted_at == 86400
    time.sleep(0.01)
    grant(tmp_path, "bash", {"command": "ls"}, ttl_days=7)
    g2 = list_grants(tmp_path)[0]
    assert g2.expires_at - g2.granted_at == 7 * 86400
    assert len(list_grants(tmp_path)) == 1
