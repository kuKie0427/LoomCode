"""Apply LSP WorkspaceEdit dicts to the filesystem atomically.

WorkspaceEdit format: "changes" variant only (file URI -> TextEdit[]).
R8 NOTE (Unix-only): assumes Unix-style ``file:///abs/path`` URIs. Windows
``file:///C:/...`` is NOT handled — would create paths like
``/C:/Users/...`` on macOS/Linux. If a Windows harness ever needs this,
add a separate code path here.

Critical invariant: edits to the SAME file must be applied from END to
START (reverse-sorted by start position). Otherwise earlier edits shift
later offsets and produce wrong text.

R2 mitigation (rollback journal): before any ``os.replace``, persist a
journal at ``/tmp/loom-lsp-rollback-<PID>.json`` containing
``{pid, files: {abs_path: sha256(original)}}``. On next SessionStart,
dead-PID journals are detected and the user is warned about
potentially-inconsistent files. We do NOT auto-restore — user decides.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from loguru import logger

JOURNAL_DIR = Path("/tmp")
JOURNAL_PREFIX = "loom-lsp-rollback-"


def _journal_path() -> Path:
    return JOURNAL_DIR / f"{JOURNAL_PREFIX}{os.getpid()}.json"


def _write_journal(originals: dict[Path, str]) -> Path:
    """Write the rollback journal for the current PID.

    Stored as ``{pid, files: {abs_path_str: sha256_hex}}`` — only hashes,
    never file contents, to keep the journal small.
    """
    journal = _journal_path()
    payload = {
        "pid": os.getpid(),
        "files": {
            str(path): hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in originals.items()
        },
    }
    # Write atomically: write to a tmp file in the same dir, then rename.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=JOURNAL_PREFIX, suffix=".json.tmp", dir=str(JOURNAL_DIR),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, journal)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return journal


def _delete_journal() -> None:
    """Remove the rollback journal for the current PID if present."""
    try:
        _journal_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete LSP rollback journal: {}", exc)


def _pid_alive(pid: int) -> bool:
    """Return True iff a process with `pid` exists and we can signal it.

    Uses ``os.kill(pid, 0)`` which sends no signal but raises ``ProcessLookupError``
    if the PID is dead and ``PermissionError`` if we lack permission to signal
    it (the PID is still alive in that case).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def recover_stale_journals(workdir: Path) -> list[Path]:
    """Scan ``/tmp`` for orphan LSP rollback journals (dead-PID) and warn.

    Does NOT auto-restore — the user must decide whether files are
    consistent. Returns the list of stale journal paths for testability.
    """
    stale: list[Path] = []
    alive: list[Path] = []
    try:
        for entry in JOURNAL_DIR.glob(f"{JOURNAL_PREFIX}*.json"):
            try:
                with entry.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                # Corrupt or unreadable journal — leave it for user inspection.
                stale.append(entry)
                continue
            pid = payload.get("pid")
            if not isinstance(pid, int) or not _pid_alive(pid):
                stale.append(entry)
            else:
                alive.append(entry)
    except OSError as exc:
        logger.warning("LSP journal scan failed: {}", exc)
        return []
    if stale:
        names = ", ".join(str(p) for p in stale)
        logger.warning(
            "Found {} stale LSP rollback journal(s) (dead PID): {}. "
            "Files listed inside may be inconsistent — inspect manually "
            "before deleting.",
            len(stale), names,
        )
    return stale


def parse_workspace_edit(edit: dict) -> dict[Path, list[dict]]:
    """Parse an LSP WorkspaceEdit dict into a per-file edit plan.

    Returns a ``dict[Path, list[dict]]`` keyed by absolute file path.
    Edits within each file are sorted in REVERSE order by ``(start.line,
    start.character)`` so they can be applied from END to START without
    offset drift.

    R8 (Unix-only): ``file:///abs/path`` URIs only. ``file:///C:/...`` would
    become the literal path ``/C:/...`` on Linux/macOS — NOT supported.

    Raises ``ValueError`` for non-``file://`` schemes (e.g. ``http://``).
    Raises ``NotImplementedError`` if the edit uses ``documentChanges``
    without the ``changes`` variant — we only support the ``changes`` form
    for now.

    Oracle watch-out: files whose ``edits`` list is empty MUST still
    appear in the result. The second-pass permission check needs to see
    every URI the rename would touch, not just those with non-empty edits.
    """
    if not isinstance(edit, dict):
        raise ValueError(f"WorkspaceEdit must be a dict, got {type(edit).__name__}")
    changes = edit.get("changes")
    if changes is None:
        if "documentChanges" in edit:
            raise NotImplementedError(
                "WorkspaceEdit.documentChanges is not supported; "
                "use the 'changes' variant (URI -> TextEdit[]) instead."
            )
        raise ValueError("WorkspaceEdit has neither 'changes' nor 'documentChanges'")

    if not isinstance(changes, dict):
        raise ValueError(f"WorkspaceEdit.changes must be a dict, got {type(changes).__name__}")

    plan: dict[Path, list[dict]] = {}
    for uri, edits in changes.items():
        if not isinstance(uri, str):
            raise ValueError(f"WorkspaceEdit key must be a string URI, got {type(uri).__name__}")
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError(
                f"Unsupported WorkspaceEdit URI scheme {parsed.scheme!r} "
                f"(expected 'file'). Only file:// URIs are supported (Unix-only)."
            )
        # urlparse("file:///etc/hosts").netloc == ""; .path == "/etc/hosts".
        # unquote handles %20 etc.
        path = Path(unquote(parsed.path))
        if not isinstance(edits, list):
            raise ValueError(
                f"WorkspaceEdit[{uri}] edits must be a list, got {type(edits).__name__}"
            )
        # Sort REVERSE by (start.line, start.character) — invariant for apply_text_edits.
        sorted_edits = sorted(
            edits,
            key=lambda e: (
                e.get("range", {}).get("start", {}).get("line", 0),
                e.get("range", {}).get("start", {}).get("character", 0),
            ),
            reverse=True,
        )
        plan[path] = sorted_edits
    return plan


def apply_text_edits(text: str, edits: list[dict]) -> str:
    """Apply a list of TextEdits to ``text`` and return the new string.

    Edits MUST already be reverse-sorted by ``(start.line, start.character)``
    so that earlier edits don't shift the offsets of later ones. This is
    the invariant ``parse_workspace_edit`` guarantees.

    All positions are 0-indexed (LSP spec). A TextEdit replaces the range
    ``[start, end)`` with ``newText``.
    """
    if not edits:
        return text
    # Pre-compute line offsets for O(1) line -> offset lookup.
    line_starts: list[int] = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def _to_offset(line: int, character: int) -> int:
        if line < 0:
            return 0
        if line >= len(line_starts):
            return len(text)
        return line_starts[line] + character

    out = text
    for edit in edits:
        rng = edit.get("range", {})
        start = rng.get("start", {})
        end = rng.get("end", {})
        s = _to_offset(int(start.get("line", 0)), int(start.get("character", 0)))
        e = _to_offset(int(end.get("line", 0)), int(end.get("character", 0)))
        if s > e:
            s, e = e, s
        if s < 0:
            s = 0
        if e > len(out):
            e = len(out)
        new_text = edit.get("newText", "")
        out = out[:s] + new_text + out[e:]
    return out


def apply_workspace_edit(edit: dict) -> dict[Path, str]:
    """Apply edits atomically with journal-backed rollback.

    WARNING: Caller is responsible for verifying all resolved paths in
    `edit` are within the workspace before calling this function. This
    function does NOT re-check workspace boundaries. The
    ``run_lsp_rename_symbol`` handler performs this check via
    ``DEFAULT_POLICY.find_rule("lsp_rename_symbol", ...)`` with
    ``_resolved_files`` injected. Any other caller must do the same.

    Flow:
    1. parse → plan (may raise ValueError / NotImplementedError)
    2. read all originals + compute all new_contents (any read/compute
       failure aborts before any disk write)
    3. write journal ``/tmp/loom-lsp-rollback-<PID>.json`` with sha256
       of every original
    4. for each file: write to ``<file>.tmp`` + ``os.replace`` (atomic)
    5. success → delete journal, return ``new_contents``
    6. exception during step 4 → in-process rollback (restore originals
       for every successfully-written file); if rollback also fails,
       keep journal as a last-resort recovery hint.
    """
    plan = parse_workspace_edit(edit)

    # Step 2: read all originals + compute new_contents BEFORE any write.
    originals: dict[Path, str] = {}
    new_contents: dict[Path, str] = {}
    for path, edits in plan.items():
        try:
            original = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            original = ""
        originals[path] = original
        new_contents[path] = apply_text_edits(original, edits)

    # Step 3: write journal BEFORE any os.replace (R2).
    _write_journal(originals)

    # Step 4: write each file atomically.
    written: list[Path] = []
    try:
        for path, content in new_contents.items():
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            try:
                tmp_path.write_text(content, encoding="utf-8")
                os.replace(tmp_path, path)
                written.append(path)
            except BaseException:
                # Clean up partial .tmp if it exists, then re-raise for rollback.
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise
    except BaseException as primary_exc:
        # Step 6: in-process rollback — restore originals for everything
        # we successfully wrote. If any restore fails, log + keep journal.
        rollback_ok = True
        for path in written:
            try:
                path.write_text(originals[path], encoding="utf-8")
            except OSError as restore_exc:
                rollback_ok = False
                logger.error(
                    "LSP rollback failed for {}: {}. "
                    "Journal retained — user must restore manually.",
                    path, restore_exc,
                )
        if rollback_ok:
            _delete_journal()
        raise primary_exc

    # Step 5: success.
    _delete_journal()
    return new_contents
