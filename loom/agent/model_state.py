"""Per-project and global model state for loom multi-model providers.

Tracks:

- ``ModelRef`` — a frozen (provider_id, model_id) reference
- ``ModelState`` — global MRU recent-models list + a separate "default"
  field, persisted to ``<workdir>/.minicode/state/model.json``
- ``ProjectConfig`` — per-project ``.minicode/config.json``; currently
  carries a single ``model`` field, looked up by walking upward from
  ``workdir`` (stopping at ``Path.home()``)

Files are written atomically (``tempfile.mkstemp`` + ``os.replace``) and
chmod'd to ``0o600`` (file) / ``0o700`` (directory) for safety. Malformed
JSON is backed up to ``<path>.bak.<ts>.json`` and treated as empty —
state is best-effort and never blocks the agent loop.

Public API:
  - ``ModelRef`` (frozen dataclass) — ``provider_id`` + ``model_id``
  - ``ModelState`` — ``recent()`` / ``add_recent()`` / ``default_model()``
    / ``set_default()``
  - ``ProjectConfig`` — ``model`` attribute + ``save()``
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

# Maximum number of recent models to keep in the MRU list.
_MAX_RECENT = 10

# The .minicode subdirectory under which global state lives.
_STATE_SUBDIR = "state"
_STATE_FILENAME = "model.json"

# The .minicode config filename at the project level.
_CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class ModelRef:
    """A (provider_id, model_id) reference.

    Frozen so instances are hashable and usable in sets / dict keys.
    The ``provider_id`` and ``model_id`` are stored verbatim — the
    caller is responsible for any normalization. ``str(ref)`` returns
    ``"provider_id/model_id"`` (the wire format used by
    ``parse_model_id``).
    """

    provider_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def to_dict(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "model_id": self.model_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelRef:
        """Build a ModelRef from a dict. Tolerant of missing keys."""
        provider = d.get("provider_id", "")
        model = d.get("model_id", "")
        if not isinstance(provider, str):
            provider = str(provider)
        if not isinstance(model, str):
            model = str(model)
        return cls(provider_id=provider, model_id=model)


# ---------------------------------------------------------------------------
# ModelState — global recent + default, persisted to <workdir>/.minicode/state/model.json
# ---------------------------------------------------------------------------


class ModelState:
    """Global model state: MRU recent list + a separate "default" pointer.

    Stored at ``<workdir>/.minicode/state/model.json`` as:

        {
          "recent": [{"provider_id": "anthropic", "model_id": "claude-sonnet-4-5"}, ...],
          "default": "anthropic/claude-sonnet-4-5" | null
        }

    The ``recent`` list is MRU-ordered: ``add_recent`` de-dupes by
    (provider_id, model_id) and bumps the entry to position 0. Capped
    at ``_MAX_RECENT`` (10) entries.

    The ``default`` field is a separate string field — it can outlive
    a model falling out of the recent list.
    """

    def __init__(self, workdir: Path) -> None:
        self._state_path = workdir / ".minicode" / _STATE_SUBDIR / _STATE_FILENAME
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._state_path.parent, 0o700)
        except OSError as exc:
            # Non-fatal: dir may already be 0o700 or owned by another user.
            logger.debug(f"could not chmod dir {self._state_path.parent} to 0o700: {exc}")

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        """The path to the model.json state file."""
        return self._state_path

    def recent(self, max: int = 10) -> list[ModelRef]:
        """Return up to ``max`` most-recently-used models, MRU-ordered (head is newest).

        The cap is the smaller of the caller-supplied ``max`` and the
        stored recent-list size. The returned list is a fresh copy;
        mutating it does not affect on-disk state.
        """
        data = self._load()
        recent_raw = data.get("recent", [])
        if not isinstance(recent_raw, list):
            recent_raw = []
        result: list[ModelRef] = []
        for entry in recent_raw[:max]:
            if not isinstance(entry, dict):
                logger.warning(f"model state: skipping non-dict recent entry: {entry!r}")
                continue
            try:
                result.append(ModelRef.from_dict(entry))
            except Exception as exc:  # noqa: BLE001 - defensive
                logger.warning(f"model state: skipping invalid recent entry: {exc}")
        return result

    def add_recent(self, provider_id: str, model_id: str) -> None:
        """Add (provider_id, model_id) to the MRU list at position 0.

        If the pair is already present, it is moved to position 0 (de-dupe +
        bump). The list is capped at ``_MAX_RECENT`` (10) entries — the
        oldest tail entries are dropped.
        """
        data = self._load()
        recent_raw = data.get("recent", [])
        if not isinstance(recent_raw, list):
            recent_raw = []

        # Filter out the existing entry (if any) to de-dupe + bump.
        filtered: list[dict[str, str]] = []
        for entry in recent_raw:
            if not isinstance(entry, dict):
                continue
            ref = ModelRef.from_dict(entry)
            if ref.provider_id == provider_id and ref.model_id == model_id:
                continue
            filtered.append({"provider_id": ref.provider_id, "model_id": ref.model_id})

        # Prepend the new entry.
        filtered.insert(0, {"provider_id": provider_id, "model_id": model_id})

        # Cap at _MAX_RECENT (drop oldest tail).
        if len(filtered) > _MAX_RECENT:
            filtered = filtered[:_MAX_RECENT]

        data["recent"] = filtered
        self._save(data)

    def default_model(self) -> str | None:
        """Return the user's default model as ``"provider_id/model_id"`` or None."""
        data = self._load()
        default = data.get("default")
        if not isinstance(default, str) or not default:
            return None
        return default

    def set_default(self, provider_id: str, model_id: str) -> None:
        """Set the default model. Persisted as ``"provider_id/model_id"``."""
        data = self._load()
        data["default"] = f"{provider_id}/{model_id}"
        self._save(data)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        """Load state from disk. Tolerant: missing/malformed -> default empty.

        Default structure: ``{"recent": [], "default": None}``. On
        malformed JSON: back up the bad file to
        ``<path>.bak.<timestamp>.json`` and return the default structure.
        Schema-invalid entries inside ``recent`` are filtered by
        ``recent()`` itself, not here.
        """
        path = self._state_path
        if not path.exists():
            return {"recent": [], "default": None}

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            backup = path.with_name(f"{path.name}.bak.{int(time.time())}.json")
            try:
                path.rename(backup)
                logger.warning(
                    f"model state file malformed; backed up to {backup.name}: {exc}"
                )
            except OSError as backup_exc:
                logger.warning(
                    f"model state file malformed AND could not back up: "
                    f"{exc}; backup error: {backup_exc}"
                )
            return {"recent": [], "default": None}

        if not isinstance(raw, dict):
            logger.warning("model state file root is not a dict; treating as empty")
            return {"recent": [], "default": None}

        # Normalize shape: top-level must have "recent" (list) and "default" (str | None).
        recent = raw.get("recent", [])
        if not isinstance(recent, list):
            recent = []
        default = raw.get("default")
        if default is not None and not isinstance(default, str):
            default = None
        return {"recent": recent, "default": default}

    def _save(self, data: dict[str, Any]) -> None:
        """Atomic write of the state dict to model.json.

        Uses ``tempfile.mkstemp`` + ``os.replace`` for true atomicity.
        Chmods the file to 0o600 and the parent directory to 0o700.
        Raises on write error after cleaning up the temp file.
        """
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError as exc:
            logger.debug(f"could not chmod dir {path.parent} to 0o700: {exc}")

        payload: dict[str, Any] = {
            "recent": list(data.get("recent", [])),
            "default": data.get("default"),
        }

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


# ---------------------------------------------------------------------------
# ProjectConfig — per-project .minicode/config.json with upward walk
# ---------------------------------------------------------------------------


class ProjectConfig:
    """Per-project ``.minicode/config.json`` with upward-walk lookup.

    The constructor reads ``<workdir>/.minicode/config.json``. If that
    file does not exist, walks upward through parent directories
    (stopping at ``Path.home()``) looking for the next
    ``.minicode/config.json``. If still not found, ``model`` is
    ``None``.

    ``save()`` writes atomically to the LOCATED path. If no
    ``config.json`` was found by the upward walk, ``save()`` writes to
    ``<workdir>/.minicode/config.json`` (creating the directory).

    Schema (P2): ``{"model": "provider_id/model_id" | null}``. Future
    fields (e.g. ``disabled_tools``) are tolerated but unused.
    """

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._located_path: Path | None = self._find_config(workdir)
        self.model: str | None = self._read_model(self._located_path)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def config_path(self) -> Path | None:
        """The config.json path that was found (None if not found by upward walk)."""
        return self._located_path

    def save(self) -> None:
        """Atomic write of the current ``model`` field to the located config.json.

        If no config was found by the upward walk, writes to
        ``<workdir>/.minicode/config.json`` and updates ``config_path``.
        """
        target = self._located_path
        if target is None:
            target = self._workdir / ".minicode" / _CONFIG_FILENAME
            self._located_path = target

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError as exc:
            logger.debug(f"could not chmod dir {target.parent} to 0o700: {exc}")

        payload: dict[str, Any] = {"model": self.model}

        fd, tmp = tempfile.mkstemp(
            dir=target.parent, prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_config(start: Path) -> Path | None:
        """Walk upward from ``start`` until ``.minicode/config.json`` is found.

        The walk stops at ``Path.home()`` (inclusive) — we do NOT walk
        above the user's home directory (privacy + path-bug safety:
        a symlink above home would cause the walk to escape). Returns
        the first existing ``<dir>/.minicode/config.json``, or ``None``
        if none is found.
        """
        home = Path.home().resolve()
        current = start.resolve()
        if current.is_file():
            current = current.parent

        # Walk from current up to and including home.
        while True:
            candidate = current / ".minicode" / _CONFIG_FILENAME
            if candidate.is_file():
                return candidate
            if current == home:
                return None
            parent = current.parent
            if parent == current:
                # Reached filesystem root without hitting home — defensive.
                return None
            current = parent

    @staticmethod
    def _read_model(path: Path | None) -> str | None:
        """Read the ``model`` field from a config.json path. Tolerant.

        Returns ``None`` if the path is ``None``, the file is missing,
        the JSON is malformed, the root is not a dict, or the ``model``
        field is missing/invalid.
        """
        if path is None:
            return None
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"project config file malformed; treating as empty: {exc}")
            return None

        if not isinstance(raw, dict):
            logger.warning("project config file root is not a dict; treating as empty")
            return None

        model = raw.get("model")
        if model is None:
            return None
        if not isinstance(model, str) or not model:
            logger.warning(f"project config: invalid 'model' field {model!r}; ignoring")
            return None
        return model
