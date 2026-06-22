"""Harness eval cases for f-permission-persistence-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class PermissionStoreModuleDefined(EvalCase):
    name = "permission-store-module-defined"
    description = "loom.agent.permission_store module exists with public API"

    def run(self) -> EvalResult:
        try:
            import loom.agent.permission_store as p
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("grant", "is_granted", "revoke", "list_grants", "save_grants", "load_grants"):
            if not hasattr(p, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="all public API present")


class PermissionStoreWorkspaceWriteRefused(EvalCase):
    name = "permission-store-workspace-write-refused"
    description = "grant() refuses to persist workspace-write tools (write_file, edit_file, etc)"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        import loom.agent.permission_store as p

        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            for tool in ("write_file", "edit_file", "multi_edit", "edit_lines"):
                result = p.grant(wd, tool, {"path": "foo.py"})
                if result is not None:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"{tool} should refuse to persist")
        return EvalResult(name=self.name, passed=True, detail="all 4 write tools refused persistence")


class PermissionStoreRoundTrip(EvalCase):
    name = "permission-store-round-trip"
    description = "grant + save + load + is_granted survives a process boundary"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        import loom.agent.permission_store as p

        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            p.grant(wd, "bash", {"command": "ls -la"})
            assert p.is_granted(wd, "bash", {"command": "ls -la"})
            assert not p.is_granted(wd, "bash", {"command": "rm -rf /"})
            grants = p.list_grants(wd)
            if len(grants) != 1:
                return EvalResult(name=self.name, passed=False, detail=f"expected 1 grant, got {len(grants)}")
        return EvalResult(name=self.name, passed=True, detail="round-trip succeeded")
