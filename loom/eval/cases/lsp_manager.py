"""Harness eval cases for f-lsp-server-lifecycle (Phase PL-2).

Six product-behavior guarantees:
1. lsp-manager-no-config-returns-none — empty config → no server.
2. lsp-manager-missing-command-graceful — bad command in PATH → tool
   handler returns a string, never raises.
3. lsp-manager-shutdown-all-idempotent — shutdown_all() called twice
   does not raise.
4. lsp-manager-session-end-triggers-shutdown — SessionEnd hook
   registered in loop.py actually calls shutdown_all.
5. lsp-read-response-auto-replies-to-server-request — R1+A regression
   guard: window/showMessageRequest from server is auto-replied to.
6. lsp-handler-evicts-dead-server-on-eof — R7 regression guard: a
   handler that hits EOFError evicts the dead server from the cache
   so the next call restarts it.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class LSPManagerNoConfigReturnsNone(EvalCase):
    name = "lsp-manager-no-config-returns-none"
    description = "get_or_start() with empty LSPConfig returns None"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent.config import HarnessConfig
        from loom.agent.lsp_manager import _ACTIVE_SERVERS, _PER_SERVER_LOCKS, get_or_start

        _ACTIVE_SERVERS.clear()
        _PER_SERVER_LOCKS.clear()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = HarnessConfig.from_defaults()
                out = get_or_start(str(Path(d) / "x.py"), cfg)
        finally:
            _ACTIVE_SERVERS.clear()
            _PER_SERVER_LOCKS.clear()
        if out is not None:
            return EvalResult(name=self.name, passed=False,
                              detail=f"expected None, got {out!r}")
        return EvalResult(name=self.name, passed=True,
                          detail="empty config → None")


class LSPManagerMissingCommandGraceful(EvalCase):
    name = "lsp-manager-missing-command-graceful"
    description = (
        "LSP server command not in PATH: the tool handler returns a "
        "fail-closed string instead of raising"
    )

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent import tools
        from loom.agent.lsp_manager import _ACTIVE_SERVERS, _PER_SERVER_LOCKS

        _ACTIVE_SERVERS.clear()
        _PER_SERVER_LOCKS.clear()
        original_workdir = tools.WORKDIR
        try:
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                tools.WORKDIR = wd
                (wd / "harness.toml").write_text(
                    '[lsp.ghost]\n'
                    'command = "definitely-not-an-lsp-server-xyz"\n'
                    'extensions = [".py"]\n',
                    encoding="utf-8",
                )
                # Force _active_config to load the harness.toml we just wrote.
                from loom.agent.config import load_config
                from loom.agent.loop import apply_config
                apply_config(load_config(wd))
                (wd / "x.py").write_text("x = 1\n")
                out = tools.run_lsp_goto_definition(path="x.py", line=0, character=0)
        finally:
            tools.WORKDIR = original_workdir
            _ACTIVE_SERVERS.clear()
            _PER_SERVER_LOCKS.clear()
        if not isinstance(out, str):
            return EvalResult(name=self.name, passed=False,
                              detail=f"non-string return: {type(out).__name__}")
        if "LSP unavailable" not in out:
            return EvalResult(name=self.name, passed=False,
                              detail=f"unexpected return: {out!r}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"fail-closed: returned {out!r}")


class LSPManagerShutdownAllIdempotent(EvalCase):
    name = "lsp-manager-shutdown-all-idempotent"
    description = "shutdown_all() may be called twice in a row without raising"

    def run(self) -> EvalResult:
        from loom.agent.lsp_manager import _ACTIVE_SERVERS, _PER_SERVER_LOCKS, shutdown_all

        _ACTIVE_SERVERS.clear()
        _PER_SERVER_LOCKS.clear()
        try:
            shutdown_all()
            shutdown_all()
        except Exception as exc:
            return EvalResult(name=self.name, passed=False,
                              detail=f"raised {type(exc).__name__}: {exc}")
        return EvalResult(name=self.name, passed=True,
                          detail="shutdown_all() twice in a row, no raise")


class LSPManagerSessionEndTriggersShutdown(EvalCase):
    name = "lsp-manager-session-end-triggers-shutdown"
    description = (
        "Triggering SessionEnd hook with a fake cached server clears the "
        "manager's _ACTIVE_SERVERS dict"
    )

    def run(self) -> EvalResult:
        from unittest.mock import patch

        from loom.agent.hooks import Hooks
        from loom.agent.lsp_client import LSPServer
        from loom.agent.lsp_manager import _ACTIVE_SERVERS, _PER_SERVER_LOCKS

        _ACTIVE_SERVERS.clear()
        _PER_SERVER_LOCKS.clear()
        try:
            fake_server = LSPServer(name="fake", command="ignored")
            _ACTIVE_SERVERS["fake"] = fake_server
            _PER_SERVER_LOCKS["fake"] = __import__("threading").Lock()
            with patch("loom.agent.lsp_client.shutdown", return_value=None):
                Hooks().trigger_hooks("SessionEnd", [], 0)
            if "fake" in _ACTIVE_SERVERS:
                return EvalResult(name=self.name, passed=False,
                                  detail=f"_ACTIVE_SERVERS still has 'fake' after SessionEnd: "
                                         f"{list(_ACTIVE_SERVERS.keys())}")
        finally:
            _ACTIVE_SERVERS.clear()
            _PER_SERVER_LOCKS.clear()
        return EvalResult(name=self.name, passed=True,
                          detail="SessionEnd cleared _ACTIVE_SERVERS")


class LSPReadResponseAutoRepliesToServerRequest(EvalCase):
    name = "lsp-read-response-auto-replies-to-server-request"
    description = (
        "R1+A: _read_response auto-replies to a server-to-client request "
        "(has id AND method) with {result: null} and still returns the "
        "matching response"
    )

    def run(self) -> EvalResult:
        from unittest.mock import patch

        from loom.agent import lsp_client
        from loom.agent.lsp_client import LSPServer, _read_response, start
        from tests.test_lsp_client import FakeLSPProcess

        server_request_id = 77
        real_response_id = 1
        captured: list[dict] = []

        def fake_send(proc, message):
            captured.append(message)

        def responder(req):
            method = req.get("method")
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": req["id"], "result": {"capabilities": {}}}
            if method == "textDocument/definition":
                return {
                    "jsonrpc": "2.0", "id": req["id"],
                    "result": {"uri": "file:///x.py",
                               "range": {"start": {"line": 0, "character": 0},
                                         "end": {"line": 0, "character": 4}}},
                }
            return None

        server = LSPServer(name="pylsp", command="ignored")
        server.process = FakeLSPProcess(responder)  # type: ignore[assignment]
        start(server)

        # Inject a server request by writing one into the fake stdout before
        # the real response is consumed. The cleanest path is to patch
        # _read_message to interleave one request, then the real response.
        real_msgs = iter([
            {"jsonrpc": "2.0", "id": server_request_id,
             "method": "window/showMessageRequest", "params": {}},
            {"jsonrpc": "2.0", "id": real_response_id,
             "result": {"uri": "file:///x.py",
                        "range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 4}}}},
        ])

        def fake_read_message(proc):
            return next(real_msgs)

        with patch.object(lsp_client, "_read_message", side_effect=fake_read_message), \
             patch.object(lsp_client, "_send", side_effect=fake_send):
            result = _read_response(server.process, real_response_id, timeout=2.0)  # type: ignore[arg-type]

        if result.get("id") != real_response_id:
            return EvalResult(name=self.name, passed=False,
                              detail=f"expected id={real_response_id}, got {result!r}")
        auto = [r for r in captured if r.get("id") == server_request_id]
        if len(auto) != 1:
            return EvalResult(name=self.name, passed=False,
                              detail=f"expected 1 auto-reply, got {captured!r}")
        if auto[0].get("result") is not None:
            return EvalResult(name=self.name, passed=False,
                              detail=f"expected result=None, got {auto[0]!r}")
        return EvalResult(name=self.name, passed=True,
                          detail="auto-replied to server request with {result: None}")


class LSPHandlerEvictsDeadServerOnEOF(EvalCase):
    name = "lsp-handler-evicts-dead-server-on-eof"
    description = (
        "R7: when the LSP call raises EOFError, the handler evicts the "
        "server from _ACTIVE_SERVERS so the next call restarts it"
    )

    def run(self) -> EvalResult:
        import shutil
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from loom.agent import loop as agent_loop
        from loom.agent import lsp_client, lsp_manager, tools
        from loom.agent.config import HarnessConfig, LSPConfig, LSPServerSpec
        from loom.agent.lsp_manager import (
            _ACTIVE_SERVERS,
            _PER_SERVER_LOCKS,
            get_or_start,
        )

        _ACTIVE_SERVERS.clear()
        _PER_SERVER_LOCKS.clear()
        original_workdir = tools.WORKDIR
        original_config = agent_loop._active_config
        try:
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                tools.WORKDIR = wd
                (wd / "x.py").write_text("x = 1\n")
                spec = LSPServerSpec(
                    name="pylsp", command="pylsp", extensions=(".py",),
                )
                cfg = HarnessConfig.from_defaults().__class__(
                    policy=HarnessConfig.from_defaults().policy,
                    checkpoint=HarnessConfig.from_defaults().checkpoint,
                    lsp=LSPConfig(servers=(spec,)),
                )
                # The handler reads _active_config from loop.py — push our
                # test config there so the handler actually uses it.
                agent_loop.apply_config(cfg)

                def fake_start(server):
                    server.capabilities = {}
                with patch.object(lsp_manager, "start", side_effect=fake_start), \
                     patch.object(lsp_client, "goto_definition",
                                  side_effect=EOFError("server died")), \
                     patch.object(shutil, "which", return_value="/usr/bin/pylsp"):
                    server = get_or_start(str(wd / "x.py"), cfg)
                    assert server is not None
                    out = tools.run_lsp_goto_definition(
                        path="x.py", line=0, character=0,
                    )
                if "evicted" not in out:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"handler did not evict: {out!r}")
                if "pylsp" in _ACTIVE_SERVERS:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"server still cached: {list(_ACTIVE_SERVERS)}")
                if "pylsp" in _PER_SERVER_LOCKS:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"per-server lock still present: "
                                             f"{list(_PER_SERVER_LOCKS)}")
        finally:
            tools.WORKDIR = original_workdir
            agent_loop.apply_config(original_config)
            _ACTIVE_SERVERS.clear()
            _PER_SERVER_LOCKS.clear()
        return EvalResult(name=self.name, passed=True,
                          detail="EOFError → server evicted from cache")
