"""Path resolution and Q3 own/foreign project detection.

A project is identified by its ``.minicode/`` directory. ``MEMORY.md`` is
expected at ``<project_root>/.minicode/memory/MEMORY.md``.

The detection rule is: walk up from the memory path until ``.minicode`` is
found; the project root is ``.minicode``'s parent. The running agent's
own project owns that root if ``.minicode`` is inside ``WORKDIR`` (or
``WORKDIR`` itself).
"""

from __future__ import annotations

from pathlib import Path

MEMORY_DIR_NAME = ".minicode"
MEMORY_SUBDIR = "memory"
MEMORY_FILE = "MEMORY.md"

DEFAULT_WORKDIR = Path.cwd()


def memory_dir(root: Path = DEFAULT_WORKDIR) -> Path:
    return root / MEMORY_DIR_NAME / MEMORY_SUBDIR


def memory_file(root: Path = DEFAULT_WORKDIR) -> Path:
    return memory_dir(root) / MEMORY_FILE


def session_log_path(session_id: str, root: Path = DEFAULT_WORKDIR) -> Path:
    return memory_dir(root) / f"{session_id}.jsonl"


def find_project_root(memory_path: Path) -> Path | None:
    current = memory_path.resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if parent.name == MEMORY_DIR_NAME:
            return parent.parent
    return None


def is_own_project(memory_path: Path, workdir: Path = DEFAULT_WORKDIR) -> bool:
    project_root = find_project_root(memory_path)
    if project_root is None:
        return False
    return project_root.is_relative_to(workdir.resolve())
