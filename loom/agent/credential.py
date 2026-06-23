"""Credential storage for loom multi-model providers.

Manages API keys for providers (anthropic / openai / deepseek / ollama /
openrouter) with a layered lookup that lets users (and subagents) override
credentials in multiple ways. Inspired by opencode's ``auth/index.ts`` but
adds OS keyring as the highest-priority layer (the rest of loom follows the
same precedence: most explicit -> least explicit).

Public API:
  - ``CredentialInfo`` (frozen dataclass) — one credential entry
  - ``CredentialManager`` — read/write/remove credentials
  - ``credentials`` — module-level singleton used by subagent inheritance (P3)

Priority chain (highest -> lowest):
  1. OS keyring (``keyring.get_password("loom", provider_id)``) — only if
     ``use_keyring=True`` AND the ``keyring`` package is importable AND no
     ``KeyringError`` is raised.
  2. ``LOOM_AUTH_CONTENT`` env var — JSON blob like
     ``{"anthropic": {"api_key": "sk-..."}}``. Designed for subagent
     inheritance: a parent agent can inject credentials into a child
     without writing to disk or the user shell.
  3. Per-provider env var (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
     ``DEEPSEEK_API_KEY``). The env var name comes from
     ``provider.env_var`` via ``get_provider(provider_id)``.
  4. ``~/.loom/auth.json`` — persistent file written by ``loom auth login``.

The ``source`` field on ``CredentialInfo`` records which layer a particular
``CredentialInfo`` came from. ``set()`` writes the credential to the file
with ``source=None`` — the source is re-derived on every ``get()`` /
``all()`` call.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    import keyring
    from keyring.errors import KeyringError

    _HAS_KEYRING = True
else:
    try:
        import keyring
        from keyring.errors import KeyringError

        _HAS_KEYRING = True
    except ImportError:  # pragma: no cover - exercised only when keyring absent
        _HAS_KEYRING = False
        keyring = None  # type: ignore[assignment]
        KeyringError = Exception  # type: ignore[assignment,misc]


def _backup_path(path: Path, ts: int | None = None) -> Path:
    """Generate a timestamped backup filename, e.g. ``auth.bak.1712345678.json``."""
    if ts is None:
        ts = int(time.time())
    return path.with_name(f"{path.stem}.bak.{ts}{path.suffix}")


@dataclass(frozen=True)
class CredentialInfo:
    """A single provider credential.

    ``source`` records where this credential was loaded from. It is
    ``None`` for credentials produced by ``set()`` callers and one of
    ``"keyring"``, ``"env"``, ``"file"``, or ``"loom_auth_content"`` for
    credentials returned from ``get()`` / ``all()``. The ``loom auth list``
    UX (Task 4) displays this field as the SOURCE column.

    ``kind`` is a general string (default ``"api"``). At runtime only
    ``"api"`` is supported; other values produce a warning.

    Note: ``source`` is metadata only and is NOT persisted to
    ``auth.json``; it is re-derived on every read.
    """

    provider_id: str
    api_key: str
    kind: str = "api"
    base_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    source: str | None = None


class CredentialManager:
    """Read/write/remove provider credentials with a layered priority chain.

    See the module docstring for the full priority chain. In short:
    keyring > LOOM_AUTH_CONTENT > per-provider env var > auth.json file.

    The file at ``auth_path`` stores ALL providers in a single JSON
    object (one key per provider_id), not per-provider files. The
    ``auth_path_for_provider`` helper is a convenience for future
    extension; today it just returns ``self.auth_path``.
    """

    _KEYRING_SERVICE = "loom"

    def __init__(
        self,
        auth_path: Path | None = None,
        *,
        use_keyring: bool = True,
    ) -> None:
        # Default: ~/.loom/auth.json (NOT XDG — loom uses its own namespace).
        self._auth_path = auth_path or (Path.home() / ".loom" / "auth.json")
        self._use_keyring = use_keyring

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def auth_path(self) -> Path:
        """The path to the auth.json file."""
        return self._auth_path

    def auth_path_for_provider(self, provider_id: str) -> Path:
        """Path to this provider's credential storage.

        Today, all providers share a single file. The helper exists so
        future extensions (e.g. per-provider files) can change the return
        value in one place.
        """
        del provider_id  # currently unused; reserved for future per-provider files
        return self._auth_path

    def get(self, provider_id: str) -> CredentialInfo | None:
        """Look up a credential using the priority chain.

        Priority (highest -> lowest):
          1. OS keyring (``use_keyring=True`` AND importable AND no error)
          2. ``LOOM_AUTH_CONTENT`` env var (subagent inheritance)
          3. Per-provider env var (from ``provider.env_var``)
          4. ``auth.json`` file

        Returns ``None`` if no layer produced a credential. The returned
        ``CredentialInfo.source`` records which layer won.
        """
        norm = self._normalize(provider_id)

        # 1. OS keyring
        if self._use_keyring and _HAS_KEYRING:
            api_key = self._try_keyring_get(norm)
            if api_key:
                return CredentialInfo(
                    provider_id=norm,
                    kind="api",
                    api_key=api_key,
                    source="keyring",
                )

        # 2. LOOM_AUTH_CONTENT env var (subagent inheritance override)
        cred = self._lookup_loom_auth_content(norm)
        if cred is not None:
            return cred

        # 3. Per-provider env var
        cred = self._lookup_provider_env(norm)
        if cred is not None:
            return cred

        # 4. auth.json file
        file_creds = self._load_from_file()
        if norm in file_creds:
            stored = file_creds[norm]
            return CredentialInfo(
                provider_id=stored.provider_id,
                kind=stored.kind,
                api_key=stored.api_key,
                base_url=stored.base_url,
                metadata=dict(stored.metadata),
                source="file",
            )

        return None

    def all(self) -> dict[str, CredentialInfo]:
        """Return every known provider's credential, merged across layers.

        Merge order (each layer overlays the previous one):
          - Start with ``auth.json`` (source="file")
          - Overlay per-provider env vars (source="env")
          - Overlay ``LOOM_AUTH_CONTENT`` (source="loom_auth_content")
          - Top priority: OS keyring entries (source="keyring")

        This ordering matches ``get()`` so that ``all()[provider_id]``
        returns the same ``CredentialInfo`` as ``get(provider_id)`` for
        any provider, regardless of which layers are populated.
        """
        result: dict[str, CredentialInfo] = {}

        # Layer 1: file
        for key, stored in self._load_from_file().items():
            result[key] = CredentialInfo(
                provider_id=stored.provider_id,
                kind=stored.kind,
                api_key=stored.api_key,
                base_url=stored.base_url,
                metadata=dict(stored.metadata),
                source="file",
            )

        # Layer 2: per-provider env vars
        for provider_id in self._known_provider_ids():
            cred = self._lookup_provider_env(provider_id)
            if cred is not None:
                result[provider_id] = cred

        # Layer 3: LOOM_AUTH_CONTENT
        for provider_id, cred in self._all_loom_auth_content().items():
            result[provider_id] = cred

        # Layer 4 (top): OS keyring
        if self._use_keyring and _HAS_KEYRING:
            for provider_id in self._known_provider_ids():
                api_key = self._try_keyring_get(provider_id)
                if api_key:
                    result[provider_id] = CredentialInfo(
                        provider_id=provider_id,
                        kind="api",
                        api_key=api_key,
                        source="keyring",
                    )

        return result

    def set(
        self,
        provider_id: str,
        info: CredentialInfo,
        *,
        persist: bool = True,
    ) -> None:
        """Persist a credential to keyring (if enabled) and auth.json.

        ``info.source`` is ignored on write — the source is re-derived
        on every read. ``info.provider_id`` should match ``provider_id``;
        the function arg is the canonical one used for storage.
        """
        if not persist:
            return
        norm = self._normalize(provider_id)

        if self._use_keyring and _HAS_KEYRING and info.api_key:
            self._try_keyring_set(norm, info.api_key)

        creds = self._load_from_file()
        creds[norm] = CredentialInfo(
            provider_id=norm,
            kind=info.kind,
            api_key=info.api_key,
            base_url=info.base_url,
            metadata=dict(info.metadata),
        )
        self._save_to_file(creds)

    def remove(self, provider_id: str) -> None:
        """Delete a credential from keyring (if enabled) and auth.json."""
        norm = self._normalize(provider_id)

        if self._use_keyring and _HAS_KEYRING:
            self._try_keyring_delete(norm)

        creds = self._load_from_file()
        if norm in creds:
            del creds[norm]
            self._save_to_file(creds)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(provider_id: str) -> str:
        return provider_id.strip().lower()

    def _load_from_file(self) -> dict[str, CredentialInfo]:
        """Load credentials from auth.json. Tolerant: missing -> {}, malformed -> {}.

        On malformed JSON: back up the bad file to
        ``<path>.bak.<timestamp>.json`` (timestamped to avoid clobbering
        an existing backup), log a warning, and return an empty dict.
        Schema-invalid entries are skipped individually with a warning.
        """
        path = self._auth_path
        if not path.exists():
            return {}

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            backup = _backup_path(path)
            try:
                path.rename(backup)
                logger.warning(
                    f"credentials file malformed; backed up to {backup.name}: {exc}"
                )
            except OSError as backup_exc:
                logger.warning(
                    f"credentials file malformed AND could not back up: "
                    f"{exc}; backup error: {backup_exc}"
                )
            return {}

        if not isinstance(raw, dict):
            logger.warning("credentials file root is not a dict; treating as empty")
            return {}

        result: dict[str, CredentialInfo] = {}
        for key, entry in raw.items():
            norm_key = self._normalize(str(key))
            if not isinstance(entry, dict):
                logger.warning(
                    f"credentials entry {key!r} is not a dict; skipping"
                )
                continue
            provider_id = entry.get("provider_id", norm_key)
            if not isinstance(provider_id, str) or not provider_id:
                provider_id = norm_key
            api_key = entry.get("api_key")
            if not isinstance(api_key, str):
                logger.warning(
                    f"credentials entry {key!r} missing string api_key; skipping"
                )
                continue
            base_url = entry.get("base_url")
            if base_url is not None and not isinstance(base_url, str):
                base_url = None
            kind_raw = entry.get("kind", "api")
            if kind_raw != "api":
                logger.warning(
                    f"credentials entry {key!r} has unsupported kind="
                    f"{kind_raw!r}; skipping (only 'api' is supported)"
                )
                continue
            metadata_raw = entry.get("metadata", {})
            if not isinstance(metadata_raw, dict):
                metadata_raw = {}
            metadata = {str(mk): str(mv) for mk, mv in metadata_raw.items()}
            result[norm_key] = CredentialInfo(
                provider_id=self._normalize(provider_id),
                kind="api",
                api_key=api_key,
                base_url=base_url,
                metadata=metadata,
            )
        return result

    def _save_to_file(self, creds: dict[str, CredentialInfo]) -> None:
        """Atomic write of the full creds dict to auth.json.

        Uses ``tempfile.mkstemp`` + ``os.replace`` for true atomicity
        (write-to-temp then rename). Chmods the file to 0o600 and the
        containing directory to 0o700. Raises on write error after
        cleaning up the temp file.
        """
        path = self._auth_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError as exc:
            # Non-fatal: dir may already be 0o700 or owned by another user.
            logger.debug(f"could not chmod dir {path.parent} to 0o700: {exc}")

        payload: dict[str, dict[str, object]] = {}
        for k, v in creds.items():
            entry: dict[str, object] = {
                "provider_id": v.provider_id,
                "kind": v.kind,
                "api_key": v.api_key,
            }
            if v.base_url is not None:
                entry["base_url"] = v.base_url
            if v.metadata:
                entry["metadata"] = dict(v.metadata)
            payload[k] = entry

        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Keyring helpers (all errors swallowed — keyring is best-effort)
    # ------------------------------------------------------------------

    def _try_keyring_get(self, provider_id: str) -> str | None:
        if not _HAS_KEYRING:
            return None
        try:
            return keyring.get_password(self._KEYRING_SERVICE, provider_id)
        except KeyringError as exc:
            logger.debug(f"keyring.get_password failed for {provider_id!r}: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - any backend failure
            logger.debug(f"keyring.get_password unexpected error for {provider_id!r}: {exc}")
            return None

    def _try_keyring_set(self, provider_id: str, api_key: str) -> None:
        if not _HAS_KEYRING:
            return
        try:
            keyring.set_password(self._KEYRING_SERVICE, provider_id, api_key)
        except KeyringError as exc:
            logger.warning(
                f"keyring.set_password failed for {provider_id!r}: {exc} "
                "(credential not stored in OS keyring; will fall back to file)"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"keyring.set_password unexpected error for {provider_id!r}: {exc}"
            )

    def _try_keyring_delete(self, provider_id: str) -> None:
        if not _HAS_KEYRING:
            return
        try:
            keyring.delete_password(self._KEYRING_SERVICE, provider_id)
        except KeyringError as exc:
            # PasswordDeleteError is fine — means it wasn't there.
            logger.debug(f"keyring.delete_password for {provider_id!r}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"keyring.delete_password unexpected error for {provider_id!r}: {exc}"
            )

    # ------------------------------------------------------------------
    # LOOM_AUTH_CONTENT (env var JSON override)
    # ------------------------------------------------------------------

    def _lookup_loom_auth_content(self, provider_id: str) -> CredentialInfo | None:
        norm = self._normalize(provider_id)
        return self._all_loom_auth_content().get(norm)

    def _all_loom_auth_content(self) -> dict[str, CredentialInfo]:
        raw = os.environ.get("LOOM_AUTH_CONTENT")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"LOOM_AUTH_CONTENT is not valid JSON: {exc}")
            return {}
        if not isinstance(data, dict):
            return {}
        result: dict[str, CredentialInfo] = {}
        for key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            api_key = entry.get("api_key")
            if not isinstance(api_key, str) or not api_key:
                continue
            base_url = entry.get("base_url")
            if base_url is not None and not isinstance(base_url, str):
                base_url = None
            norm = self._normalize(str(key))
            result[norm] = CredentialInfo(
                provider_id=norm,
                kind="api",
                api_key=api_key,
                base_url=base_url,
                source="loom_auth_content",
            )
        return result

    # ------------------------------------------------------------------
    # Per-provider env var lookup
    # ------------------------------------------------------------------

    def _lookup_provider_env(self, provider_id: str) -> CredentialInfo | None:
        env_var = self._provider_env_var(provider_id)
        if not env_var:
            return None
        api_key = os.environ.get(env_var)
        if not api_key:
            return None
        # Convention: provider may also expose <env_var>_BASE_URL for
        # self-hosted gateways (e.g. ANTHROPIC_API_KEY_BASE_URL).
        base_url = os.environ.get(f"{env_var}_BASE_URL")
        return CredentialInfo(
            provider_id=provider_id,
            kind="api",
            api_key=api_key,
            base_url=base_url,
            source="env",
        )

    @staticmethod
    def _provider_env_var(provider_id: str) -> str:
        # Lazy import to avoid a hard dependency on the providers package
        # at module import time.
        try:
            from loom.agent.providers import get_provider
        except ImportError:
            return ""
        try:
            provider = get_provider(provider_id)
        except Exception:  # noqa: BLE001 - unknown provider is fine
            return ""
        return getattr(provider, "env_var", "") or ""

    @staticmethod
    def _known_provider_ids() -> list[str]:
        try:
            from loom.agent.providers import PROVIDERS
        except ImportError:
            return []
        return list(PROVIDERS.keys())


# Module-level singleton — P3 subagent inheritance uses this.
# Tests and CLI code may construct their own CredentialManager with
# a custom auth_path (e.g. tmp_path) without touching the singleton.
credentials = CredentialManager()
