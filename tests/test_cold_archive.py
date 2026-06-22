"""Tests for f-long-context-stability-p3 (loom.agent.cold_archive)."""

from __future__ import annotations

import pytest

from loom.agent.cold_archive import (
    CHUNK_PREFIX,
    CHUNK_SUFFIX,
    MANIFEST_FILENAME,
    ArchiveManifest,
    ChunkInfo,
    archive,
    estimate_tokens,
    list_chunks,
    rehydrate,
)


def _make_turns(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}", "idx": i} for i in range(n)]


def test_archive_writes_manifest(tmp_path):
    dest = tmp_path / "cs"
    archive(_make_turns(10), dest)
    assert (dest / MANIFEST_FILENAME).exists()


def test_archive_writes_chunks(tmp_path):
    dest = tmp_path / "cs"
    archive(_make_turns(10), dest, chunk_size=3)
    chunks = list(dest.glob(CHUNK_PREFIX + "*" + CHUNK_SUFFIX))
    assert len(chunks) == 4


def test_archive_manifest_records_total(tmp_path):
    dest = tmp_path / "cs"
    manifest = archive(_make_turns(120), dest, chunk_size=50)
    assert manifest.total_turns == 120
    assert len(manifest.chunks) == 3


def test_archive_manifest_chunk_ranges(tmp_path):
    dest = tmp_path / "cs"
    manifest = archive(_make_turns(120), dest, chunk_size=50)
    assert manifest.chunks[0].start == 0 and manifest.chunks[0].end == 50
    assert manifest.chunks[1].start == 50 and manifest.chunks[1].end == 100
    assert manifest.chunks[2].start == 100 and manifest.chunks[2].end == 120


def test_archive_is_idempotent(tmp_path):
    dest = tmp_path / "cs"
    archive(_make_turns(10), dest)
    archive(_make_turns(20), dest)
    chunks = list(dest.glob(CHUNK_PREFIX + "*" + CHUNK_SUFFIX))
    assert len(chunks) == 1
    manifest = ArchiveManifest.from_json((dest / MANIFEST_FILENAME).read_text())
    assert manifest.total_turns == 20


def test_archive_empty_turns_writes_empty_manifest(tmp_path):
    dest = tmp_path / "cs"
    manifest = archive([], dest)
    assert manifest.total_turns == 0
    assert manifest.chunks == []
    assert (dest / MANIFEST_FILENAME).exists()


def test_rehydrate_full_range(tmp_path):
    dest = tmp_path / "cs"
    turns = _make_turns(20)
    archive(turns, dest, chunk_size=5)
    out = rehydrate(dest, 0, 20)
    assert out == turns


def test_rehydrate_slice_from_middle(tmp_path):
    dest = tmp_path / "cs"
    turns = _make_turns(20)
    archive(turns, dest, chunk_size=5)
    out = rehydrate(dest, 7, 13)
    assert out == turns[7:13]


def test_rehydrate_single_turn(tmp_path):
    dest = tmp_path / "cs"
    turns = _make_turns(10)
    archive(turns, dest, chunk_size=3)
    out = rehydrate(dest, 4, 5)
    assert len(out) == 1
    assert out[0]["idx"] == 4


def test_rehydrate_raises_when_no_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        rehydrate(tmp_path, 0, 5)


def test_rehydrate_raises_on_bad_range(tmp_path):
    dest = tmp_path / "cs"
    archive(_make_turns(10), dest)
    with pytest.raises(ValueError, match="invalid range"):
        rehydrate(dest, 5, 5)
    with pytest.raises(ValueError, match="invalid range"):
        rehydrate(dest, -1, 5)
    with pytest.raises(ValueError, match="invalid range"):
        rehydrate(dest, 0, 100)


def test_list_chunks_returns_chunk_info(tmp_path):
    dest = tmp_path / "cs"
    archive(_make_turns(30), dest, chunk_size=10)
    chunks = list_chunks(dest)
    assert len(chunks) == 3
    assert chunks[0].file.endswith(CHUNK_SUFFIX)
    assert chunks[0].start == 0


def test_estimate_tokens_returns_positive():
    assert estimate_tokens([{"role": "user", "content": "hi"}]) > 0


def test_estimate_tokens_handles_empty():
    assert estimate_tokens([]) == 0


def test_archive_manifest_round_trip():
    manifest = ArchiveManifest(
        version=1, created_at="2026-06-22T00:00:00Z",
        total_turns=10, total_tokens=100,
        chunks=[ChunkInfo(file="chunk-0000.jsonl.gz", start=0, end=10, tokens=100)],
    )
    s = manifest.to_json()
    restored = ArchiveManifest.from_json(s)
    assert restored.total_turns == 10
    assert restored.chunks[0].file == "chunk-0000.jsonl.gz"


def test_cold_archive_module_public_api():
    from loom.agent import cold_archive
    for name in ("archive", "rehydrate", "list_chunks", "estimate_tokens", "ArchiveManifest", "ChunkInfo"):
        assert hasattr(cold_archive, name), f"missing {name}"