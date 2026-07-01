"""Session history store — multi-session persistence for the loom agent.

Stores each session as an independent JSON file under
``<workdir>/.minicode/sessions/<session_id>.json``, with a lightweight
``index.json`` holding metadata for the session list UI.

File layout::

    .minicode/sessions/
    ├── index.json              # list of SessionMeta dicts, MRU-sorted
    ├── <session_id>.json       # full checkpoint payload for one session
    └── ...

Each session file has the same shape as ``checkpoint.json`` plus a
``session_id``, ``session_name``, and ``created_at`` field.  This
mirrors the existing ``checkpoint`` module so loading code can be
shared.

Public API:
  - ``SessionMeta`` (frozen dataclass) — id, name, created_at, updated_at,
    message_count, tool_call_count, model
  - ``SessionStore`` — create / save / load / list / delete / rename
  - ``generate_session_name`` — LLM-powered one-line summary of the
    first user message
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from loom.agent.context import Context
    from loom.agent.llm import LLMClient

def _json_default(obj: Any) -> Any:
    """JSON default encoder — serializes dataclass blocks (TextBlock, ToolUseBlock, etc.) to dicts.

    Without this, ``json.dump(..., default=str)`` turns content blocks into
    unreadable strings like ``"TextBlock(type='text', text='Hello')"``, making
    session files impossible to replay.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return str(obj)


_SESSIONS_SUBDIR = "sessions"
_INDEX_FILENAME = "index.json"
_MAX_SESSIONS = 50  # cap to avoid unbounded growth


@dataclass(frozen=True)
class SessionMeta:
    """Lightweight metadata for one session, stored in index.json."""

    session_id: str
    name: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    message_count: int
    tool_call_count: int
    model: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionMeta:
        """Tolerant constructor — missing/bad fields get sane defaults."""

        def _int(v: Any) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        return cls(
            session_id=str(d.get("session_id", "")),
            name=str(d.get("name", "Untitled")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            message_count=_int(d.get("message_count", 0)),
            tool_call_count=_int(d.get("tool_call_count", 0)),
            model=str(d.get("model", "")),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_session_id() -> str:
    """Generate a 12-char hex session id (matches trace.py session_id format)."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class SessionStore:
    """Manages session files under ``<workdir>/.minicode/sessions/``.

    All operations are best-effort: a failed disk write logs a warning
    but never raises.  This matches the project convention that state
    persistence must not block the agent loop.
    """

    def __init__(self, workdir: Path) -> None:
        self._dir = workdir / ".minicode" / _SESSIONS_SUBDIR
        self._index_path = self._dir / _INDEX_FILENAME
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._dir, 0o700)
        except OSError as exc:
            logger.debug(f"could not chmod dir {self._dir} to 0o700: {exc}")

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    # ------------------------------------------------------------------
    # Index load / save
    # ------------------------------------------------------------------

    def _load_index(self) -> list[SessionMeta]:
        """Load the session index, MRU-sorted (newest updated_at first)."""
        if not self._index_path.exists():
            return []
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("sessions index malformed; treating as empty: {}", exc)
            return []
        if not isinstance(raw, list):
            return []
        metas = [SessionMeta.from_dict(item) for item in raw if isinstance(item, dict)]
        # Sort by updated_at descending (most recent first).
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def _save_index(self, metas: list[SessionMeta]) -> None:
        """Atomically write the index file."""
        payload = [m.to_dict() for m in metas]
        try:
            fd, tmp = tempfile.mkstemp(
                dir=self._dir, prefix=_INDEX_FILENAME + ".", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._index_path)
        except Exception as exc:
            logger.warning("failed to save sessions index: {}", exc)
            try:
                os.unlink(tmp)
            except (OSError, UnboundLocalError):
                pass

    def _upsert_index(self, meta: SessionMeta) -> None:
        """Insert or update ``meta`` in the index, capping at _MAX_SESSIONS."""
        metas = self._load_index()
        # Remove existing entry with the same session_id (if any).
        metas = [m for m in metas if m.session_id != meta.session_id]
        metas.insert(0, meta)  # newest first
        # Cap the list; oldest excess entries are pruned.
        if len(metas) > _MAX_SESSIONS:
            pruned = metas[_MAX_SESSIONS:]
            metas = metas[:_MAX_SESSIONS]
            # Delete the pruned session files to avoid orphaned data.
            for m in pruned:
                p = self._session_path(m.session_id)
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        self._save_index(metas)

    # ------------------------------------------------------------------
    # Public CRUD API
    # ------------------------------------------------------------------

    def create_session(self, name: str | None = None) -> str:
        """Create a new session id and register it in the index.

        Returns the new session_id.  The session file itself is not
        written until ``save_session`` is called — this only reserves
        the id and adds a placeholder entry to the index.
        """
        session_id = _new_session_id()
        now = _now_iso()
        meta = SessionMeta(
            session_id=session_id,
            name=name or f"Session {now[:16]}",
            created_at=now,
            updated_at=now,
            message_count=0,
            tool_call_count=0,
            model="",
        )
        self._upsert_index(meta)
        return session_id

    def save_session(
        self,
        session_id: str,
        messages: list,
        llm_client: LLMClient,
        context: Context,
        tool_call_count: int,
        name: str | None = None,
        *,
        async_io: bool = False,
    ) -> Path | None:
        """Atomically save a session file and update the index.

        Returns the path written, or None on failure.

        When ``async_io=True`` (used by the agent loop), the session-file
        disk write is offloaded to a background worker via
        ``checkpoint._submit_write``; only the index update remains
        synchronous (it's small and needs consistency). The caller must
        have already snapshot-serialized anything race-sensitive — here
        we serialize ``payload`` to a string in the main thread before
        dispatching.
        """
        path = self._session_path(session_id)
        now = _now_iso()
        # Load existing meta to preserve created_at if this is an update.
        existing = self._find_meta(session_id)
        created_at = existing.created_at if existing else now
        session_name = name or (existing.name if existing else "Untitled")

        payload = {
            "session_id": session_id,
            "session_name": session_name,
            "saved_at": now,
            "created_at": created_at,
            "workdir": str(self._dir.parent.parent),  # the workdir
            "model": llm_client.model,
            "messages": messages,
            "tool_call_count": tool_call_count,
            "last_input_tokens": context.last_input_tokens,
            "checked_at_index": context.checked_at_index,
        }
        # Serialize in main thread — snapshot avoids race with agent loop.
        payload_str = json.dumps(payload, ensure_ascii=False, default=_json_default)

        if async_io:
            # Offload disk I/O to background worker (L6). Index update
            # stays synchronous for consistency.
            from loom.agent.checkpoint import _submit_write

            _submit_write(path, payload_str, chmod_mode=0o600)
        else:
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=self._dir, prefix=session_id + ".", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload_str)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except Exception as exc:
                logger.warning("failed to save session {}: {}", session_id, exc)
                try:
                    os.unlink(tmp)
                except (OSError, UnboundLocalError):
                    pass
                return None

        # Update the index (sync — fast, and needs consistency).
        meta = SessionMeta(
            session_id=session_id,
            name=session_name,
            created_at=created_at,
            updated_at=now,
            message_count=len(messages),
            tool_call_count=tool_call_count,
            model=llm_client.model,
        )
        self._upsert_index(meta)
        return path

    def load_session(self, session_id: str) -> dict | None:
        """Load a session file. Returns None if missing or malformed."""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("session {} malformed: {}", session_id, exc)
            return None

    def list_sessions(self) -> list[SessionMeta]:
        """Return all sessions, MRU-sorted (newest first)."""
        return self._load_index()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file and remove it from the index."""
        path = self._session_path(session_id)
        deleted = False
        try:
            path.unlink(missing_ok=True)
            deleted = True
        except OSError as exc:
            logger.warning("failed to delete session {}: {}", session_id, exc)
        metas = self._load_index()
        metas = [m for m in metas if m.session_id != session_id]
        self._save_index(metas)
        return deleted

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """Rename a session in the index and on-disk file."""
        metas = self._load_index()
        found = False
        updated: list[SessionMeta] = []
        for m in metas:
            if m.session_id == session_id:
                found = True
                # Use object.__setattr__ since SessionMeta is frozen.
                updated.append(
                    SessionMeta(
                        session_id=m.session_id,
                        name=new_name,
                        created_at=m.created_at,
                        updated_at=_now_iso(),
                        message_count=m.message_count,
                        tool_call_count=m.tool_call_count,
                        model=m.model,
                    )
                )
            else:
                updated.append(m)
        if found:
            self._save_index(updated)
            # Also update the session_name field in the session file.
            data = self.load_session(session_id)
            if data is not None:
                data["session_name"] = new_name
                path = self._session_path(session_id)
                try:
                    fd, tmp = tempfile.mkstemp(
                        dir=self._dir, prefix=session_id + ".", suffix=".tmp"
                    )
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, default=_json_default)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, path)
                except Exception as exc:
                    logger.warning("failed to update session name on disk: {}", exc)
                    try:
                        os.unlink(tmp)
                    except (OSError, UnboundLocalError):
                        pass
        return found

    def _find_meta(self, session_id: str) -> SessionMeta | None:
        for m in self._load_index():
            if m.session_id == session_id:
                return m
        return None


# ---------------------------------------------------------------------------
# LLM-powered session naming
# ---------------------------------------------------------------------------


def generate_session_name(
    llm_client: LLMClient, first_user_message: str
) -> str | None:
    """Ask the LLM for a 3-6 word summary of the first user message.

    Returns a short string suitable for the session list, or None on
    failure.  Uses a synchronous one-shot call (no streaming) to keep
    the integration simple — this runs after the first turn completes.
    """
    prompt = (
        f"Summarize the following user request in 3-6 words. "
        f"Reply with ONLY the summary, no quotes or punctuation:\n\n"
        f"{first_user_message[:500]}"
    )
    try:
        # Use the provider's underlying client for a one-shot completion.
        provider = llm_client._provider
        client = getattr(provider, "_client", None)
        if client is None:
            return None
        # Try the provider's complete method (varies by provider type).
        # Fall back to a simple truncation if LLM call is not available.
        complete_fn = getattr(provider, "complete", None)
        if callable(complete_fn):
            result = complete_fn(
                model=llm_client.model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=30,
            )
            # Extract text from the response.
            if isinstance(result, str):
                return result.strip()[:60]
            if isinstance(result, dict):
                content = result.get("content", "")
                if isinstance(content, list) and content:
                    text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                    return text.strip()[:60]
                if isinstance(content, str):
                    return content.strip()[:60]
        return None
    except Exception as exc:
        logger.debug("LLM session name generation failed: {}", exc)
        return None


def default_session_name(first_user_message: str) -> str:
    """Fallback name: first 30 chars of the first user message + timestamp."""
    snippet = first_user_message.strip().replace("\n", " ")[:30]
    if not snippet:
        snippet = "Untitled"
    return snippet
