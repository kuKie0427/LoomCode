"""Thread-safe registry for background subagents.

A background subagent is launched via ``task(background=true)`` and runs
in a daemon thread.  The main agent loop receives an immediate placeholder
result and can later poll for completion via the ``subagent_poll`` tool.

The registry is a module-level singleton that persists across agent_loop
invocations.  Completed/error entries are cleaned up after 10 minutes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class BackgroundSubagent:
    """Snapshot of a background subagent's state."""

    subagent_id: str  # == tool_use_id from the originating task call
    description: str
    status: Literal["running", "done", "error"] = "running"
    result: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    turns: int = 0
    tool_calls: int = 0
    error: str | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


_STALE_SECONDS = 600  # 10 minutes


class BackgroundRegistry:
    """Thread-safe registry of background subagents."""

    def __init__(self) -> None:
        self._registry: dict[str, BackgroundSubagent] = {}
        self._lock = threading.Lock()

    # -- write API ----------------------------------------------------------

    def register(self, subagent_id: str, description: str) -> BackgroundSubagent:
        """Register a new background subagent. Returns the created entry."""
        entry = BackgroundSubagent(subagent_id=subagent_id, description=description)
        with self._lock:
            self._registry[subagent_id] = entry
        return entry

    def complete(
        self,
        subagent_id: str,
        result: str,
        turns: int = 0,
        tool_calls: int = 0,
        error: str | None = None,
    ) -> None:
        """Mark a background subagent as done or error."""
        with self._lock:
            entry = self._registry.get(subagent_id)
            if entry is None:
                return
            entry.result = result
            entry.turns = turns
            entry.tool_calls = tool_calls
            entry.error = error
            entry.finished_at = time.monotonic()
            entry.status = "error" if error is not None else "done"

    # -- read API -----------------------------------------------------------

    def get(self, subagent_id: str) -> BackgroundSubagent | None:
        with self._lock:
            return self._registry.get(subagent_id)

    def list_running(self) -> list[BackgroundSubagent]:
        with self._lock:
            return [e for e in self._registry.values() if e.status == "running"]

    def list_all(self) -> list[BackgroundSubagent]:
        with self._lock:
            return list(self._registry.values())

    # -- maintenance --------------------------------------------------------

    def cleanup_stale(self, max_age_seconds: float = _STALE_SECONDS) -> int:
        """Remove done/error entries older than *max_age_seconds*.

        Running entries are never removed.  Returns the number of entries
        pruned.
        """
        now = time.monotonic()
        pruned = 0
        with self._lock:
            stale_ids = [
                sid
                for sid, entry in self._registry.items()
                if entry.status != "running"
                    and entry.finished_at is not None
                    and (now - entry.finished_at) > max_age_seconds
            ]
            for sid in stale_ids:
                del self._registry[sid]
                pruned += 1
        return pruned

    def clear(self) -> None:
        """Remove all entries (used in tests)."""
        with self._lock:
            self._registry.clear()


# Module-level singleton
_REGISTRY = BackgroundRegistry()


def get_registry() -> BackgroundRegistry:
    """Return the module-level singleton."""
    return _REGISTRY
