"""MEMORY.md read/write/search + per-session event log.

Two-step save invariant (from the memory-persistence reference):
  1. Write full content to a dedicated topic file (under ``.minicode/memory/topics/``)
  2. Append a one-line pointer to MEMORY.md (the index)

If the process crashes between the two steps, the worst outcome is an
orphaned topic file — the index remains consistent.

For Phase 2 we keep the simpler single-file model (MEMORY.md holds the
whole index + most-recent content) and provide the topic-file path as
an extension point for Phase 4 (when memory volume justifies the split).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loom.memory.paths import (
    DEFAULT_WORKDIR,
    memory_dir,
    memory_file,
    session_log_path,
)

MAX_INDEX_BYTES = 25 * 1024
MAX_INDEX_LINES = 200


@dataclass
class MemoryStore:
    root: Path = field(default_factory=lambda: DEFAULT_WORKDIR)

    def __post_init__(self) -> None:
        self.dir = memory_dir(self.root)
        self.file = memory_file(self.root)
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.file.exists():
            self.file.write_text(
                "# Project Memory\n\n"
                "Long-term cross-session memory. The agent appends here.\n"
                "Index is hard-capped at 200 lines / 25 KB.\n",
                encoding="utf-8",
            )

    def read(self) -> str:
        return self.file.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        self._enforce_caps(content)
        self.file.write_text(content, encoding="utf-8")

    def append(self, entry: str, heading: str | None = None) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        title = heading or timestamp
        block = f"\n\n## {title}\n\n{entry.strip()}\n"
        current = self.read().rstrip() + "\n"
        new = current + block
        self._enforce_caps(new)
        self.file.write_text(new, encoding="utf-8")

    def search(self, query: str, case_insensitive: bool = True) -> list[str]:
        if not query:
            return []
        haystack = self.read()
        if case_insensitive:
            return [
                line for line in haystack.splitlines()
                if query.lower() in line.lower()
            ]
        return [line for line in haystack.splitlines() if query in line]

    def summary(self, max_lines: int = 30) -> str:
        return "\n".join(self.read().splitlines()[:max_lines])

    @staticmethod
    def _enforce_caps(content: str) -> None:
        size = len(content.encode("utf-8"))
        line_count = content.count("\n")
        if size > MAX_INDEX_BYTES:
            raise ValueError(
                f"MEMORY.md would exceed {MAX_INDEX_BYTES} bytes "
                f"(actual: {size}); rotate to a topic file before appending"
            )
        if line_count > MAX_INDEX_LINES:
            raise ValueError(
                f"MEMORY.md would exceed {MAX_INDEX_LINES} lines "
                f"(actual: {line_count}); rotate to a topic file before appending"
            )

    def session_log(self, session_id: str) -> Path:
        return session_log_path(session_id, self.root)

    def append_event(self, session_id: str, event: dict) -> None:
        path = self.session_log(session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, session_id: str) -> list[dict]:
        path = self.session_log(session_id)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


WORD_PATTERN = re.compile(r"\w+")


def token_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text))
