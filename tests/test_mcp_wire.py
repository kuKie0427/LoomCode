"""Tests for f-mcp-wire-config-manager (Phase PM-1).

Covers the wire-up of MCP into loom's config + tool registry + lazy
resolution surface:

  - MCPConfig.from_defaults() empty (MCP servers = ())
  - _parse_mcp_section: minimal + full parse
  - duplicate name → ConfigError (M3)
  - missing command → ConfigError
  - env field does NOT inherit os.environ (M11)
  - mcp_tool_to_loom_tool uses double-underscore prefix (M2)
  - malformed inputSchema → None (M4)
  - missing inputSchema → default schema dict (not None)
  - get_tools() / get_tool_handlers() lazy: reflect registry state
  - ToolRegistry thread-safe under concurrent register (M15)
  - ToolRegistry.unregister() removes a tool

No real MCP server is spawned; mcp_client.start is mocked via
FakeProcess in tests/test_mcp_client.py where needed, and the manager
tests patch mcp_client.start to a no-op.

Manager-level tests (start_discovery, failure handling, shutdown_all)
live in tests/test_mcp_manager.py to mirror the LSP test split.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loom.agent.config import (
    ConfigError,
    HarnessConfig,
    MCPConfig,
    MCPServerConfig,
    load_config,
)
from loom.agent.mcp_client import MCPError, MCPServer, mcp_tool_to_loom_tool
from loom.agent.tool_registry import Tool
from loom.agent.tools import (
    TOOL_REGISTRY,
    _resync_from_registry,
    get_tool_handlers,
    get_tools,
)

# ── 1. MCPConfig defaults ──────────────────────────────────────────────────


def test_mcp_config_default_empty() -> None:
    """MCPConfig.from_defaults() has no servers."""
    cfg = MCPConfig.from_defaults()
    assert cfg.servers == ()


def test_mcp_field_on_harness_config_defaults_empty() -> None:
    """HarnessConfig.from_defaults().mcp.servers == ()."""
    cfg = HarnessConfig.from_defaults()
    assert isinstance(cfg.mcp, MCPConfig)
    assert cfg.mcp.servers == ()


# ── 2. _parse_mcp_section — minimal + full parse ───────────────────────────


def test_mcp_config_parse_minimal(tmp_path: Path) -> None:
    """[mcp.servers.x] command='y' parses with all other fields defaulted."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.x]\ncommand = "y"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert len(cfg.mcp.servers) == 1
    spec = cfg.mcp.servers[0]
    assert spec.name == "x"
    assert spec.command == "y"
    assert spec.args == ()
    assert spec.env == {}
    assert spec.cwd is None
    assert spec.subagent_access is False


def test_mcp_config_parse_full(tmp_path: Path) -> None:
    """Full fields: args/env/cwd/subagent_access all round-trip."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.github]\n'
        'command = "npx"\n'
        'args = ["-y", "@modelcontextprotocol/server-github"]\n'
        'env = {GITHUB_TOKEN = "ghp_abc"}\n'
        'cwd = "/tmp/gh"\n'
        'subagent_access = true\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    spec = cfg.mcp.servers[0]
    assert spec.name == "github"
    assert spec.command == "npx"
    assert spec.args == ("-y", "@modelcontextprotocol/server-github")
    assert spec.env == {"GITHUB_TOKEN": "ghp_abc"}
    assert spec.cwd == "/tmp/gh"
    assert spec.subagent_access is True


def test_mcp_config_parse_missing_section_uses_defaults(tmp_path: Path) -> None:
    """harness.toml without [mcp] section → MCPConfig(servers=())."""
    (tmp_path / "harness.toml").write_text(
        '[permissions]\ndeny_patterns = []\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.mcp.servers == ()


# ── 3. duplicate server name → ConfigError (M3) ────────────────────────────


def test_mcp_config_duplicate_name_errors(tmp_path: Path) -> None:
    """Two [mcp.servers.s1] entries → ConfigError mentioning the dup name.

    The TOML parser rejects duplicate subtable keys at parse time, which
    load_config wraps as ConfigError. The user-facing error must name the
    offending server so the user can find and fix it.
    """
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.s1]\ncommand = "x"\n'
        '[mcp.servers.s1]\ncommand = "y"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"s1"):
        load_config(tmp_path)


# ── 4. missing command → ConfigError ───────────────────────────────────────


def test_mcp_config_missing_command_errors(tmp_path: Path) -> None:
    """Server entry without `command` → ConfigError naming the field."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.broken]\nargs = ["foo"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"\[mcp\.servers\.broken\]\.command"):
        load_config(tmp_path)


def test_mcp_config_non_string_command_errors(tmp_path: Path) -> None:
    """`command = 42` → ConfigError (int is not a non-empty string)."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.bad]\ncommand = 42\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"\[mcp\.servers\.bad\]\.command"):
        load_config(tmp_path)


def test_mcp_config_env_must_be_string_table(tmp_path: Path) -> None:
    """`env = {FOO = 42}` (non-string value) → ConfigError naming the env field."""
    (tmp_path / "harness.toml").write_text(
        '[mcp.servers.x]\ncommand = "y"\nenv = {FOO = 42}\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"\[mcp\.servers\.x\]\.env"):
        load_config(tmp_path)


# ── 5. env field does NOT inherit os.environ (M11) ─────────────────────────


def test_mcp_config_env_not_inheriting_os_environ() -> None:
    """MCPServerConfig.env holds only what the user declared (M11)."""
    os.environ["LOOM_CANARY_PM1"] = "must-not-leak"
    try:
        spec = MCPServerConfig(
            name="x", command="y", env={"GITHUB_TOKEN": "ghp_abc"},
        )
        assert "LOOM_CANARY_PM1" not in spec.env, (
            "M11 violation: MCPServerConfig.env should hold ONLY explicit "
            "declarations, not inherit the loom process environment"
        )
        assert spec.env == {"GITHUB_TOKEN": "ghp_abc"}
    finally:
        del os.environ["LOOM_CANARY_PM1"]


def test_mcp_client_start_does_not_inherit_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """start() Popen call uses env=server.env only (no os.environ merge)."""
    import subprocess

    import loom.agent.mcp_client as mc
    captured: dict = {}

    class _FakeProc:
        stdin = None
        stdout = None
        stderr = None
        def terminate(self) -> None: ...
        def wait(self, timeout=None) -> int: return 0
        def kill(self) -> None: ...

    def _fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        # Return a process that fakes the handshake by raising — start()
        # will catch the EOFError and re-raise as MCPError, but we only
        # need the Popen kwargs to verify env= behavior.
        p = _FakeProc()
        p.stdin = None
        # Make stdout.read(1) raise EOFError to break out of _read_message.
        class _EOFReader:
            def read(self, n: int = -1) -> bytes:
                raise EOFError("fake eof")
        p.stdout = _EOFReader()
        return p

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setenv("LOOM_CANARY_PM1_POPEN", "must-not-leak")
    try:
        server = MCPServer(
            name="fs", command="x", env={"GITHUB_TOKEN": "ghp_abc"},
        )
        with pytest.raises((MCPError, EOFError)):  # start() raises MCPError; EOFError variant possible
            mc.start(server)
        assert "env" in captured, "Popen not called?"
        env = captured["env"]
        assert env == {"GITHUB_TOKEN": "ghp_abc"}, (
            f"M11 violation: Popen env should be ONLY server.env, got {env!r}"
        )
        assert "LOOM_CANARY_PM1_POPEN" not in env
    finally:
        monkeypatch.delenv("LOOM_CANARY_PM1_POPEN", raising=False)


# ── 6. double-underscore prefix (M2) ──────────────────────────────────────


def test_mcp_tool_to_loom_tool_double_underscore_prefix() -> None:
    """Tool name uses mcp__server__tool (double underscore), M2."""
    server = MCPServer(name="github", command="ignored")
    mcp_tool = {
        "name": "create_issue",
        "description": "Create a GitHub issue",
        "inputSchema": {"type": "object", "properties": {}},
    }
    out = mcp_tool_to_loom_tool(server, mcp_tool)
    assert out["name"] == "mcp__github__create_issue", (
        f"M2: expected double-underscore prefix, got {out['name']!r}"
    )


# ── 7. malformed inputSchema returns None (M4) ────────────────────────────


def test_mcp_tool_to_loom_tool_malformed_schema_returns_none() -> None:
    """inputSchema missing 'type=object' or 'properties' → None (M4)."""
    server = MCPServer(name="x", command="ignored")
    # Missing 'properties' field → invalid object schema
    mcp_tool = {
        "name": "t",
        "description": "T",
        "inputSchema": {"type": "object"},  # no 'properties'
    }
    assert mcp_tool_to_loom_tool(server, mcp_tool) is None
    # Wrong top-level type
    mcp_tool2 = {
        "name": "t2",
        "description": "T2",
        "inputSchema": {"type": "string"},
    }
    assert mcp_tool_to_loom_tool(server, mcp_tool2) is None
    # Not a dict at all
    mcp_tool3 = {"name": "t3", "inputSchema": "not-a-dict"}
    assert mcp_tool_to_loom_tool(server, mcp_tool3) is None


def test_mcp_tool_to_loom_tool_missing_schema_returns_default() -> None:
    """No inputSchema key at all → defaults to valid object schema (not None)."""
    server = MCPServer(name="x", command="ignored")
    out = mcp_tool_to_loom_tool(server, {"name": "t"})
    assert out is not None
    assert out["input_schema"] == {"type": "object", "properties": {}}


# ── 8. get_tools / get_tool_handlers are lazy ──────────────────────────────


def test_get_tools_lazy_reflects_registry() -> None:
    """After TOOL_REGISTRY.register, get_tools() includes the new tool (M14)."""
    initial = len(get_tools())
    test_tool = Tool(
        name="pm1_test_lazy",
        description="lazy test tool",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: "stub",
        is_read_only=True,
        is_concurrent_safe=True,
        enabled=True,
    )
    try:
        TOOL_REGISTRY.register(test_tool)
        _resync_from_registry()
        names = [t["name"] for t in get_tools()]
        assert "pm1_test_lazy" in names, (
            f"M14 violation: get_tools() did not reflect new registration. "
            f"Got {names!r}"
        )
        assert len(get_tools()) == initial + 1
    finally:
        TOOL_REGISTRY.unregister("pm1_test_lazy")
        _resync_from_registry()


def test_get_tool_handlers_lazy_reflects_registry() -> None:
    """After TOOL_REGISTRY.register, get_tool_handlers() includes the new tool."""
    test_tool = Tool(
        name="pm1_test_handler_lazy",
        description="handler lazy test",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: "handler-stub",
        is_read_only=True,
        is_concurrent_safe=True,
        enabled=True,
    )
    try:
        TOOL_REGISTRY.register(test_tool)
        _resync_from_registry()
        handlers = get_tool_handlers()
        assert "pm1_test_handler_lazy" in handlers
        assert handlers["pm1_test_handler_lazy"](arg="anything") == "handler-stub"
    finally:
        TOOL_REGISTRY.unregister("pm1_test_handler_lazy")
        _resync_from_registry()


# ── 9. ToolRegistry thread-safety (M15) ────────────────────────────────────


def test_tool_registry_register_is_thread_safe() -> None:
    """10 threads concurrently register distinct-named tools; all succeed (M15)."""
    N = 10
    errors: list[Exception] = []

    def _reg(i: int) -> None:
        try:
            TOOL_REGISTRY.register(Tool(
                name=f"pm1_thread_{i}",
                description=f"thread {i}",
                input_schema={"type": "object", "properties": {}},
                handler=lambda **kw: f"t{i}",
                enabled=True,
            ))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=N) as ex:
        futures = [ex.submit(_reg, i) for i in range(N)]
        for f in futures:
            f.result()
    try:
        assert not errors, f"concurrent register raised: {errors}"
        names = set(TOOL_REGISTRY.names())
        for i in range(N):
            assert f"pm1_thread_{i}" in names, (
                f"missing pm1_thread_{i} after concurrent register"
            )
    finally:
        for i in range(N):
            TOOL_REGISTRY.unregister(f"pm1_thread_{i}")


# ── 10. unregister works (M15 / crash recovery) ────────────────────────────


def test_tool_registry_unregister_removes_tool() -> None:
    """register → unregister → not in registry. Re-register works after."""
    test_tool = Tool(
        name="pm1_unreg",
        description="unregister test",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: "x",
        enabled=True,
    )
    try:
        TOOL_REGISTRY.register(test_tool)
        assert TOOL_REGISTRY.get("pm1_unreg") is not None
        TOOL_REGISTRY.unregister("pm1_unreg")
        assert TOOL_REGISTRY.get("pm1_unreg") is None
        # re-register after unregister must succeed (no stale state)
        TOOL_REGISTRY.register(test_tool)
        assert TOOL_REGISTRY.get("pm1_unreg") is not None
    finally:
        TOOL_REGISTRY.unregister("pm1_unreg")


def test_tool_registry_unregister_missing_is_silent() -> None:
    """unregister() on a non-existent name is a no-op (no exception)."""
    TOOL_REGISTRY.unregister("pm1_definitely_not_registered_xyz")
