"""PL-2 stub: lsp_manager manages LSP server lifecycle (start / route / shutdown).

This module is intentionally a STUB in Phase PL-1 (f-lsp-wire-tool-registry).
The 3 run_lsp_* tool handlers in loom.agent.tools import `get_or_start` from
here and call it; in PL-1 it always raises NotImplementedError so the tools
fail-closed with "LSP unavailable: PL-2: lsp_manager not yet wired".

PL-2 will:
- maintain a per-extension LSPServer cache
- spawn subprocess.Popen + call lsp_client.start() on first request
- route by file extension via LSPConfig.find_for()
- shutdown_all() on AgentStop

The PL-1 handlers wrap every get_or_start call in try/except so this stub
being incomplete is observable as a string return, never as a raise.
"""

from __future__ import annotations

from pathlib import Path


def get_or_start(file_path: Path):  # pragma: no cover — always raises
    """Return a started LSPServer for `file_path`, or None if no config matches.

    PL-2 will route by extension via HarnessConfig.lsp.find_for(file_path)
    and lazily spawn / cache servers. PL-1 raises so handlers fail-closed.
    """
    raise NotImplementedError("PL-2: lsp_manager not yet wired")


def shutdown_all() -> None:  # pragma: no cover — PL-2 will iterate cache
    """Terminate every cached LSP server subprocess. PL-1 is a no-op-but-error."""
    raise NotImplementedError("PL-2: lsp_manager not yet wired")