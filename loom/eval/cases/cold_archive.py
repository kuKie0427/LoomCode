"""Harness eval cases for f-long-context-stability-p3."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class ColdArchiveModuleDefined(EvalCase):
    name = "cold-archive-module-defined"
    description = "loom.agent.cold_archive exposes archive + rehydrate + manifest types"

    def run(self) -> EvalResult:
        try:
            from loom.agent import cold_archive as c
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("archive", "rehydrate", "list_chunks", "estimate_tokens",
                     "ArchiveManifest", "ChunkInfo"):
            if not hasattr(c, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="cold_archive public API complete")


class ColdArchiveRoundTrip(EvalCase):
    name = "cold-archive-round-trip"
    description = "archive + rehydrate preserves turn order and content for 100 turns"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent.cold_archive import archive, rehydrate

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}", "idx": i}
                     for i in range(100)]
            manifest = archive(turns, dest, chunk_size=20)
            if manifest.total_turns != 100:
                return EvalResult(name=self.name, passed=False, detail=f"manifest={manifest.total_turns}")
            rehydrated = rehydrate(dest, 0, 100)
            if rehydrated != turns:
                return EvalResult(name=self.name, passed=False, detail="round-trip mismatch")
        return EvalResult(name=self.name, passed=True, detail="100 turns round-tripped exactly")


class ColdArchivePartialSlice(EvalCase):
    name = "cold-archive-partial-slice"
    description = "rehydrate returns only the requested slice, with correct indices"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent.cold_archive import archive, rehydrate

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            turns = [{"role": "user", "content": f"t{i}", "idx": i} for i in range(50)]
            archive(turns, dest, chunk_size=10)
            out = rehydrate(dest, 15, 25)
            if len(out) != 10:
                return EvalResult(name=self.name, passed=False, detail=f"got {len(out)} turns")
            if out[0]["idx"] != 15 or out[-1]["idx"] != 24:
                return EvalResult(name=self.name, passed=False, detail=f"indices wrong: {out[0]}..{out[-1]}")
        return EvalResult(name=self.name, passed=True, detail="slice [15:25] correct")


class ColdArchiveLargeSessionSimulated(EvalCase):
    name = "cold-archive-large-session-simulated"
    description = "archive handles a 1M-token-shaped session (10K turns, many chunks)"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent.cold_archive import archive, rehydrate

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            turns = [{"role": "user", "content": f"x{i}", "idx": i} for i in range(10_000)]
            manifest = archive(turns, dest, chunk_size=500)
            if len(manifest.chunks) != 20:
                return EvalResult(name=self.name, passed=False, detail=f"chunks={len(manifest.chunks)}")
            sample = rehydrate(dest, 5_000, 5_010)
            if len(sample) != 10 or sample[0]["idx"] != 5_000:
                return EvalResult(name=self.name, passed=False, detail=f"slice wrong: {sample[0]}")
        return EvalResult(name=self.name, passed=True, detail="10K-turn session chunked + rehydrated correctly")