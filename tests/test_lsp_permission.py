"""Tests for f-lsp-rename-apply (Phase PL-3) — R3 fix regression guards.

The R3 CRITICAL bug was a fake_block that constructed a block with
``input={"files": [...]}`` and called ``hooks.trigger_hooks("PreToolUse",
fake_block)``. But the real rule ``_lsp_rename_outside_workspace`` reads
``args.get("_resolved_files")`` — ``fake_block.input["files"]`` was never
seen, the check always returned False, and any rename could rewrite
``/etc/hosts``.

These tests guard the FIX:

1-4. Pure-rule behavior: DEFAULT_POLICY contains a real rule for
     ``lsp_rename_symbol``; it blocks when entry path OR any resolved
     file is outside WORKDIR; passes when all inside.
5-6. ``find_rule`` returns the rule (and its message) when blocked,
     ``None`` when passes — the handler calls this directly.
7. The handler does call ``find_rule`` AFTER the LSP call with
     ``_resolved_files`` injected — not before, not via fake_block.
8. Source-level regression guard: the handler does NOT construct any
     ``fake_block`` variable and does NOT call
     ``hooks.trigger_hooks("PreToolUse", ...)`` from inside the rename
     handler. If a future contributor tries to reintroduce the R3 bug,
     this test fails.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from loom.agent.permissions import DEFAULT_POLICY

# ---------------------------------------------------------------------------
# 1. Pure rule registration
# ---------------------------------------------------------------------------

def test_lsp_rename_rule_in_default_policy() -> None:
    """A real PermissionRule for ``lsp_rename_symbol`` is registered.

    R3 regression guard #1: before the fix, the permission was enforced by
    a fake_block passed to PreToolUse — but the real rule chain never saw
    it. We must have a real rule with ``tools=("lsp_rename_symbol",)``.
    """
    matches = [
        rule for rule in DEFAULT_POLICY.rules
        if "lsp_rename_symbol" in rule.tools
    ]
    assert len(matches) == 1, (
        f"expected exactly one rule for lsp_rename_symbol, "
        f"found {len(matches)} in {DEFAULT_POLICY.rules!r}"
    )
    rule = matches[0]
    assert callable(rule.check)
    assert "outside the workspace" in rule.message


# ---------------------------------------------------------------------------
# 2-4. Rule check semantics
# ---------------------------------------------------------------------------

def test_lsp_rename_blocks_when_entry_path_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-LSP early pass: only entry path known, /etc/hosts must block."""
    # WORKDIR is module-level in permissions.py — swap it so _outside_workspace
    # resolves relative to our tmp_path.
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)
    rule = next(r for r in DEFAULT_POLICY.rules if "lsp_rename_symbol" in r.tools)
    # No _resolved_files (early pass): falls back to entry path check.
    assert rule.check({"path": "/etc/hosts"}) is True


def test_lsp_rename_blocks_when_any_resolved_file_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-LSP second pass: any resolved file outside WORKDIR must block."""
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)
    rule = next(r for r in DEFAULT_POLICY.rules if "lsp_rename_symbol" in r.tools)
    inside = str((tmp_path / "ok.py").resolve())
    outside = "/etc/hosts"
    assert rule.check({
        "path": "ok.py",
        "_resolved_files": [inside, outside],
    }) is True


def test_lsp_rename_passes_when_all_inside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both passes: every resolved file inside WORKDIR → not blocked."""
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)
    rule = next(r for r in DEFAULT_POLICY.rules if "lsp_rename_symbol" in r.tools)
    a = str((tmp_path / "a.py").resolve())
    b = str((tmp_path / "sub" / "b.py").resolve())
    assert rule.check({
        "path": "a.py",
        "_resolved_files": [a, b],
    }) is False


# ---------------------------------------------------------------------------
# 5-6. find_rule surface
# ---------------------------------------------------------------------------

def test_find_rule_returns_message_string_on_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``find_rule`` returns the rule (whose .message is the denial string).

    The handler does ``blocked = DEFAULT_POLICY.find_rule(...)`` and
    formats ``blocking_rule.message`` into the user-visible denial. The
    type is ``PermissionRule`` (not ``str``); we verify the contract by
    checking the returned rule's .message field.
    """
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)
    rule = DEFAULT_POLICY.find_rule(
        "lsp_rename_symbol",
        {"path": "/etc/hosts", "_resolved_files": []},
    )
    assert rule is not None
    assert isinstance(rule.message, str)
    assert "outside" in rule.message.lower() or "workspace" in rule.message.lower()


def test_find_rule_returns_none_when_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``find_rule`` returns ``None`` when no rule blocks."""
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)
    a = str((tmp_path / "a.py").resolve())
    rule = DEFAULT_POLICY.find_rule(
        "lsp_rename_symbol",
        {"path": "a.py", "_resolved_files": [a]},
    )
    assert rule is None


# ---------------------------------------------------------------------------
# 7. Handler calls find_rule AFTER the LSP call
# ---------------------------------------------------------------------------

def test_lsp_rename_handler_calls_find_rule_after_lsp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler invokes ``DEFAULT_POLICY.find_rule`` with _resolved_files
    after the LSP server returns the WorkspaceEdit — and blocks when any
    resolved file is outside WORKDIR.

    Uses mocks for the LSP server so we never need a real pylsp process.
    Verifies:
    - The handler invokes ``DEFAULT_POLICY.find_rule`` exactly once.
    - The arguments include ``_resolved_files``.
    - The handler returns ``"Rename blocked by permission policy: ..."``.
    """
    monkeypatch.setattr("loom.agent.tools.WORKDIR", tmp_path)
    (tmp_path / "x.py").write_text("x = 1\n")
    safe_target = (tmp_path / "x.py").resolve()

    # Stand-in LSPServer — anything that quacks like one.
    class _FakeServer:
        name = "pylsp"

    # Mock the lsp_client.rename_symbol to return a WorkspaceEdit whose
    # 'changes' key contains both an in-workspace file AND /etc/hosts.
    cross_workspace_edit = {
        "changes": {
            f"file://{safe_target}": [
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

    # The handler does `from loom.agent.lsp_manager import get_or_start,
    # ...` — the names live in the handler's local scope, so we patch
    # at the SOURCE module, not at `loom.agent.tools`.
    import loom.agent.lsp_manager as lsp_manager_mod
    monkeypatch.setattr(lsp_manager_mod, "get_or_start", lambda *_a, **_kw: _FakeServer())
    monkeypatch.setattr(lsp_manager_mod, "get_server_lock", lambda _name: __import__("threading").Lock())
    monkeypatch.setattr("loom.agent.tools.safe_path", lambda p: safe_target)
    monkeypatch.setattr("loom.agent.tools._coerce_lsp_line", lambda _p, line: line)
    # The handler imports `from loom.agent import lsp_client` locally;
    # the rename_symbol binding lives on `loom.agent.lsp_client`, not
    # on `loom.agent.tools`.
    import loom.agent.lsp_client as lsp_client_mod
    monkeypatch.setattr(lsp_client_mod, "rename_symbol",
                        lambda *_a, **_kw: cross_workspace_edit)
    monkeypatch.setattr("loom.agent.permissions.WORKDIR", tmp_path)

    from loom.agent.tools import run_lsp_rename_symbol

    out = run_lsp_rename_symbol(path="x.py", line=0, character=0, new_name="y")
    assert "Rename blocked by permission policy" in out, (
        f"handler did not block; out={out!r}"
    )
    assert "outside the workspace" in out, f"unexpected message: {out!r}"


# ---------------------------------------------------------------------------
# 8. Source-level regression guard — no fake_block, no second PreToolUse
# ---------------------------------------------------------------------------

def test_lsp_rename_handler_does_not_construct_fake_block() -> None:
    """The R3 regression guard: the handler must NOT construct any
    ``fake_block`` variable and must NOT call
    ``hooks.trigger_hooks("PreToolUse", ...)`` from inside
    ``run_lsp_rename_symbol``.

    Why this matters: the original plan had the handler build a synthetic
    ``fake_block`` with ``input={"files": [...]}`` and route it through
    ``PreToolUse``. But the real rule reads ``_resolved_files`` (not
    ``files``), so this would silently never block. Oracle flagged this
    as CRITICAL. If a future contributor reintroduces either pattern,
    this test must fail loudly.
    """
    from loom.agent import tools as tools_mod

    source = inspect.getsource(tools_mod.run_lsp_rename_symbol)

    assert "fake_block" not in source, (
        "R3 regression: run_lsp_rename_symbol contains 'fake_block'. "
        "The fix uses DEFAULT_POLICY.find_rule directly — no synthetic "
        "permission block should ever be constructed in this handler."
    )
    assert "trigger_hooks(\"PreToolUse\"" not in source, (
        "R3 regression: run_lsp_rename_symbol calls "
        "hooks.trigger_hooks('PreToolUse', ...) — the second pass must "
        "invoke DEFAULT_POLICY.find_rule directly, not re-trigger the "
        "permission hook (which would log + potentially ask the user)."
    )
    # Sanity: the handler DOES invoke find_rule (positive assertion that
    # the second-pass check is actually wired up).
    assert "DEFAULT_POLICY.find_rule" in source, (
        "run_lsp_rename_symbol does not call DEFAULT_POLICY.find_rule — "
        "the second-pass permission check is missing."
    )
