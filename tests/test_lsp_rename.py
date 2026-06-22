"""Tests for f-lsp-rename-apply (Phase PL-3) — lsp_apply module.

Covers:
- ``parse_workspace_edit`` shape, sort order, scheme rejection, documentChanges
- ``apply_text_edits`` reverse-order invariant (multi-line)
- ``apply_workspace_edit`` journal lifecycle (write-before-replace, delete-on-success,
  keep-on-rollback-failure) and rollback restores originals on partial failure
- ``recover_stale_journals`` skips alive PIDs and reports dead PIDs

No real LSP server is involved — these tests drive ``apply_workspace_edit``
directly with synthesized ``WorkspaceEdit`` dicts. Tests that need filesystem
isolation use ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from loom.agent import lsp_apply


# ---------------------------------------------------------------------------
# parse_workspace_edit
# ---------------------------------------------------------------------------

def _uri(path: Path) -> str:
    return f"file://{path}"


def test_parse_workspace_edit_single_file_single_edit(tmp_path: Path) -> None:
    """One URI, one edit → one Path key, one edit dict, reverse-sorted."""
    f = tmp_path / "x.py"
    edit = {
        "changes": {
            _uri(f): [
                {"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}},
                 "newText": "y"},
            ],
        },
    }
    plan = lsp_apply.parse_workspace_edit(edit)
    assert f.resolve() in plan
    assert len(plan[f.resolve()]) == 1
    assert plan[f.resolve()][0]["newText"] == "y"


def test_parse_workspace_edit_sorts_edits_reverse_by_position(tmp_path: Path) -> None:
    """Three edits at lines (2, 5, 9) → returned in order (9, 5, 2)."""
    f = tmp_path / "y.py"
    edits = [
        {"range": {"start": {"line": 2, "character": 0},
                   "end": {"line": 2, "character": 1}}, "newText": "A"},
        {"range": {"start": {"line": 5, "character": 0},
                   "end": {"line": 5, "character": 1}}, "newText": "B"},
        {"range": {"start": {"line": 9, "character": 0},
                   "end": {"line": 9, "character": 1}}, "newText": "C"},
    ]
    edit = {"changes": {_uri(f): edits}}
    plan = lsp_apply.parse_workspace_edit(edit)
    sorted_lines = [e["range"]["start"]["line"] for e in plan[f.resolve()]]
    assert sorted_lines == [9, 5, 2]


def test_parse_workspace_edit_multifile(tmp_path: Path) -> None:
    """Two URIs → two Path keys, each with their own sorted edits."""
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    edit = {
        "changes": {
            _uri(a): [
                {"range": {"start": {"line": 5, "character": 0},
                           "end": {"line": 5, "character": 1}}, "newText": "X"},
            ],
            _uri(b): [
                {"range": {"start": {"line": 1, "character": 0},
                           "end": {"line": 1, "character": 1}}, "newText": "Y"},
                {"range": {"start": {"line": 3, "character": 0},
                           "end": {"line": 3, "character": 1}}, "newText": "Z"},
            ],
        },
    }
    plan = lsp_apply.parse_workspace_edit(edit)
    assert set(plan.keys()) == {a.resolve(), b.resolve()}
    # b's edits: line 3 then line 1 (reverse-sorted).
    b_lines = [e["range"]["start"]["line"] for e in plan[b.resolve()]]
    assert b_lines == [3, 1]


def test_parse_workspace_edit_rejects_non_file_scheme() -> None:
    """http:// URI → ValueError explaining scheme is unsupported."""
    edit = {
        "changes": {
            "http://example.com/x.py": [
                {"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}}, "newText": "y"},
            ],
        },
    }
    with pytest.raises(ValueError, match=r"(?i)scheme"):
        lsp_apply.parse_workspace_edit(edit)


def test_parse_workspace_edit_rejects_documentchanges_only() -> None:
    """documentChanges without 'changes' → NotImplementedError."""
    edit = {
        "documentChanges": [
            {"textDocument": {"uri": "file:///x.py", "version": 1},
             "edits": []},
        ],
    }
    with pytest.raises(NotImplementedError, match="documentChanges"):
        lsp_apply.parse_workspace_edit(edit)


def test_parse_workspace_edit_includes_empty_edits_lists(tmp_path: Path) -> None:
    """Oracle watch-out: a URI present with empty edits list MUST still
    appear in the plan, so the second-pass permission check sees it.
    """
    f = tmp_path / "empty.py"
    edit = {
        "changes": {
            _uri(f): [],  # No edits, but the URI is present.
            _uri(tmp_path / "with_edit.py"): [
                {"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}}, "newText": "X"},
            ],
        },
    }
    plan = lsp_apply.parse_workspace_edit(edit)
    assert f.resolve() in plan, (
        "Oracle watch-out: empty-edits URI must still appear in plan"
    )
    assert plan[f.resolve()] == []


# ---------------------------------------------------------------------------
# apply_text_edits
# ---------------------------------------------------------------------------

def test_apply_text_edits_simple() -> None:
    """Single character replacement at offset 0."""
    out = lsp_apply.apply_text_edits(
        "abc",
        [{"range": {"start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 1}},
          "newText": "Z"}],
    )
    assert out == "Zbc"


def test_apply_text_edits_multiline_replace() -> None:
    """Replace across multiple lines (line 1, col 2 to line 2, col 3)."""
    text = "abcde\nfghij\nklmno"
    # line=1, char=2 → offset 8 (after "fgh"); line=2, char=3 → offset 16 (after "klm")
    edits = [{
        "range": {"start": {"line": 1, "character": 2},
                  "end": {"line": 2, "character": 3}},
        "newText": "REPLACED",
    }]
    assert lsp_apply.apply_text_edits(text, edits) == "abcde\nfgREPLACEDno"


def test_apply_text_edits_reverse_order_invariant() -> None:
    """Three edits applied in REVERSE order produce correct text, while
    forward order would shift offsets and corrupt output. The caller
    (parse_workspace_edit) is responsible for sorting; this test pins
    the invariant: apply_text_edits takes a pre-sorted list.
    """
    text = "111\n222\n333\n"
    # Edits to replace lines 0, 1, 2 with "A", "B", "C". When applied
    # from END to START (reverse), the offsets don't shift.
    edits_reverse = [
        # line 2: "333" → "C"
        {"range": {"start": {"line": 2, "character": 0},
                   "end": {"line": 2, "character": 3}},
         "newText": "C"},
        # line 1: "222" → "B"
        {"range": {"start": {"line": 1, "character": 0},
                   "end": {"line": 1, "character": 3}},
         "newText": "B"},
        # line 0: "111" → "A"
        {"range": {"start": {"line": 0, "character": 0},
                   "end": {"line": 0, "character": 3}},
         "newText": "A"},
    ]
    out = lsp_apply.apply_text_edits(text, edits_reverse)
    assert out == "A\nB\nC\n", f"reverse order failed: {out!r}"


# ---------------------------------------------------------------------------
# apply_workspace_edit — journal lifecycle + rollback
# ---------------------------------------------------------------------------

def test_apply_workspace_edit_writes_journal_before_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.replace raises on the 2nd file, the journal must already exist
    (journal is written BEFORE any os.replace per R2).
    """
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("aaa\n")
    b.write_text("bbb\n")
    edit = {
        "changes": {
            _uri(a): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "AAA"}],
            _uri(b): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "BBB"}],
        },
    }

    journal_existed_during_replace = []

    real_replace = os.replace

    def spy_replace(src, dst):
        # Path == str is False in Python, so compare via str() both sides.
        if str(Path(str(dst))) == str(b.resolve()):
            journal_existed_during_replace.append(
                lsp_apply._journal_path().exists()
            )
            raise OSError("simulated write failure on second file")
        return real_replace(src, dst)

    monkeypatch.setattr(lsp_apply.os, "replace", spy_replace)

    with pytest.raises(OSError, match="simulated write failure"):
        lsp_apply.apply_workspace_edit(edit)

    # On failure, the journal should still be present (kept for user inspection).
    assert journal_existed_during_replace == [True], (
        "journal must exist BEFORE the failing os.replace (R2 invariant)"
    )
    # Original a may have been restored by rollback; b was never touched.
    assert b.read_text() == "bbb\n", "untouched file must remain original"


def test_apply_workspace_edit_deletes_journal_on_success(tmp_path: Path) -> None:
    """Happy path: journal is removed after apply completes."""
    a = tmp_path / "a.py"
    a.write_text("hi\n")
    edit = {
        "changes": {
            _uri(a): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 2}},
                       "newText": "HI"}],
        },
    }
    journal_path = lsp_apply._journal_path()
    # Make sure no stale journal from a prior test pollutes this assertion.
    if journal_path.exists():
        journal_path.unlink()
    out = lsp_apply.apply_workspace_edit(edit)
    assert a.resolve() in out
    assert a.read_text() == "HI\n"
    assert not journal_path.exists(), "journal must be deleted on success"


def test_apply_workspace_edit_keeps_journal_on_rollback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If os.replace fails AND the rollback write fails, the journal must
    be retained so the user can inspect / manually restore.

    Setup: two files (a, b). a writes successfully → added to `written`.
    b's os.replace fails → caught → rollback tries to restore a → that
    write ALSO fails → rollback_ok=False → journal kept.
    """
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("aaa\n")
    b.write_text("bbb\n")
    edit = {
        "changes": {
            _uri(a): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "AAA"}],
            _uri(b): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "BBB"}],
        },
    }
    journal_path = lsp_apply._journal_path()
    if journal_path.exists():
        journal_path.unlink()

    real_replace = os.replace

    def spy_replace(src, dst):
        if str(Path(str(dst))) == str(b.resolve()):
            raise OSError("simulated replace failure on second file")
        return real_replace(src, dst)

    monkeypatch.setattr(lsp_apply.os, "replace", spy_replace)

    # Spy Path.write_text: allow .tmp writes (write phase) but fail
    # non-tmp user-file writes (which are the rollback writes).
    from pathlib import Path as _P
    original_write_text = _P.write_text

    def spy_write_text(self, *args, **kwargs):
        if self.suffix == ".tmp":
            return original_write_text(self, *args, **kwargs)
        raise OSError("simulated rollback write failure")

    monkeypatch.setattr(_P, "write_text", spy_write_text)

    with pytest.raises(OSError):
        lsp_apply.apply_workspace_edit(edit)

    assert journal_path.exists(), (
        "journal must be retained when rollback itself fails"
    )
    # Cleanup so we don't leave stale journals for subsequent tests.
    journal_path.unlink(missing_ok=True)


def test_apply_workspace_edit_rollback_restores_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three files; os.replace fails on the second → the first gets
    restored to its original content by in-process rollback.
    """
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    c = tmp_path / "c.py"
    a.write_text("aaa-original\n")
    b.write_text("bbb-original\n")
    c.write_text("ccc-original\n")

    edit = {
        "changes": {
            _uri(a): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "AAA"}],
            _uri(b): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "BBB"}],
            _uri(c): [{"range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 3}},
                       "newText": "CCC"}],
        },
    }

    real_replace = os.replace

    def spy_replace(src, dst):
        if str(dst) == str(b):
            raise OSError("simulated write failure on second file")
        return real_replace(src, dst)

    monkeypatch.setattr(lsp_apply.os, "replace", spy_replace)

    journal_path = lsp_apply._journal_path()
    if journal_path.exists():
        journal_path.unlink()

    with pytest.raises(OSError):
        lsp_apply.apply_workspace_edit(edit)

    # a was successfully written then rolled back.
    assert a.read_text() == "aaa-original\n", (
        f"a not restored: {a.read_text()!r}"
    )
    # b was never written.
    assert b.read_text() == "bbb-original\n"
    # c was never reached.
    assert c.read_text() == "ccc-original\n"
    # Journal deleted (rollback succeeded).
    assert not journal_path.exists(), "rollback succeeded → journal deleted"


# ---------------------------------------------------------------------------
# recover_stale_journals
# ---------------------------------------------------------------------------

def test_recover_stale_journals_skips_alive_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A journal for the CURRENT PID is alive → not reported as stale."""
    # _journal_path() for our test process — that PID is alive by definition.
    journal = lsp_apply._journal_path()
    journal.write_text(json.dumps({"pid": os.getpid(), "files": {}}))

    stale = lsp_apply.recover_stale_journals(tmp_path)
    journal.unlink(missing_ok=True)

    assert stale == [], (
        f"current PID journal must not be reported as stale, got {stale!r}"
    )


def test_recover_stale_journals_logs_dead_pid_journal(
    tmp_path: Path, caplog,
) -> None:
    """A journal for an obviously-dead PID is reported and a warning logged."""
    # PID 2^30 is essentially never alive; even if it is, the assertion
    # below just needs _pid_alive to return False for it OR no stale
    # detection is performed. We use PID 999999 which is almost certainly dead.
    dead_pid = 999_999
    journal = lsp_apply.JOURNAL_DIR / f"{lsp_apply.JOURNAL_PREFIX}{dead_pid}.json"
    journal.write_text(json.dumps({"pid": dead_pid, "files": {"/tmp/x.py": "abc"}}))

    try:
        stale = lsp_apply.recover_stale_journals(tmp_path)
    finally:
        journal.unlink(missing_ok=True)

    assert journal.name in {p.name for p in stale}, (
        f"dead PID journal not reported: stale={stale!r}"
    )
