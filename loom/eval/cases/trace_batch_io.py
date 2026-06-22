"""Harness eval cases for f-trace-batch-io-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class TraceKeepsFileHandleOpen(EvalCase):
    name = "trace-keeps-file-handle-open"
    description = "Trace.record() does NOT reopen the file on every call (uses cached handle)"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import loom.agent.trace as t

        with tempfile.TemporaryDirectory() as tmp:
            tr = t.Trace(Path(tmp), "s")
            open_count = [0]
            real_open = Path.open
            def counting_open(self, *args, **kwargs):
                open_count[0] += 1
                return real_open(self, *args, **kwargs)
            with patch.object(Path, "open", counting_open):
                for _ in range(10):
                    tr.record("e", x=1)
            tr.close()
            if open_count[0] != 1:
                return EvalResult(name=self.name, passed=False, detail=f"opened {open_count[0]} times, expected 1")
        return EvalResult(name=self.name, passed=True, detail="file handle kept open across 10 records")


class TraceStopClosesHandle(EvalCase):
    name = "trace-stop-closes-handle"
    description = "trace_mod.stop() closes the active trace's file handle"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        import loom.agent.trace as t

        with tempfile.TemporaryDirectory() as tmp:
            tr = t.start(Path(tmp), "s")
            tr.record("e", x=1)
            assert tr._fh is not None
            t.stop()
            if tr._fh is not None:
                return EvalResult(name=self.name, passed=False, detail="_fh not None after stop")
        return EvalResult(name=self.name, passed=True, detail="stop() closes the handle")


class TraceConcurrentSafe(EvalCase):
    name = "trace-concurrent-safe"
    description = "5 threads x 10 records each produces 50 valid JSONL lines (no corruption)"

    def run(self) -> EvalResult:
        import json
        import tempfile
        import threading
        from pathlib import Path

        import loom.agent.trace as t

        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            tr = t.Trace(wd, "s")
            def worker(i):
                for _ in range(10):
                    tr.record("e", thread=i)
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            tr.close()
            content = wd.joinpath(".minicode", "trace.jsonl").read_text()
            lines = [json.loads(line) for line in content.splitlines() if line]
            if len(lines) != 50:
                return EvalResult(name=self.name, passed=False, detail=f"got {len(lines)} lines, expected 50")
            seen = {e["thread"] for e in lines}
            if seen != {0, 1, 2, 3, 4}:
                return EvalResult(name=self.name, passed=False, detail=f"threads seen: {seen}")
        return EvalResult(name=self.name, passed=True, detail="50 records, 5 threads, no corruption")
