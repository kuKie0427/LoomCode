"""Cold-storage archive for f-long-context-stability-p3.

Scope (deliberately small):
- archive(turns, dest, chunk_size): split a long list of turns into
  compressed JSONL chunks on disk; returns a manifest
- rehydrate(manifest, start_turn, end_turn): load a contiguous
  slice of turns back into memory
- list_chunks(manifest): discover archived chunks on disk
- estimate_tokens(turns): rough token count for budgeting

NOT in scope (deferred per Working Rule #2 — single-feature scope):
- agent-loop integration (caller passes turns in / gets turns back)
- automatic eviction thresholds
- encryption at rest
- cross-session merging
- lazy loading during a live agent turn

Format:
  .minicode/cold-storage/
    manifest.json          # {chunks: [{file, start, end, tokens}]}
    chunk-0000.jsonl.gz    # newline-delimited JSON, gzipped
    chunk-0001.jsonl.gz
    ...

Why JSONL+gz not pickle: text/jsonl is inspectable (debuggable),
gz is 5-10x smaller on JSON, and the format is stable across Python
versions.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

MANIFEST_FILENAME = "manifest.json"
CHUNK_PREFIX = "chunk-"
CHUNK_SUFFIX = ".jsonl.gz"


@dataclass
class ChunkInfo:
    file: str
    start: int
    end: int
    tokens: int


@dataclass
class ArchiveManifest:
    version: int
    created_at: str
    total_turns: int
    total_tokens: int
    chunks: list[ChunkInfo] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "ArchiveManifest":
        data = json.loads(s)
        chunks = [ChunkInfo(**c) for c in data.pop("chunks", [])]
        return cls(chunks=chunks, **data)


def estimate_tokens(turns: Iterable[dict]) -> int:
    """Rough token estimate: chars/4 + 10 overhead per turn.

    Not accurate — the real estimator is in loom.agent.context. This
    one is for cold-storage budgeting only (decide chunking cadence).
    """
    total = 0
    for t in turns:
        body = json.dumps(t, default=str)
        total += max(1, len(body) // 4) + 10
    return total


def _serialize_turn(turn: dict) -> str:
    return json.dumps(turn, default=str, ensure_ascii=False)


def archive(
    turns: list[dict],
    dest: Path,
    *,
    chunk_size: int = 50,
) -> ArchiveManifest:
    """Write `turns` into cold storage as gzipped JSONL chunks.

    Each chunk holds up to `chunk_size` turns. Writes a manifest at
    dest/MANIFEST_FILENAME mapping chunk files to turn index ranges.

    Idempotent: deletes any existing chunks in dest before writing.
    """
    import datetime as _dt

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob(CHUNK_PREFIX + "*" + CHUNK_SUFFIX):
        old.unlink()

    manifest = ArchiveManifest(
        version=1,
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        total_turns=len(turns),
        total_tokens=estimate_tokens(turns),
        chunks=[],
    )

    n_chunks = math.ceil(len(turns) / chunk_size) if turns else 0
    for i in range(n_chunks):
        start = i * chunk_size
        end = min(start + chunk_size, len(turns))
        chunk_turns = turns[start:end]
        file_name = f"{CHUNK_PREFIX}{i:04d}{CHUNK_SUFFIX}"
        file_path = dest / file_name
        with gzip.open(file_path, "wt", encoding="utf-8") as f:
            for t in chunk_turns:
                f.write(_serialize_turn(t))
                f.write("\n")
        manifest.chunks.append(ChunkInfo(
            file=file_name,
            start=start,
            end=end,
            tokens=estimate_tokens(chunk_turns),
        ))

    manifest_path = dest / MANIFEST_FILENAME
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def rehydrate(
    manifest_dir: Path,
    start_turn: int,
    end_turn: int,
) -> list[dict]:
    """Load turns [`start_turn`, `end_turn`) from cold storage.

    Reads only the chunks that overlap the requested range. Raises
    FileNotFoundError if manifest is missing; ValueError on bad range.
    """
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest at {manifest_path}")
    manifest = ArchiveManifest.from_json(manifest_path.read_text(encoding="utf-8"))

    if start_turn < 0 or end_turn > manifest.total_turns or start_turn >= end_turn:
        raise ValueError(
            f"invalid range [{start_turn}, {end_turn}) for archive with "
            f"{manifest.total_turns} turns"
        )

    out: list[dict] = []
    needed = end_turn - start_turn
    for chunk in manifest.chunks:
        if chunk.end <= start_turn or chunk.start >= end_turn:
            continue
        file_path = manifest_dir / chunk.file
        local_index = 0
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    local_index += 1
                    continue
                global_index = chunk.start + local_index
                if start_turn <= global_index < end_turn:
                    out.append(json.loads(line))
                    if len(out) >= needed:
                        return out
                local_index += 1
    return out


def list_chunks(manifest_dir: Path) -> list[ChunkInfo]:
    """Return the chunk list from manifest without loading any turns."""
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest at {manifest_path}")
    manifest = ArchiveManifest.from_json(manifest_path.read_text(encoding="utf-8"))
    return list(manifest.chunks)