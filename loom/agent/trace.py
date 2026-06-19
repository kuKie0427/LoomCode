"""Structured trace — append-only JSONL log of agent events.

Per docs/harness-roadmap.md::Phase 5:
  Every tool call, hook, and compaction event writes to
  .minicode/trace.jsonl. Fields: ts, session_id, event, tool,
  latency_ms, tokens_in, tokens_out, outcome.

Public surface:
  Trace(workdir, session_id) -> Trace
  Trace.record(event, **fields) -> None
  Trace.flush() -> None
  Trace.recent(n=20) -> list[dict]
  default_path_for(workdir) -> Path
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

TRACE_FILENAME = "trace.jsonl"

_CURRENT: Trace | None = None
_CURRENT_LOCK = threading.Lock()


def default_path_for(workdir: Path) -> Path:
    return workdir / ".minicode" / TRACE_FILENAME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Trace:
    def __init__(self, workdir: Path, session_id: str, sink_command: str | None = None) -> None:
        self.workdir = Path(workdir)
        self.session_id = session_id
        self.sink_command = sink_command
        self.path = default_path_for(self.workdir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        payload = {
            "ts": _now_iso(),
            "session_id": self.session_id,
            "event": event,
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
        if self.sink_command is not None:
            try:
                subprocess.run(
                    [self.sink_command],
                    input=line,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.warning("telemetry sink failed: {}", exc)

    def flush(self) -> None:
        pass

    def set_sink(self, sink_command: str | None) -> None:
        self.sink_command = sink_command

    def recent(self, n: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        out: list[dict] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def current() -> Trace | None:
    return _CURRENT


def start(workdir: Path, session_id: str, sink_command: str | None = None) -> Trace:
    """Initialize a Trace for this session and make it the current one."""
    global _CURRENT
    with _CURRENT_LOCK:
        tr = Trace(workdir, session_id, sink_command=sink_command)
        _CURRENT = tr
        return tr


def set_sink(sink_command: str | None) -> None:
    """Update the active Trace's sink command."""
    with _CURRENT_LOCK:
        if _CURRENT is not None:
            _CURRENT.sink_command = sink_command


def stop() -> None:
    global _CURRENT
    with _CURRENT_LOCK:
        _CURRENT = None
