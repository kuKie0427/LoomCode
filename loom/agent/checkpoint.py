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

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loom.agent.context import Context
    from loom.agent.llm import LLMClient

CHECKPOINT_EVERY_TOOL_CALLS = 10
CHECKPOINT_EVERY_TOKENS = 5000
CHECKPOINT_FILENAME = "checkpoint.json"


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
) -> Path:
    """Atomic save: write to .tmp then rename."""
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
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
) -> Path | None:
    if not is_due(tool_call_count, new_tokens_since_checkpoint,
                  every_tool_calls, every_tokens):
        return None
    return save(workdir, messages, llm_client, context, tool_call_count)
