"""Harness eval cases for f-mcp-handler-safety (Phase PM-3, M6/M7/M12).

Three product-behavior guarantees that lock the four PM-3 output-shape
mitigations in ``mcp_manager._make_mcp_handler``:

  1. **mcp-handler-flattens-content-list** (M12): the handler returns a
     single human-readable string when the MCP server returns the
     canonical ``content: [{type, text/image/resource}]`` payload.
     Image blocks become bracketed placeholders; resource blocks
     become ``[MCP: resource <uri>]``. Pre-PM-3, ``json.dumps`` returned
     a Python-list literal the LLM could not parse.

  2. **mcp-handler-truncates-oversized-output** (M7): a 60KB text
     response is capped at 50KB + a footer with the overflow count,
     and a ``mcp_output_truncated`` event is written to the trace.
     This prevents a misbehaving server (e.g. dumping a 200MB log)
     from blowing up the agent's context window.

  3. **mcp-handler-crash-visible-warning** (M6): when ``call_tool``
     raises, the handler emits a visible ``logger.warning``, evicts
     the server from ``_ACTIVE_SERVERS`` + ``_PER_SERVER_LOCKS``, and
     unregisters every ``mcp__<server>__*`` tool from
     ``TOOL_REGISTRY`` so the next tool_use fails fast instead of
     hanging on a dead stdio pipe.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult


def _install_fake_server(server_name: str = "fs"):
    """Inject a synthetic MCPServer into mcp_manager cache.

    Returns the server. Caller is responsible for cleanup via
    ``_cleanup_fake_server(server_name)``.
    """
    from loom.agent import mcp_manager as mm
    from loom.agent.mcp_client import MCPServer

    server = MCPServer(name=server_name, command="ignored")
    mm._ACTIVE_SERVERS[server_name] = server
    mm._PER_SERVER_LOCKS[server_name] = threading.Lock()
    return server


def _cleanup_fake_server(server_name: str) -> None:
    """Remove the fake server + any tools registered under its prefix."""
    from loom.agent import mcp_manager as mm
    from loom.agent.tools import TOOL_REGISTRY

    mm._ACTIVE_SERVERS.pop(server_name, None)
    mm._PER_SERVER_LOCKS.pop(server_name, None)
    for n in list(TOOL_REGISTRY.names()):
        if n.startswith(f"mcp__{server_name}__"):
            try:
                TOOL_REGISTRY.unregister(n)
            except Exception:
                pass


class MCPHandlerFlattensContentList(EvalCase):
    name = "mcp-handler-flattens-content-list"
    description = (
        "M12: handler flattens MCP content list (text/image/resource) "
        "into a single string with image/resource placeholders"
    )

    def run(self) -> EvalResult:
        from loom.agent import mcp_client
        from loom.agent import mcp_manager as mm

        _install_fake_server("fs")
        try:
            # Mixed content: text + image + resource.
            content = [
                {"type": "text", "text": "first line"},
                {
                    "type": "image",
                    "data": "BASE64==",
                    "mimeType": "image/png",
                },
                {
                    "type": "resource",
                    "resource": {"uri": "file:///etc/hosts"},
                },
                {"type": "text", "text": "second line"},
            ]
            with patch.object(mcp_client, "call_tool", return_value=content):
                handler = mm._make_mcp_handler("fs", "mixed")
                out = handler()

            if not isinstance(out, str):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"handler returned non-string: {type(out).__name__}",
                )

            # Text blocks emitted in order.
            if "first line" not in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"first text block missing from output: {out!r}",
                )
            if "second line" not in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"second text block missing from output: {out!r}",
                )

            # Image became a placeholder, not a giant base64 blob.
            if "image/png image content omitted" not in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"image placeholder missing: {out!r}",
                )
            if "BASE64" in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="raw image base64 leaked into output (context-blow risk)",
                )

            # Resource became a placeholder.
            if "[MCP: resource file:///etc/hosts]" not in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"resource placeholder missing: {out!r}",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail=f"content list flattened to {len(out)}-char string "
                       "with text + image + resource placeholders",
            )
        finally:
            _cleanup_fake_server("fs")


class MCPHandlerTruncatesOversizedOutput(EvalCase):
    name = "mcp-handler-truncates-oversized-output"
    description = (
        "M7: handler truncates >50KB output to 50KB + footer + "
        "mcp_output_truncated trace event"
    )

    def run(self) -> EvalResult:
        from loom.agent import mcp_client
        from loom.agent import mcp_manager as mm
        from loom.agent import trace as trace_mod

        class _FakeTrace:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            def record(self, event: str, **fields: object) -> None:
                self.events.append((event, fields))

        fake = _FakeTrace()
        _install_fake_server("big")
        try:
            # 60KB response — well past the 50KB cap.
            big_text = "x" * 60000
            with patch.object(trace_mod, "current", return_value=fake), \
                 patch.object(
                     mcp_client, "call_tool",
                     return_value=[{"type": "text", "text": big_text}],
                 ):
                handler = mm._make_mcp_handler("big", "huge")
                out = handler()

            if not isinstance(out, str):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"handler returned non-string: {type(out).__name__}",
                )

            # Body must be 50000 chars + footer.
            body, sep, footer = out.rpartition("\n... (truncated, ")
            if sep != "\n... (truncated, ":
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"missing truncation footer: {out[-80:]!r}",
                )
            if len(body) != 50000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"body length = {len(body)}, want 50000",
                )
            if not footer.startswith("10000 more characters)"):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"footer overflow count wrong: {footer!r}",
                )

            # Trace event recorded.
            event_names = [e for e, _ in fake.events]
            if "mcp_output_truncated" not in event_names:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"mcp_output_truncated event not recorded; got {event_names}",
                )
            trunc = next(f for e, f in fake.events if e == "mcp_output_truncated")
            if trunc.get("capped_len") != 50000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"trace capped_len = {trunc.get('capped_len')}, want 50000",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail=f"60KB output truncated to 50000 chars + footer, "
                       f"overflow={trunc.get('original_len', '?')}",
            )
        finally:
            _cleanup_fake_server("big")


class MCPHandlerCrashVisibleWarning(EvalCase):
    name = "mcp-handler-crash-visible-warning"
    description = (
        "M6: handler call_tool failure → logger.warning visible + server "
        "evicted + mcp__<server>__* tools unregistered from TOOL_REGISTRY"
    )

    def run(self) -> EvalResult:
        from loom.agent import mcp_client
        from loom.agent import mcp_manager as mm
        from loom.agent.tool_registry import Tool
        from loom.agent.tools import TOOL_REGISTRY

        server_name = "crashy"
        _install_fake_server(server_name)
        try:
            # Pre-register two tools so we can verify they are unregistered.
            for tname in (f"mcp__{server_name}__a", f"mcp__{server_name}__b"):
                TOOL_REGISTRY.register(Tool(
                    name=tname,
                    description="t",
                    input_schema={"type": "object", "properties": {}},
                    handler=lambda **kw: "x",
                    is_read_only=False,
                    is_concurrent_safe=False,
                    enabled=True,
                ))
            assert f"mcp__{server_name}__a" in TOOL_REGISTRY.names()

            with patch.object(
                mcp_client, "call_tool",
                side_effect=RuntimeError("simulated server crash"),
            ), patch.object(mm.logger, "warning") as warning_spy:
                handler = mm._make_mcp_handler(server_name, "broken")
                out = handler()

            # Returns a string error, not raise.
            if not isinstance(out, str):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"handler returned non-string: {type(out).__name__}",
                )
            if "MCP error" not in out or "evicted" not in out:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"error message missing 'MCP error' / 'evicted': {out!r}",
                )

            # Server evicted from cache.
            if server_name in mm._ACTIVE_SERVERS:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"server '{server_name}' still in _ACTIVE_SERVERS",
                )
            if server_name in mm._PER_SERVER_LOCKS:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"server '{server_name}' still in _PER_SERVER_LOCKS",
                )

            # Tools unregistered.
            if f"mcp__{server_name}__a" in TOOL_REGISTRY.names():
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"mcp__{server_name}__a still registered",
                )
            if f"mcp__{server_name}__b" in TOOL_REGISTRY.names():
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"mcp__{server_name}__b still registered",
                )

            # Visible warning emitted. loguru uses lazy %-format, so render
            # the template + args to inspect the actual message.
            if not warning_spy.called:
                return EvalResult(
                    name=self.name, passed=False,
                    detail="M6 visible warning NOT emitted via logger.warning",
                )
            warning_args = warning_spy.call_args.args
            rendered = warning_args[0] % warning_args[1:]
            if server_name not in rendered or "evicted" not in rendered:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"M6 warning missing server name or 'evicted': {rendered!r}",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail="crash → visible warning + server evicted + "
                       "2 tools unregistered + error string returned",
            )
        finally:
            _cleanup_fake_server(server_name)