"""Harness eval cases for f-lsp-integration-p3."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class LSPClientModuleDefined(EvalCase):
    name = "lsp-client-module-defined"
    description = "loom.agent.lsp_client exposes JSON-RPC LSP client + LSPServer dataclass"

    def run(self) -> EvalResult:
        try:
            import loom.agent.lsp_client as l
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("LSPServer", "LSPError", "start", "shutdown", "goto_definition"):
            if not hasattr(l, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="LSP public API complete")


class LSPClientInitializeHandshake(EvalCase):
    name = "lsp-client-initialize-handshake"
    description = "start() completes initialize + initialized with the LSP server"

    def run(self) -> EvalResult:
        from loom.agent.lsp_client import LSPServer, start
        from tests.test_lsp_client import FakeLSPProcess

        def responder(req):
            if req.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {"definitionProvider": True}}}
            return None

        server = LSPServer(name="pylsp", command="ignored")
        server.process = FakeLSPProcess(responder)  # type: ignore[assignment]
        start(server)
        if not server.capabilities.get("definitionProvider"):
            return EvalResult(name=self.name, passed=False, detail=f"got {server.capabilities}")
        return EvalResult(name=self.name, passed=True, detail="initialize handshake completes + capabilities parsed")


class LSPClientGotoDefinitionReturnsLocation(EvalCase):
    name = "lsp-client-goto-definition-returns-location"
    description = "goto_definition parses a single LSP Location into a dict"

    def run(self) -> EvalResult:
        from loom.agent.lsp_client import LSPServer, goto_definition, start
        from tests.test_lsp_client import FakeLSPProcess

        def responder(req):
            if req.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {}}}
            if req.get("method") == "textDocument/definition":
                return {
                    "jsonrpc": "2.0", "id": req["id"],
                    "result": {"uri": "file:///x.py", "range": {"start": {"line": 5, "character": 0}, "end": {"line": 5, "character": 8}}},
                }
            return None

        server = LSPServer(name="pylsp", command="ignored")
        server.process = FakeLSPProcess(responder)  # type: ignore[assignment]
        start(server)
        locs = goto_definition(server, "/x.py", line=0, character=0)
        if len(locs) != 1 or locs[0]["uri"] != "file:///x.py":
            return EvalResult(name=self.name, passed=False, detail=f"got {locs}")
        return EvalResult(name=self.name, passed=True, detail="single Location parsed correctly")


class LSPClientGotoDefinitionEmpty(EvalCase):
    name = "lsp-client-goto-definition-empty"
    description = "goto_definition returns [] when server responds with null"

    def run(self) -> EvalResult:
        from loom.agent.lsp_client import LSPServer, goto_definition, start
        from tests.test_lsp_client import FakeLSPProcess

        def responder(req):
            if req.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {}}}
            if req.get("method") == "textDocument/definition":
                return {"jsonrpc": "2.0", "id": req["id"], "result": None}
            return None

        server = LSPServer(name="pylsp", command="ignored")
        server.process = FakeLSPProcess(responder)  # type: ignore[assignment]
        start(server)
        locs = goto_definition(server, "/x.py", line=0, character=0)
        if locs != []:
            return EvalResult(name=self.name, passed=False, detail=f"expected [], got {locs}")
        return EvalResult(name=self.name, passed=True, detail="null response returns empty list")