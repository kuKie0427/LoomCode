"""Conversation export — convert messages + trace to markdown or JSON.

Used by the /export slash command and the `loom export` CLI subcommand
to produce shareable transcripts of agent sessions for debugging,
post-mortem analysis, and team sharing.

Markdown format is human-readable: role headers, thinking blocks
collapsed by default, tool calls with input + truncated output,
final cost summary footer.
JSON format is the raw messages + metadata (model, session_id,
total_cost, tool_call_count, started_at, ended_at) and round-trips
losslessly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loom.agent.cost import SessionCostAccumulator

OUTPUT_TRUNCATE = 500


@dataclass
class ExportMetadata:
    model: str
    session_id: str
    workdir: str
    tool_call_count: int
    started_at: str
    ended_at: str
    session_cost: SessionCostAccumulator | None = None


def _block_kind(block: dict) -> str:
    return block.get("type", "?")


def _block_text(block: dict) -> str:
    t = block.get("type")
    if t == "text":
        return block.get("text", "")
    if t == "thinking":
        return f"[thinking: {len(block.get('thinking', ''))} chars]"
    if t == "tool_use":
        return f"[tool_use: {block.get('name', '?')}]"
    return f"[{t}: unknown]"


def _format_tool_input_summary(inp: dict) -> str:
    if not inp:
        return "(no input)"
    parts = []
    for k, v in list(inp.items())[:3]:
        sv = str(v)
        if len(sv) > 80:
            sv = sv[:77] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


def to_markdown(messages: list, meta: ExportMetadata) -> str:
    """Render messages + metadata as a readable markdown transcript."""
    out: list[str] = []
    out.append(f"# Agent Session Transcript")
    out.append("")
    out.append(f"- **Session ID**: `{meta.session_id}`")
    out.append(f"- **Model**: `{meta.model}`")
    out.append(f"- **Workdir**: `{meta.workdir}`")
    out.append(f"- **Started**: {meta.started_at}")
    out.append(f"- **Ended**: {meta.ended_at}")
    out.append(f"- **Tool calls**: {meta.tool_call_count}")
    if meta.session_cost is not None:
        c = meta.session_cost
        out.append(f"- **Total cost**: ${c.total_usd:.6f}")
        out.append(f"- **Total tokens**: in={c.total_input}, out={c.total_output}")
        if c.total_cache_read:
            out.append(f"- **Cache read**: {c.total_cache_read} tokens")
        if c.total_cache_creation:
            out.append(f"- **Cache write**: {c.total_cache_creation} tokens")
        out.append(f"- **Turns**: {c.turns}")
    out.append("")
    out.append("---")
    out.append("")

    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                out.append(f"## User ({i})")
                out.append(content)
            elif isinstance(content, list):
                out.append(f"## User ({i})")
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        out.append(f"**Tool result** (`{block.get('tool_use_id', '?')[:12]}`):")
                        result_content = block.get("content", "")
                        if isinstance(result_content, str) and len(result_content) > OUTPUT_TRUNCATE:
                            out.append(f"```\n{result_content[:OUTPUT_TRUNCATE]}\n... ({len(result_content) - OUTPUT_TRUNCATE} more chars)\n```")
                        else:
                            out.append(f"```\n{result_content}\n```")
                        if block.get("is_error"):
                            out.append("*(error)*")
            out.append("")
        elif role == "assistant":
            out.append(f"## Assistant ({i})")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    t = block.get("type")
                    if t == "text":
                        out.append(block.get("text", ""))
                    elif t == "thinking":
                        out.append(f"<details><summary>thinking ({len(block.get('thinking', ''))} chars)</summary>\n\n{block.get('thinking', '')}\n\n</details>")
                    elif t == "tool_use":
                        out.append(f"**Tool call**: `{block.get('name', '?')}`")
                        out.append(f"```json\n{json.dumps(block.get('input', {}), indent=2, ensure_ascii=False)[:OUTPUT_TRUNCATE]}\n```")
            out.append("")
        else:
            out.append(f"## {role.capitalize()} ({i})")
            out.append(f"```\n{str(content)[:OUTPUT_TRUNCATE]}\n```")
            out.append("")
        out.append("---")
        out.append("")

    if meta.session_cost is not None and meta.session_cost.total_usd > 0:
        out.append(f"\n*Generated by loom export • {datetime.now().isoformat()}*")
    return "\n".join(out)


def to_json(messages: list, meta: ExportMetadata) -> str:
    """Render messages + metadata as a lossless JSON transcript."""
    metadata: dict = {
        "model": meta.model,
        "session_id": meta.session_id,
        "workdir": meta.workdir,
        "tool_call_count": meta.tool_call_count,
        "started_at": meta.started_at,
        "ended_at": meta.ended_at,
    }
    if meta.session_cost is not None:
        metadata["session_cost"] = meta.session_cost.as_dict()
    payload = {"metadata": metadata, "messages": messages}
    return json.dumps(payload, indent=2, ensure_ascii=False)


def redact_pii(text: str) -> str:
    """Replace common PII patterns (API keys, emails) with [REDACTED]."""
    import re
    text = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED_API_KEY]", text)
    text = re.sub(r"ANTHROPIC_API_KEY=[A-Za-z0-9_-]+", "ANTHROPIC_API_KEY=[REDACTED]", text)
    text = re.sub(r"[\w.-]+@[\w.-]+\.\w+", "[REDACTED_EMAIL]", text)
    return text


def write_export(content: str, output_path: Path, redact: bool = False) -> Path:
    if redact:
        content = redact_pii(content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
