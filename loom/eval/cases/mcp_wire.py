"""Harness eval cases for f-mcp-wire-config-manager (Phase PM-1).

Four product-behavior guarantees that lock the M2/M3/M4 contract and
the no-config fail-closed behavior:

  1. mcp-wire-config-default-empty: missing [mcp] section →
     MCPConfig(servers=()). Backward compat: a harness.toml with no MCP
     config must not break the agent.

  2. mcp-wire-double-underscore-prefix: mcp_tool_to_loom_tool returns
     `mcp__server__tool` (M2 regression guard). Mirrors the Anthropic
     prompt-cache namespace format and keeps MCP tool names distinct
     from native tool names.

  3. mcp-wire-duplicate-server-name-rejected: two [mcp.servers.X] entries
     produce a ConfigError (M3 regression guard). The user-facing error
     must name the offending server.

  4. mcp-wire-malformed-schema-skipped: an MCP tool whose inputSchema is
     not a valid object schema is skipped, not silently registered
     (M4 regression guard). The conversion returns None.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class MCPWireConfigDefaultEmpty(EvalCase):
    name = "mcp-wire-config-default-empty"
    description = (
        "load_config() with no [mcp] section in harness.toml returns "
        "MCPConfig(servers=()) — no error, no side effects"
    )

    def run(self) -> EvalResult:
        import shutil
        from pathlib import Path

        from loom.agent.config import MCPConfig, load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("mcp-wire-default-empty")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[permissions]\ndeny_patterns = []\n',
            encoding="utf-8",
        )

        cfg = load_config(Path(wd))
        if not isinstance(cfg.mcp, MCPConfig):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"cfg.mcp is {type(cfg.mcp).__name__}, not MCPConfig",
            )
        if cfg.mcp.servers != ():
            return EvalResult(
                name=self.name, passed=False,
                detail=f"servers = {cfg.mcp.servers!r}, want ()",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="missing [mcp] section → MCPConfig(servers=())",
        )


class MCPWireDoubleUnderscorePrefix(EvalCase):
    name = "mcp-wire-double-underscore-prefix"
    description = (
        "M2 regression guard: mcp_tool_to_loom_tool returns "
        "mcp__server__tool (double underscore), not mcp_server_tool"
    )

    def run(self) -> EvalResult:
        from loom.agent.mcp_client import MCPServer, mcp_tool_to_loom_tool

        server = MCPServer(name="github", command="ignored")
        mcp_tool = {
            "name": "create_issue",
            "description": "Create a GitHub issue",
            "inputSchema": {"type": "object", "properties": {}},
        }
        out = mcp_tool_to_loom_tool(server, mcp_tool)
        if out is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="mcp_tool_to_loom_tool returned None for valid input",
            )
        expected = "mcp__github__create_issue"
        if out["name"] != expected:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"got {out['name']!r}, want {expected!r} (M2 violation)",
            )
        if "_MCP_" in out["name"] or out["name"].count("_") < 4:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"name {out['name']!r} does not have the expected "
                       "mcp__server__tool shape",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="mcp__github__create_issue: double underscore preserved",
        )


class MCPWireDuplicateServerNameRejected(EvalCase):
    name = "mcp-wire-duplicate-server-name-rejected"
    description = (
        "M3 regression guard: two [mcp.servers.X] entries in harness.toml "
        "raise ConfigError naming the offending server"
    )

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import ConfigError, load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("mcp-wire-dup-name")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[mcp.servers.dup]\n'
            'command = "x"\n'
            '[mcp.servers.dup]\n'
            'command = "y"\n',
            encoding="utf-8",
        )

        try:
            load_config(wd)
        except ConfigError as exc:
            msg = str(exc)
            if "dup" in msg:
                return EvalResult(
                    name=self.name, passed=True,
                    detail=f"raised ConfigError naming the dup: {msg[:80]}",
                )
            return EvalResult(
                name=self.name, passed=False,
                detail=f"ConfigError did not name the dup server: {msg[:80]}",
            )
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"wrong exception: {type(exc).__name__}: {exc}",
            )
        return EvalResult(
            name=self.name, passed=False,
            detail="ConfigError not raised on duplicate [mcp.servers.dup]",
        )


class MCPWireMalformedSchemaSkipped(EvalCase):
    name = "mcp-wire-malformed-schema-skipped"
    description = (
        "M4 regression guard: mcp_tool_to_loom_tool returns None when "
        "inputSchema is not a valid object schema (missing type=object, "
        "missing properties, or not a dict). Skipped, not silently "
        "registered with garbage schema."
    )

    def run(self) -> EvalResult:
        from loom.agent.mcp_client import MCPServer, mcp_tool_to_loom_tool

        server = MCPServer(name="x", command="ignored")

        # Case A: inputSchema has wrong type (string instead of object)
        bad_a = {"name": "a", "inputSchema": {"type": "string"}}
        out_a = mcp_tool_to_loom_tool(server, bad_a)
        if out_a is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"type=string schema should be skipped, got {out_a!r}",
            )

        # Case B: inputSchema missing 'properties' field
        bad_b = {"name": "b", "inputSchema": {"type": "object"}}
        out_b = mcp_tool_to_loom_tool(server, bad_b)
        if out_b is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"missing-properties schema should be skipped, got {out_b!r}",
            )

        # Case C: inputSchema is not a dict
        bad_c = {"name": "c", "inputSchema": "not-a-dict"}
        out_c = mcp_tool_to_loom_tool(server, bad_c)
        if out_c is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"non-dict schema should be skipped, got {out_c!r}",
            )

        # Sanity: a well-formed schema DOES return a valid tool dict
        good = {
            "name": "g",
            "inputSchema": {"type": "object", "properties": {}},
        }
        out_g = mcp_tool_to_loom_tool(server, good)
        if out_g is None or out_g["name"] != "mcp__x__g":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"valid schema incorrectly skipped: {out_g!r}",
            )

        return EvalResult(
            name=self.name, passed=True,
            detail="malformed schemas return None; valid schema returns a tool",
        )
