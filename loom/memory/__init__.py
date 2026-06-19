"""Memory persistence + context loading for the loom agent.

Layout (under ``workdir``):
  .minicode/memory/
    MEMORY.md            long-term cross-session memory (one-line index + topic files)
    <session-id>.jsonl   per-session event log
    topics/              on-demand topic detail (two-step save)

Public surface:
  loom.memory.paths    path resolution + Q3 own/foreign detection
  loom.memory.store    MEMORY.md read/write/search + session event log
  loom.memory.context  three-tier context loading
"""

from loom.memory.context import load_tier1, load_tier2, load_tier3
from loom.memory.paths import is_own_project, memory_dir, memory_file, session_log_path
from loom.memory.store import MemoryStore

__all__ = [
    "MemoryStore",
    "is_own_project",
    "memory_dir",
    "memory_file",
    "session_log_path",
    "load_tier1",
    "load_tier2",
    "load_tier3",
]
