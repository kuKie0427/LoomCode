"""Credential storage for loom multi-model providers.

Simplified 2-layer design matching opencode's approach:
  1. ``LOOM_AUTH_CONTENT`` env var — JSON override (subagent inheritance)
  2. ``~/.loom/auth.json`` — persistent file (chmod 600)

Public API:
  - ``CredentialInfo`` (frozen dataclass) — one credential entry
  - ``CredentialManager`` — read/write/remove credentials
  - ``credentials`` — module-level singleton
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


def _backup_path(path: Path, ts: int | None = None) -> Path:
    """Generate a timestamped backup filename, e.g. ``auth.bak.1712345678.json``."""
    if ts is None:
        ts = int(time.time())
    return path.with_name(f"{path.stem}.bak.{ts}{path.suffix}")


@dataclass(frozen=True)
class CredentialInfo:
    """A single provider credential.

    ``source`` records where this credential was loaded from (``"file"``
    or ``"loom_auth_content"``). It is metadata only and is NOT persisted.
    """

    provider_id: str
    api_key: str
    kind: str = "api"
    base_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    source: str | None = None


class CredentialManager:
    """Read/write/remove provider credentials.

    Priority (highest -> lowest):
      1. ``LOOM_AUTH_CONTENT`` env var (subagent inheritance override)
      2. ``~/.loom/auth.json`` file (persistent)
    """

    def __init__(self, auth_path: Path | None = None) -> None:
        self._auth_path = auth_path or (Path.home() / ".loom" / "auth.json")
        # Bumped on every set/remove so callers can cache `get()` results
        # and invalidate when credentials change (P0-3 perf fix: avoids
        # StatusBar re-reading auth.json on every render).
        self._version: int = 0

    @property
    def auth_path(self) -> Path:
        return self._auth_path

    @property
    def version(self) -> int:
        """Bumped on every mutation (set/remove). Callers can cache `get()`
        results keyed on this version and invalidate when it changes."""
        return self._version

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def get(self, provider_id: str) -> CredentialInfo | None:
        """Look up a credential — env override first, then file."""
        norm = self._normalize(provider_id)

        # 1. LOOM_AUTH_CONTENT env var
        cred = self._all_loom_auth_content().get(norm)
        if cred is not None:
            return cred

        # 2. auth.json file
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
        """Return all credentials merged from file and env override.

        ``LOOM_AUTH_CONTENT`` overlays the file so that env overrides
        take precedence.
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

        # Layer 2: LOOM_AUTH_CONTENT (overlays file)
        for provider_id, cred in self._all_loom_auth_content().items():
            result[provider_id] = cred

        return result

    def set(
        self,
        provider_id: str,
        info: CredentialInfo,
        *,
        persist: bool = True,
    ) -> None:
        """Persist a credential to auth.json."""
        if not persist:
            return
        norm = self._normalize(provider_id)
        creds = self._load_from_file()
        creds[norm] = CredentialInfo(
            provider_id=norm,
            kind=info.kind,
            api_key=info.api_key,
            base_url=info.base_url,
            metadata=dict(info.metadata),
        )
        self._save_to_file(creds)
        self._version += 1

    def remove(self, provider_id: str) -> None:
        """Delete a credential from auth.json."""
        norm = self._normalize(provider_id)
        creds = self._load_from_file()
        if norm in creds:
            del creds[norm]
            self._save_to_file(creds)
            self._version += 1

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(provider_id: str) -> str:
        return provider_id.strip().lower()

    def _load_from_file(self) -> dict[str, CredentialInfo]:
        """Load credentials from auth.json. Tolerant: missing -> {}, malformed -> {}."""
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
                continue
            api_key = entry.get("api_key")
            if not isinstance(api_key, str):
                continue
            provider_id = entry.get("provider_id", norm_key)
            if not isinstance(provider_id, str) or not provider_id:
                provider_id = norm_key
            base_url = entry.get("base_url")
            if base_url is not None and not isinstance(base_url, str):
                base_url = None
            metadata_raw = entry.get("metadata", {})
            if not isinstance(metadata_raw, dict):
                metadata_raw = {}
            metadata = {str(mk): str(mv) for mk, mv in metadata_raw.items() if isinstance(mv, str)}
            result[norm_key] = CredentialInfo(
                provider_id=self._normalize(provider_id),
                kind="api",
                api_key=api_key,
                base_url=base_url,
                metadata=metadata,
            )
        return result

    def _save_to_file(self, creds: dict[str, CredentialInfo]) -> None:
        """Atomic write of the full creds dict to auth.json (chmod 600)."""
        path = self._auth_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass

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
    # LOOM_AUTH_CONTENT (env var JSON override)
    # ------------------------------------------------------------------

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


# Module-level singleton.
credentials = CredentialManager()
