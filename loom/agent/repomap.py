"""Repository map for the system prompt.

Minimal-scope implementation using Python's stdlib `ast` module (no
tree-sitter dependency). Extracts top-level classes + functions +
imports from each .py file under the workspace and produces a
compact symbol map suitable for injection into the system prompt.

Output format per file:
    path/to/file.py: ClassA, ClassB.method, function_foo, helper_bar

Top N files by symbol count (default 20) are included; truncated at
max_tokens (default 4000) to avoid prompt bloat.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


def _symbols_in_file(path: Path) -> list[str]:
    """Extract top-level classes + functions + methods from a .py file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError):
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not child.name.startswith("_"):
                        out.append(f"{node.name}.{child.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                out.append(node.name)
    return out


def build_repomap(workdir: Path, max_files: int = 20, max_tokens: int = 4000) -> str:
    """Return a compact repomap string for injection into the system prompt.

    Top files by symbol count win the budget. Truncates at max_tokens
    (rough char estimate: 1 token ~= 4 chars).
    """
    if not workdir.exists():
        return ""
    file_symbols: list[tuple[Path, list[str]]] = []
    for path in _iter_python_files(workdir):
        symbols = _symbols_in_file(path)
        if symbols:
            file_symbols.append((path, symbols))
    file_symbols.sort(key=lambda fs: -len(fs[1]))

    lines: list[str] = []
    used_chars = 0
    char_budget = max_tokens * 4
    for path, symbols in file_symbols[:max_files]:
        rel = path.relative_to(workdir)
        line = f"{rel}: {', '.join(symbols)}"
        if used_chars + len(line) + 1 > char_budget:
            break
        lines.append(line)
        used_chars += len(line) + 1
    if not lines:
        return ""
    return "Codebase Map (top files by symbol count):\n" + "\n".join(lines)
