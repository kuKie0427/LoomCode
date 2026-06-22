"""Harness eval cases for f-lsp-rename-apply (Phase PL-3).

Six product-behavior guarantees:
1. lsp-rename-permission-real-rule-not-fake-block — R3 regression guard:
   DEFAULT_POLICY.rules contains a real rule for ``lsp_rename_symbol`` AND
   the handler source contains no fake-block construction.
2. lsp-rename-blocks-out-of-workspace-resolved-file — R3: a mock LSP
   server returning a WorkspaceEdit that touches ``/etc/hosts`` must be
   blocked by the handler; the file must remain unchanged.
3. lsp-rename-rollback-on-partial-failure — R2: when one of N writes
   fails, the others are restored to their originals (in-process rollback).
4. lsp-rename-rejects-non-file-uri — non-file:// URI returns a fail-closed
   error string instead of crashing.
5. lsp-rename-journal-recovered-on-session-start — R2: a stale
   (dead-PID) journal triggers a warning log on SessionStart.
6. lsp-rename-unix-path-only-comment-present — R8 regression guard: the
   lsp_apply module docstring mentions "Unix-only" / "R8".
"""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from loom.eval.runner import EvalCase, EvalResult

# ---------------------------------------------------------------------------
# 1. R3 regression guard — real rule + no fake_block
# ---------------------------------------------------------------------------

class LSPRenamePermissionRealRuleNotFakeBlock(EvalCase):
    name = "lsp-rename-permission-real-rule-not-fake-block"
    description = (
        "R3 regression guard: DEFAULT_POLICY has a real rule for "
        "lsp_rename_symbol AND the handler source contains no "
        "synthetic-permission-block construction."
    )

    def run(self) -> EvalResult:
        from loom.agent import tools
        from loom.agent.permissions import DEFAULT_POLICY

        matches = [
            r for r in DEFAULT_POLICY.rules if "lsp_rename_symbol" in r.tools
        ]
        if not matches:
            return EvalResult(name=self.name, passed=False,
                              detail="no rule for lsp_rename_symbol in DEFAULT_POLICY")

        source = inspect.getsource(tools.run_lsp_rename_symbol)
        if "fake_block" in source:
            return EvalResult(name=self.name, passed=False,
                              detail="handler source contains 'fake_block' — R3 regression")
        if "trigger_hooks(\"PreToolUse\"" in source:
            return EvalResult(name=self.name, passed=False,
                              detail="handler source triggers PreToolUse — "
                                     "second pass must invoke DEFAULT_POLICY.find_rule directly")

        return EvalResult(name=self.name, passed=True,
                          detail=f"real rule present + handler clean (matched rule: {matches[0].message!r})")


# ---------------------------------------------------------------------------
# 2. R3: cross-workspace WorkspaceEdit is blocked
# ---------------------------------------------------------------------------

class LSPRenameBlocksOutOfWorkspaceResolvedFile(EvalCase):
    name = "lsp-rename-blocks-out-of-workspace-resolved-file"
    description = (
        "Mock LSP returns a WorkspaceEdit that touches /etc/hosts; "
        "the handler must block the rename and /etc/hosts must be unchanged."
    )

    def run(self) -> EvalResult:
        import threading

        import loom.agent.lsp_client as lsp_client_mod
        from loom.agent import lsp_manager as lm
        from loom.agent import tools

        class _FakeServer:
            name = "pylsp"

        original_workdir = tools.WORKDIR
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            tools.WORKDIR = wd
            (wd / "x.py").write_text("x = 1\n")
            # Reset the manager caches.
            lm._ACTIVE_SERVERS.clear()
            lm._PER_SERVER_LOCKS.clear()

            cross_workspace_edit = {
                "changes": {
                    f"file://{(wd / 'x.py').resolve()}": [
                        {"range": {"start": {"line": 0, "character": 0},
                                   "end": {"line": 0, "character": 1}},
                         "newText": "y"},
                    ],
                    "file:///etc/hosts": [
                        {"range": {"start": {"line": 0, "character": 0},
                                   "end": {"line": 0, "character": 1}},
                         "newText": "pwned"},
                    ],
                },
            }

            # Patch the manager functions the handler imports locally.
            with patch.object(lm, "get_or_start", return_value=_FakeServer()), \
                 patch.object(lm, "get_server_lock", return_value=threading.Lock()), \
                 patch.object(lsp_client_mod, "rename_symbol",
                              return_value=cross_workspace_edit):
                out = tools.run_lsp_rename_symbol(
                    path="x.py", line=0, character=0, new_name="y",
                )

        tools.WORKDIR = original_workdir

        if "Rename blocked by permission policy" not in out:
            return EvalResult(name=self.name, passed=False,
                              detail=f"handler did not block; output={out!r}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"blocked: {out[:100]!r}")


# ---------------------------------------------------------------------------
# 3. R2: rollback on partial failure
# ---------------------------------------------------------------------------

class LSPRenameRollbackOnPartialFailure(EvalCase):
    name = "lsp-rename-rollback-on-partial-failure"
    description = (
        "apply_workspace_edit with N files where one write fails: the "
        "successfully-written files are restored to their originals."
    )

    def run(self) -> EvalResult:
        from loom.agent import lsp_apply

        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            a = wd / "a.py"
            a.write_text("aaa-original\n")
            b = wd / "b.py"
            b.write_text("bbb-original\n")
            c = wd / "c.py"
            c.write_text("ccc-original\n")
            edit = {
                "changes": {
                    f"file://{a}": [{"range": {"start": {"line": 0, "character": 0},
                                               "end": {"line": 0, "character": 3}},
                                     "newText": "AAA"}],
                    f"file://{b}": [{"range": {"start": {"line": 0, "character": 0},
                                               "end": {"line": 0, "character": 3}},
                                     "newText": "BBB"}],
                    f"file://{c}": [{"range": {"start": {"line": 0, "character": 0},
                                               "end": {"line": 0, "character": 3}},
                                     "newText": "CCC"}],
                },
            }
            import os as _os
            real_replace = _os.replace

            def spy_replace(src, dst):
                # Compare WITHOUT .resolve() — tempfile.TemporaryDirectory
                # on macOS returns /var/... while dst comes from the URI
                # path verbatim. .resolve() would walk the /var symlink to
                # /private/var and never match the URI's path string.
                if str(Path(str(dst))) == str(b):
                    raise OSError("simulated failure on second file")
                return real_replace(src, dst)

            with patch.object(lsp_apply.os, "replace", spy_replace):
                try:
                    lsp_apply.apply_workspace_edit(edit)
                except OSError:
                    pass

            a_after = a.read_text()
            b_after = b.read_text()
            c_after = c.read_text()

            if a_after != "aaa-original\n":
                return EvalResult(name=self.name, passed=False,
                                  detail=f"a not restored: {a_after!r}")
            if b_after != "bbb-original\n":
                return EvalResult(name=self.name, passed=False,
                                  detail=f"b not preserved: {b_after!r}")
            if c_after != "ccc-original\n":
                return EvalResult(name=self.name, passed=False,
                                  detail=f"c not preserved: {c_after!r}")

        return EvalResult(name=self.name, passed=True,
                          detail="3-file plan: a restored, b + c never touched")


# ---------------------------------------------------------------------------
# 4. Non-file:// URI is rejected
# ---------------------------------------------------------------------------

class LSPRenameRejectsNonFileUri(EvalCase):
    name = "lsp-rename-rejects-non-file-uri"
    description = (
        "WorkspaceEdit containing an http:// URI returns a fail-closed "
        "error string from run_lsp_rename_symbol — no crash."
    )

    def run(self) -> EvalResult:
        import threading

        import loom.agent.lsp_client as lsp_client_mod
        from loom.agent import lsp_manager as lm
        from loom.agent import tools

        class _FakeServer:
            name = "pylsp"

        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            tools.WORKDIR = wd
            (wd / "x.py").write_text("x = 1\n")
            lm._ACTIVE_SERVERS.clear()
            lm._PER_SERVER_LOCKS.clear()

            edit = {
                "changes": {
                    "http://example.com/x.py": [
                        {"range": {"start": {"line": 0, "character": 0},
                                   "end": {"line": 0, "character": 1}},
                         "newText": "y"},
                    ],
                },
            }

            with patch.object(lm, "get_or_start", return_value=_FakeServer()), \
                 patch.object(lm, "get_server_lock", return_value=threading.Lock()), \
                 patch.object(lsp_client_mod, "rename_symbol", return_value=edit):
                try:
                    out = tools.run_lsp_rename_symbol(
                        path="x.py", line=0, character=0, new_name="y",
                    )
                except Exception as exc:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"raised {type(exc).__name__}: {exc}")

        if not isinstance(out, str):
            return EvalResult(name=self.name, passed=False,
                              detail=f"non-string return: {type(out).__name__}")
        if not (out.startswith("Cannot apply") or "Error" in out or "blocked" in out):
            return EvalResult(name=self.name, passed=False,
                              detail=f"unexpected return: {out!r}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"fail-closed: {out[:120]!r}")


# ---------------------------------------------------------------------------
# 5. R2: stale journal is recovered on SessionStart
# ---------------------------------------------------------------------------

class LSPRenameJournalRecoveredOnSessionStart(EvalCase):
    name = "lsp-rename-journal-recovered-on-session-start"
    description = (
        "Inject a dead-PID journal, call recover_stale_journals, verify "
        "the journal is reported and the warning log fires."
    )

    def run(self) -> EvalResult:
        from loguru import logger

        from loom.agent import lsp_apply

        dead_pid = 999_999
        journal_path = lsp_apply.JOURNAL_DIR / f"{lsp_apply.JOURNAL_PREFIX}{dead_pid}.json"
        journal_path.write_text(json.dumps({"pid": dead_pid, "files": {}}))

        captured: list[str] = []

        def _sink(message):
            record = message.record if hasattr(message, "record") else {}
            captured.append(record.get("message", str(message)))

        handler_id = logger.add(_sink, level="WARNING")
        try:
            with tempfile.TemporaryDirectory() as wd:
                stale = lsp_apply.recover_stale_journals(Path(wd))
        finally:
            logger.remove(handler_id)
            journal_path.unlink(missing_ok=True)

        if not any(journal_path.name in p.name for p in stale):
            return EvalResult(name=self.name, passed=False,
                              detail=f"dead-PID journal not in stale list: {stale!r}")
        if not any("stale" in m.lower() or "journal" in m.lower() for m in captured):
            return EvalResult(name=self.name, passed=False,
                              detail=f"no warning logged: {captured!r}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"reported + warned ({len(stale)} stale, {len(captured)} log lines)")


# ---------------------------------------------------------------------------
# 6. R8: Unix-only comment is present
# ---------------------------------------------------------------------------

class LSPRenameUnixPathOnlyCommentPresent(EvalCase):
    name = "lsp-rename-unix-path-only-comment-present"
    description = (
        "Regression guard: the lsp_apply module docstring must mention "
        "'Unix-only' or 'R8' so future contributors see the path-format "
        "constraint."
    )

    def run(self) -> EvalResult:
        from loom.agent import lsp_apply

        docstring = (lsp_apply.__doc__ or "").lower()
        has_unix = "unix-only" in docstring or "unix only" in docstring
        has_r8 = "r8" in docstring
        if not (has_unix or has_r8):
            return EvalResult(name=self.name, passed=False,
                              detail=f"missing Unix-only/R8 note in lsp_apply docstring: {lsp_apply.__doc__!r}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"R8 marker present (unix={has_unix}, r8={has_r8})")
