"""Harness eval cases for f-edit-file-v2-p0."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class EditFileV2ExactMatchSucceeds(EvalCase):
    name = "edit-file-v2-exact-match-succeeds"
    description = "run_edit with a single exact match applies and returns a diff"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.txt").write_text("hello world\n")
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_edit("a.txt", "hello", "goodbye")
            finally:
                main.WORKDIR = original
        if "Edited" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"not edited: {out[:80]}")
        if "--- diff ---" not in out:
            return EvalResult(name=self.name, passed=False, detail="no diff in output")
        return EvalResult(name=self.name, passed=True, detail="exact match applied with diff")


class EditFileV2MultipleMatchesRejected(EvalCase):
    name = "edit-file-v2-multiple-matches-rejected"
    description = "run_edit with multiple exact matches returns error and does not modify the file"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.txt").write_text("foo bar foo bar foo")
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_edit("a.txt", "foo", "X")
            finally:
                main.WORKDIR = original
            content = (wd / "a.txt").read_text()
        if "Error" not in out or "multiple_matches" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"unexpected: {out[:80]}")
        if content != "foo bar foo bar foo":
            return EvalResult(name=self.name, passed=False, detail="file was modified")
        return EvalResult(name=self.name, passed=True, detail="multiple matches rejected, file unchanged")


class EditFileV2FuzzyMatchApplies(EvalCase):
    name = "edit-file-v2-fuzzy-match-applies"
    description = "run_edit falls back to difflib fuzzy match when exact match fails (old_text >= 40 chars)"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            snippet = "def calculate_total(items: list[float]) -> float:" + " " * 60 + "return sum(items)\n"
            (wd / "a.py").write_text(snippet)
            fuzzy_old = "def calculate_total(items: List[float]) -> float:" + " " * 60 + "return sum(items)\n"
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_edit("a.py", fuzzy_old, "# replaced\n")
            finally:
                main.WORKDIR = original
            content = (wd / "a.py").read_text()
        if "fuzzy" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"fuzzy not in output: {out[:120]}")
        if not content.startswith("# replaced"):
            return EvalResult(name=self.name, passed=False, detail="fuzzy match did not apply")
        return EvalResult(name=self.name, passed=True, detail="fuzzy match applied (List vs list)")


class MultiEditToolRegistered(EvalCase):
    name = "multi-edit-tool-registered"
    description = "multi_edit tool is registered with proper schema"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY
        tool = TOOL_REGISTRY.get("multi_edit")
        if tool is None:
            return EvalResult(name=self.name, passed=False, detail="multi_edit not in registry")
        if "edits" not in tool.input_schema.get("properties", {}):
            return EvalResult(name=self.name, passed=False, detail="edits property missing")
        if "path" not in tool.input_schema.get("required", []):
            return EvalResult(name=self.name, passed=False, detail="path required missing")
        return EvalResult(name=self.name, passed=True, detail="multi_edit registered correctly")


class MultiEditAtomicOnFailure(EvalCase):
    name = "multi-edit-atomic-on-failure"
    description = "multi_edit leaves file unchanged when one edit fails"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.py").write_text("a = 1\nb = 2\n")
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_multi_edit("a.py", [
                    {"old_text": "a = 1", "new_text": "A = 10"},
                    {"old_text": "nonexistent", "new_text": "X = 99"},
                ])
            finally:
                main.WORKDIR = original
            content = (wd / "a.py").read_text()
        if "Error" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"no error returned: {out[:80]}")
        if content != "a = 1\nb = 2\n":
            return EvalResult(name=self.name, passed=False, detail="file was modified despite failure")
        return EvalResult(name=self.name, passed=True, detail="all-or-nothing semantics verified")


class EditLinesToolRegistered(EvalCase):
    name = "edit-lines-tool-registered"
    description = "edit_lines tool is registered with proper schema"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY
        tool = TOOL_REGISTRY.get("edit_lines")
        if tool is None:
            return EvalResult(name=self.name, passed=False, detail="edit_lines not in registry")
        required = tool.input_schema.get("required", [])
        for field in ("path", "start_line", "end_line", "new_content"):
            if field not in required:
                return EvalResult(name=self.name, passed=False, detail=f"{field} required missing")
        return EvalResult(name=self.name, passed=True, detail="edit_lines registered correctly")


class EditLinesReplacesRange(EvalCase):
    name = "edit-lines-replaces-range"
    description = "edit_lines replaces a 1-indexed inclusive range with new content"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "a.py").write_text("line1\nline2\nline3\nline4\nline5\n")
            import loom.agent.tools as main
            original = main.WORKDIR
            main.WORKDIR = wd
            try:
                out = main.run_edit_lines("a.py", 2, 3, "REPLACED")
            finally:
                main.WORKDIR = original
            content = (wd / "a.py").read_text()
        if "Replaced" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"unexpected: {out[:80]}")
        if content != "line1\nREPLACED\nline4\nline5\n":
            return EvalResult(name=self.name, passed=False, detail=f"wrong content: {content!r}")
        return EvalResult(name=self.name, passed=True, detail="lines 2-3 replaced, others preserved")
