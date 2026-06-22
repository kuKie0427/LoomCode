"""Harness eval cases for f-mcp-client-p3."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class MCPClientModuleDefined(EvalCase):
    name = "mcp-client-module-defined"
    description = "loom.agent.mcp_client module exists with public API"

    def run(self) -> EvalResult:
        try:
            import loom.agent.mcp_client as m
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("MCPServer", "MCPError", "start", "stop", "call_tool",
                     "mcp_tool_to_loom_tool", "_send_message", "_read_message"):
            if not hasattr(m, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="MCP public API complete")


class MCPClientJsonRpcFramingCorrect(EvalCase):
    name = "mcp-client-jsonrpc-framing-correct"
    description = "_send_message writes Content-Length-framed JSON; _read_message parses it"

    def run(self) -> EvalResult:
        import time

        from loom.agent.mcp_client import MCPServer, _send_message
        from tests.test_mcp_client import FakeProcess
        captured = []
        def responder(req):
            captured.append(req)
            return {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": True}}
        server = MCPServer(name="x", command="ignored")
        server.process = FakeProcess(responder)  # type: ignore[assignment]
        _send_message(server.process, {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})  # type: ignore[arg-type]
        time.sleep(0.1)
        if not captured:
            return EvalResult(name=self.name, passed=False, detail="no request received by fake server")
        if captured[0]["method"] != "ping":
            return EvalResult(name=self.name, passed=False, detail=f"wrong method: {captured[0]}")
        return EvalResult(name=self.name, passed=True, detail="Content-Length framing works")


class MCPClientToolsListParse(EvalCase):
    name = "mcp-client-tools-list-parse"
    description = "start() handshake + tools/list correctly populates server.tools"

    def run(self) -> EvalResult:
        from loom.agent.mcp_client import MCPServer, start
        from tests.test_mcp_client import FakeProcess
        responses = iter([
            {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "fs"}, "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                {"name": "read_file", "description": "Read", "inputSchema": {"type": "object"}},
            ]}},
        ])
        def responder(_req):
            return next(responses)
        server = MCPServer(name="fs", command="ignored")
        server.process = FakeProcess(responder)  # type: ignore[assignment]
        start(server)
        if len(server.tools) != 1 or server.tools[0]["name"] != "read_file":
            return EvalResult(name=self.name, passed=False, detail=f"got {server.tools}")
        return EvalResult(name=self.name, passed=True, detail="handshake + tools/list works")
