"""Checkpoint save/load for agent_loop session resumption.

Per Q4 (docs/harness-roadmap.md::10. Decisions):
  Hybrid default (10 tool calls OR 5k new tokens, whichever fires first),
  tunable in harness.toml [checkpoint] section.

For Phase 4, the harness.toml parsing is deferred. Defaults are
hard-coded constants here.

Public surface:
  Checkpoint.save(workdir, messages, llm_client, context, tool_call_count)
  Checkpoint.load(workdir) -> dict | None
  Checkpoint.exists(workdir) -> bool
  default_path_for(workdir) -> Path
  maybe_save(workdir, messages, llm_client, context, tool_call_count, new_tokens) -> bool
  is_due(tool_call_count, new_tokens) -> bool
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import os
import tempfile
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loom.agent.context import Context
    from loom.agent.llm import LLMClient

CHECKPOINT_EVERY_TOOL_CALLS = 10
CHECKPOINT_EVERY_TOKENS = 5000
CHECKPOINT_FILENAME = "checkpoint.json"


def _json_default(obj: object) -> object:
    """Serialize dataclass blocks (TextBlock, ToolUseBlock, etc.) to dicts."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return str(obj)


# ---------------------------------------------------------------------------
# L6: background disk-write executor
# ---------------------------------------------------------------------------
# checkpoint.save and session_store.save_session are called from the agent
# loop's hot path (every ~10 tool calls). The JSON serialization is fast
# (CPU-bound, ~10-50ms) but the disk fsync + rename can block the agent
# thread for 100-500ms on slow disks / network filesystems.
#
# We offload only the disk I/O to a single-worker ThreadPoolExecutor:
#   * main thread: build payload dict + json.dumps → payload_str (snapshot)
#   * worker thread: tempfile.mkstemp + write + os.replace (atomic)
#
# Single worker serializes writes so concurrent save() calls don't race on
# the same file. The queue never backs up because checkpoint frequency is
# low (every 10 tool calls / 5k tokens).
_write_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="ckpt-writer"
)
_pending_writes: list[concurrent.futures.Future] = []
_write_lock = threading.Lock()


def _submit_write(
    path: Path,
    payload_str: str,
    *,
    chmod_mode: int | None = None,
) -> concurrent.futures.Future:
    """Submit a background atomic write to ``path``.

    Writes ``payload_str`` to a temp file in the same directory (so the
    rename is atomic on POSIX), optionally applies ``chmod_mode``, then
    renames to ``path``. The caller must have already serialized the
    payload — this function only offloads the disk I/O.

    Errors are logged and swallowed (best-effort persistence): a failed
    checkpoint write must never crash the agent loop.
    """
    def _do_write() -> None:
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                dir=path.parent, prefix=path.name + ".", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload_str)
            if chmod_mode is not None:
                try:
                    os.chmod(tmp, chmod_mode)
                except OSError as exc:
                    logger.debug("chmod {} failed: {}", tmp, exc)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("background write to {} failed: {}", path, exc)
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    with _write_lock:
        fut = _write_executor.submit(_do_write)
        _pending_writes.append(fut)
        # Prune completed futures to avoid unbounded list growth.
        _pending_writes[:] = [f for f in _pending_writes if not f.done()]
        return fut


def flush_pending_writes(timeout: float = 5.0) -> bool:
    """Wait for all pending background writes to finish.

    Called at agent exit (and registered with ``atexit``) to ensure the
    final checkpoint/session save is durable before the process exits.
    Returns True if all writes completed within ``timeout``, False
    otherwise.
    """
    with _write_lock:
        futs = list(_pending_writes)
        _pending_writes.clear()
    if not futs:
        return True
    done, not_done = concurrent.futures.wait(futs, timeout=timeout)
    if not_done:
        logger.warning(
            "flush_pending_writes: {}/{} writes did not complete in {}s",
            len(not_done),
            len(futs),
            timeout,
        )
        return False
    return True


atexit.register(flush_pending_writes)


def default_path_for(workdir: Path) -> Path:
    return workdir / ".minicode" / CHECKPOINT_FILENAME


def exists(workdir: Path) -> bool:
    return default_path_for(workdir).exists()


def load(workdir: Path) -> dict | None:
    path = default_path_for(workdir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(
    workdir: Path,
    messages: list,
    llm_client: LLMClient,
    context: Context,
    tool_call_count: int,
    *,
    async_io: bool = False,
) -> Path:
    """Atomic save: write to .tmp then rename.

    When ``async_io=True`` (used by the agent loop), the disk write is
    offloaded to a background worker — the main thread only pays the
    JSON serialization cost. Use ``flush_pending_writes()`` before exit
    to ensure durability.

    When ``async_io=False`` (default; used by tests), the write is
    synchronous and the file is on-disk when this function returns.
    """
    path = default_path_for(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(UTC).isoformat(),
        "workdir": str(workdir),
        "model": llm_client.model,
        "messages": messages,
        "tool_call_count": tool_call_count,
        "last_input_tokens": context.last_input_tokens,
        "checked_at_index": context.checked_at_index,
    }
    # Serialize in main thread — this is the snapshot that prevents race
    # with the agent loop mutating `messages` after we return.
    payload_str = json.dumps(payload, ensure_ascii=False, default=_json_default)
    if async_io:
        _submit_write(path, payload_str)
        return path
    # Sync write path (tests / explicit callers that need on-disk durability)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload_str, encoding="utf-8")
    tmp.replace(path)
    return path


def is_due(
    tool_call_count: int,
    new_tokens_since_checkpoint: int,
    every_tool_calls: int = CHECKPOINT_EVERY_TOOL_CALLS,
    every_tokens: int = CHECKPOINT_EVERY_TOKENS,
) -> bool:
    return (
        tool_call_count >= every_tool_calls
        or new_tokens_since_checkpoint >= every_tokens
    )


def maybe_save(
    workdir: Path,
    messages: list,
    llm_client: LLMClient,
    context: Context,
    tool_call_count: int,
    new_tokens_since_checkpoint: int,
    every_tool_calls: int = CHECKPOINT_EVERY_TOOL_CALLS,
    every_tokens: int = CHECKPOINT_EVERY_TOKENS,
    *,
    async_io: bool = False,
) -> Path | None:
    if not is_due(tool_call_count, new_tokens_since_checkpoint,
                  every_tool_calls, every_tokens):
        return None
    return save(workdir, messages, llm_client, context, tool_call_count, async_io=async_io)
