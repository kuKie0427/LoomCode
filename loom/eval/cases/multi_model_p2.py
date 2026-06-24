"""Eval cases for f-multi-model-providers-p2 (credential + UX).

Locks the contracts for CredentialManager auth.json persistence,
priority chain, atomic writes, ModelState MRU dedup+cap,
ProjectConfig upward walk, CLI auth/models commands, TUI model
picker, and model resolver precedence.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


def _check(name: str, condition: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, passed=bool(condition), detail=detail)


# ----------------------------------------------------------------------------
# CredentialManager persistence
# ----------------------------------------------------------------------------


class MultiModelCredentialManagerWrites0600(EvalCase):
    name = "multi-model-p2-credential-writes-0600"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.credential import CredentialInfo, CredentialManager

            auth_path = Path(tmp) / "auth.json"
            m = CredentialManager(auth_path=auth_path)
            info = CredentialInfo(
                provider_id="evaltest", kind="api", api_key="sk-eval"
            )
            m.set("evaltest", info)
            mode = stat.S_IMODE(auth_path.stat().st_mode)
            return _check(self.name, mode == 0o600, f"mode=0o{mode:o}")


class MultiModelCredentialManagerLoomAuthContentOverridesFile(EvalCase):
    name = "multi-model-p2-credential-loom-auth-content-overrides-file"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.credential import CredentialManager

            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "anthropic": {
                            "api_key": "file-key",
                            "kind": "api",
                            "provider_id": "anthropic",
                        }
                    }
                )
            )
            old = os.environ.get("LOOM_AUTH_CONTENT")
            os.environ["LOOM_AUTH_CONTENT"] = json.dumps(
                {"anthropic": {"api_key": "lac-key"}}
            )
            try:
                m = CredentialManager(auth_path=auth_path)
                cred = m.get("anthropic")
                ok = (
                    cred is not None
                    and cred.api_key == "lac-key"
                    and cred.source == "loom_auth_content"
                )
                return _check(
                    self.name,
                    ok,
                    f"got api_key={cred.api_key if cred else None}",
                )
            finally:
                if old is not None:
                    os.environ["LOOM_AUTH_CONTENT"] = old
                else:
                    del os.environ["LOOM_AUTH_CONTENT"]


class MultiModelCredentialManagerLoomAuthContentRead(EvalCase):
    name = "multi-model-p2-credential-loom-auth-content-read"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.credential import CredentialManager

            auth_path = Path(tmp) / "auth.json"
            old = os.environ.get("LOOM_AUTH_CONTENT")
            os.environ["LOOM_AUTH_CONTENT"] = json.dumps(
                {"openai": {"api_key": "lac-key"}}
            )
            try:
                m = CredentialManager(auth_path=auth_path)
                cred = m.get("openai")
                ok = (
                    cred is not None
                    and cred.api_key == "lac-key"
                    and cred.source == "loom_auth_content"
                )
                return _check(
                    self.name,
                    ok,
                    f"got api_key={cred.api_key if cred else None}",
                )
            finally:
                del os.environ["LOOM_AUTH_CONTENT"]
                if old is not None:
                    os.environ["LOOM_AUTH_CONTENT"] = old


class MultiModelCredentialManagerAtomicWrite(EvalCase):
    name = "multi-model-p2-credential-atomic-write"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:

            from loom.agent.credential import CredentialInfo, CredentialManager

            auth_path = Path(tmp) / "auth.json"
            m = CredentialManager(auth_path=auth_path)
            info = CredentialInfo(
                provider_id="test", kind="api", api_key="sk-key"
            )
            m.set("test", info)
            # Write must succeed (atomic = file exists with correct content)
            ok = auth_path.exists()
            if ok:
                data = json.loads(auth_path.read_text())
                ok = data.get("test", {}).get("api_key") == "sk-key"
            return _check(self.name, ok, "written" if ok else "not found")


class MultiModelCredentialManagerMalformedFileBackup(EvalCase):
    name = "multi-model-p2-credential-malformed-backup"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.credential import CredentialManager

            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text("{bad json")
            m = CredentialManager(auth_path=auth_path)
            creds = m._load_from_file()
            file_renamed = not auth_path.exists()
            has_backup = len(list(Path(tmp).glob("auth.bak.*.json"))) >= 1
            return _check(
                self.name,
                creds == {} and file_renamed and has_backup,
                f"empty={creds == {}} renamed={file_renamed}",
            )


# ----------------------------------------------------------------------------
# ModelState
# ----------------------------------------------------------------------------


class MultiModelModelStateRecentDedupAndCap(EvalCase):
    name = "multi-model-p2-modelstate-recent-dedup-cap"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.model_state import ModelRef, ModelState

            ms = ModelState(Path(tmp))
            ms.add_recent("a", "m1")
            ms.add_recent("b", "m2")
            ms.add_recent("a", "m1")  # dedupe + bump
            recent = ms.recent()
            if len(recent) != 2:
                return _check(self.name, False, f"len={len(recent)}")
            if recent[0] != ModelRef("a", "m1"):
                return _check(self.name, False, "first not a/m1")
            if recent[1] != ModelRef("b", "m2"):
                return _check(self.name, False, "second not b/m2")
            # Cap at 10
            for i in range(15):
                ms.add_recent("p", f"m{i}")
            return _check(
                self.name,
                len(ms.recent()) == 10,
                f"capped len={len(ms.recent())}",
            )


# ----------------------------------------------------------------------------
# ProjectConfig
# ----------------------------------------------------------------------------


class MultiModelProjectConfigLocalFileRead(EvalCase):
    name = "multi-model-p2-projectconfig-local-read"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.model_state import ProjectConfig

            workdir = Path(tmp)
            (workdir / ".minicode").mkdir()
            (workdir / ".minicode" / "config.json").write_text(
                json.dumps({"model": "anthropic/claude-sonnet-4-5"})
            )
            pc = ProjectConfig(workdir)
            return _check(
                self.name,
                pc.model == "anthropic/claude-sonnet-4-5",
                f"model={pc.model!r}",
            )


class MultiModelProjectConfigUpwardWalkFallback(EvalCase):
    name = "multi-model-p2-projectconfig-upward-walk"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmp:
            from loom.agent.model_state import ProjectConfig

            workdir = Path(tmp)
            (workdir / ".minicode").mkdir()
            (workdir / ".minicode" / "config.json").write_text(
                json.dumps({"model": "deepseek/deepseek-chat"})
            )
            sub = workdir / "deep" / "nested"
            sub.mkdir(parents=True)
            pc = ProjectConfig(sub)
            return _check(
                self.name,
                pc.model == "deepseek/deepseek-chat",
                f"model={pc.model!r}",
            )


# ----------------------------------------------------------------------------
# CLI commands
# ----------------------------------------------------------------------------


class MultiModelCliModelsCommandListsAllProviders(EvalCase):
    name = "multi-model-p2-cli-models-lists-all-providers"

    def run(self) -> EvalResult:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "loom.cli", "models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        ok = result.returncode == 0
        # Must list at least 3 providers
        for p in ("anthropic", "openai", "deepseek"):
            if p not in result.stdout:
                ok = False
        return _check(
            self.name, ok, f"stdout[:200]={result.stdout[:200]!r}"
        )


class MultiModelCliAuthLoginPromptsAndWritesFile(EvalCase):
    name = "multi-model-p2-cli-auth-login-writes-file"

    def run(self) -> EvalResult:
        # getpass.getpass reads from /dev/tty, not stdin, so we must call
        # cli.main() directly with a mock instead of using subprocess.
        import getpass as getpass_mod

        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch

            tmp_home = Path(tmp)
            old_depth = os.environ.get("LOOP_CALL_DEPTH")
            os.environ.pop("LOOP_CALL_DEPTH", None)
            try:
                with (
                    patch("pathlib.Path.home", return_value=tmp_home),
                    patch.object(getpass_mod, "getpass", return_value="sk-eval-key"),
                ):
                    from loom.cli import main

                    exit_code = main(
                        ["auth", "login", "eval-provider"]
                    )
                    if exit_code != 0:
                        return _check(
                            self.name,
                            False,
                            f"exit_code={exit_code}",
                        )
                    auth_path = tmp_home / ".loom" / "auth.json"
                    if not auth_path.exists():
                        return _check(
                            self.name,
                            False,
                            f"auth.json not found at {auth_path}",
                        )
                    data = json.loads(auth_path.read_text())
                    ok = data.get("eval-provider", {}).get("api_key") == "sk-eval-key"
                    return _check(self.name, ok, f"data={data}")
            finally:
                if old_depth is not None:
                    os.environ["LOOP_CALL_DEPTH"] = old_depth
                else:
                    os.environ.pop("LOOP_CALL_DEPTH", None)


class MultiModelCliAuthLogoutRemovesEntry(EvalCase):
    name = "multi-model-p2-cli-auth-logout-removes-entry"

    def run(self) -> EvalResult:
        import getpass as getpass_mod

        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch

            tmp_home = Path(tmp)
            old_depth = os.environ.get("LOOP_CALL_DEPTH")
            os.environ.pop("LOOP_CALL_DEPTH", None)
            try:
                with (
                    patch("pathlib.Path.home", return_value=tmp_home),
                    patch.object(getpass_mod, "getpass", return_value="sk-remove-key"),
                ):
                    from loom.cli import main

                    # Login first
                    login_code = main(
                        ["auth", "login", "to-remove"]
                    )
                    if login_code != 0:
                        return _check(
                            self.name,
                            False,
                            f"login failed: exit_code={login_code}",
                        )
                    # Then logout
                    logout_code = main(
                        ["auth", "logout", "to-remove"]
                    )
                    if logout_code != 0:
                        return _check(
                            self.name,
                            False,
                            f"logout failed: exit_code={logout_code}",
                        )
                    auth_path = tmp_home / ".loom" / "auth.json"
                    if auth_path.exists():
                        data = json.loads(auth_path.read_text())
                        if "to-remove" in data:
                            return _check(
                                self.name, False, "entry still present"
                            )
                    return _check(self.name, True, "removed")
            finally:
                if old_depth is not None:
                    os.environ["LOOP_CALL_DEPTH"] = old_depth
                else:
                    os.environ.pop("LOOP_CALL_DEPTH", None)


# ----------------------------------------------------------------------------
# Model resolver
# ----------------------------------------------------------------------------


class MultiModelResolverPrecedenceCliOverConfig(EvalCase):
    name = "multi-model-p2-resolver-precedence-cli-over-config"

    def run(self) -> EvalResult:
        from loom.agent.model_resolver import resolve_model

        result = resolve_model(
            Path("."),
            cli_model="anthropic/claude-sonnet-4-5",
            config_model="openai/gpt-4o",
        )
        return _check(
            self.name,
            result == "anthropic/claude-sonnet-4-5",
            f"result={result!r}",
        )


class MultiModelResolverFallsThroughToDefault(EvalCase):
    name = "multi-model-p2-resolver-falls-through-to-default"

    def run(self) -> EvalResult:
        from loom.agent.model_resolver import resolve_model

        result = resolve_model(Path("."))
        ok = result is not None and "/" in result
        return _check(self.name, ok, f"result={result!r}")


class MultiModelResolverWiredIntoAgentTUIApp(EvalCase):
    """Locks: AgentTUIApp.__init__ calls resolve_model() instead of hardcoding."""
    name = "multi-model-p2-resolver-wired-into-tui"
    def run(self) -> EvalResult:
        from pathlib import Path
        app_py = (Path(__file__).parent.parent.parent / "tui" / "app.py").resolve()
        src = app_py.read_text()
        # Must import resolve_model
        ok = "from loom.agent.model_resolver import resolve_model" in src
        # Must consume ProjectConfig
        ok = ok and "ProjectConfig(WORKDIR).model" in src
        # Must NOT have hardcoded "deepseek-v4-flash" fallback
        ok = ok and '"deepseek-v4-flash"' not in src
        return _check(self.name, ok, "resolve_model + ProjectConfig + no hardcoded fallback")


class MultiModelModelChangePersistsDefault(EvalCase):
    """Locks: _on_model_picked calls ModelState.set_default."""
    name = "multi-model-p2-model-change-persists-default"
    def run(self) -> EvalResult:
        from pathlib import Path
        app_py = (Path(__file__).parent.parent.parent / "tui" / "app.py").resolve()
        src = app_py.read_text()
        # _on_model_picked must call set_default
        ok = "ms.set_default(provider_id, model_id)" in src
        return _check(self.name, ok, "set_default called in _on_model_picked")
