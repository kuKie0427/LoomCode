"""PL-2: lsp_manager manages LSP server lifecycle (start / route / shutdown).

Per-extension LSPServer cache keyed by server name. First request for a
given extension spawns ``subprocess.Popen`` + runs ``lsp_client.start()``
(initialize handshake); subsequent calls reuse the same subprocess.
``shutdown_all()`` terminates every cached server — wired to the
``SessionEnd`` hook in ``loom.agent.loop``.

Thread safety:
- ``_LOCK`` guards the cache dict for check-then-spawn.
- ``_PER_SERVER_LOCKS[name]`` serialises JSON-RPC reads/writes against
  the same stdin/stdout pair, so concurrent tool calls to the same
  server cannot interleave.

Fail-closed contract:
- No config for the file's extension → returns ``None`` (handler emits
  ``"No LSP server configured for ... files"``).
- Command not in PATH → raises ``FileNotFoundError`` (handler emits
  ``"LSP unavailable: ..."``).
- Handshake / runtime error → raised by ``lsp_client.start`` /
  ``lsp_client.X`` (handler catches and emits ``"LSP error: ..."``).
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from loguru import logger

from loom.agent.config import HarnessConfig
from loom.agent.lsp_client import LSPServer, shutdown, start

_LOCK = threading.Lock()
_ACTIVE_SERVERS: dict[str, LSPServer] = {}
_PER_SERVER_LOCKS: dict[str, threading.Lock] = {}


def get_or_start(file_path: str | Path, config: HarnessConfig) -> LSPServer | None:
    """Look up the LSP server for ``file_path``'s extension; lazy-start if needed.

    Returns ``None`` when no server is configured for the file's extension.
    Raises ``FileNotFoundError`` when the configured ``command`` is not in
    PATH (better diagnostic than a Popen traceback). Raises
    ``loom.agent.lsp_client.LSPError`` on handshake failure.
    """
    spec = config.lsp.find_for(str(file_path))
    if spec is None:
        return None
    with _LOCK:
        if spec.name in _ACTIVE_SERVERS:
            return _ACTIVE_SERVERS[spec.name]
        if shutil.which(spec.command) is None:
            raise FileNotFoundError(
                f"LSP server '{spec.command}' not found in PATH. "
                f"Install hint: see docs/lsp.md"
            )
        server = LSPServer(
            name=spec.name,
            command=spec.command,
            args=list(spec.args),
            cwd=str(Path.cwd()),
        )
        start(server)  # raises LSPError on handshake failure
        _ACTIVE_SERVERS[spec.name] = server
        _PER_SERVER_LOCKS[spec.name] = threading.Lock()
        return server


def get_server_lock(name: str) -> threading.Lock:
    """Return the per-server lock for serialising JSON-RPC against one subprocess."""
    return _PER_SERVER_LOCKS[name]


def shutdown_all() -> None:
    """Terminate every cached LSP server subprocess. Idempotent.

    Individual ``shutdown(server)`` failures are logged but do not stop
    the loop — we still want to evict the rest of the cache and tear
    down the per-server locks. Called from the ``SessionEnd`` hook.
    """
    with _LOCK:
        names = list(_ACTIVE_SERVERS.keys())
        for name in names:
            server = _ACTIVE_SERVERS.pop(name)
            try:
                shutdown(server)
            except Exception:
                logger.warning("Failed to cleanly shutdown LSP server '{}'", name)
        _PER_SERVER_LOCKS.clear()
