"""Tests for f-trace-batch-io-p2.

Verifies the trace file handle is kept open across record() calls
(not reopened on every event), with line-buffered flush, and
closed cleanly on stop().
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import loom.agent.trace as trace_mod


@pytest.fixture
def trace(tmp_path):
    tr = trace_mod.Trace(tmp_path, session_id="test-session")
    yield tr
    tr.close()


def test_record_creates_file(tmp_path, trace):
    trace.record("test_event", value=42)
    assert trace.path.exists()


def test_record_persists_jsonl_line(tmp_path, trace):
    trace.record("e1", foo="bar")
    trace.record("e2", n=3)
    content = trace.path.read_text()
    lines = [line for line in content.splitlines() if line]
    assert len(lines) == 2
    import json
    e1 = json.loads(lines[0])
    assert e1["event"] == "e1"
    assert e1["foo"] == "bar"
    assert e1["session_id"] == "test-session"


def test_record_keeps_file_handle_open(tmp_path):
    """The file handle should NOT be reopened on each record() call."""
    tr = trace_mod.Trace(tmp_path, session_id="s")
    open_counts = []
    real_open = Path.open
    def counting_open(self, *args, **kwargs):
        open_counts.append(self)
        return real_open(self, *args, **kwargs)
    with patch.object(Path, "open", counting_open):
        for _ in range(5):
            tr.record("e", x=1)
    tr.close()
    assert len(open_counts) == 1, f"expected 1 open, got {len(open_counts)}"


def test_record_flushes_after_each_write(tmp_path, trace):
    """line-buffered + explicit flush() means a reader sees the line immediately."""
    trace.record("a", x=1)
    content = trace.path.read_text()
    assert '"event": "a"' in content


def test_close_releases_handle(tmp_path):
    tr = trace_mod.Trace(tmp_path, session_id="s")
    tr.record("a", x=1)
    tr.close()
    assert tr._fh is None


def test_record_after_close_works(tmp_path, trace):
    """close() sets _fh=None so the next record() reopens transparently."""
    trace.record("before", x=1)
    trace.close()
    trace.record("after", x=2)
    import json
    content = trace.path.read_text()
    lines = [json.loads(line) for line in content.splitlines() if line]
    events = [e["event"] for e in lines]
    assert events == ["before", "after"]


def test_stop_closes_active_trace(tmp_path):
    tr = trace_mod.start(tmp_path, session_id="stop-test")
    tr.record("e", x=1)
    assert tr._fh is not None
    trace_mod.stop()
    assert tr._fh is None
    assert trace_mod.current() is None


def test_concurrent_records_dont_corrupt_file(tmp_path):
    """Multiple threads calling record() concurrently should produce N clean lines."""
    import threading
    tr = trace_mod.Trace(tmp_path, session_id="concurrent")
    errors = []
    def worker(i):
        try:
            for _ in range(10):
                tr.record("e", thread=i)
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tr.close()
    assert not errors
    content = trace_mod.Trace(tmp_path, session_id="verify").path.read_text()
    import json
    valid = [json.loads(line) for line in content.splitlines() if line]
    assert len(valid) == 50
    threads_seen = {e["thread"] for e in valid}
    assert threads_seen == {0, 1, 2, 3, 4}


def test_legacy_record_still_produces_valid_jsonl(tmp_path, trace):
    """Sanity: legacy record() contract (file exists, JSONL valid) preserved."""
    trace.record("e1", a=1)
    trace.record("e2", b="two", c=[1, 2])
    import json
    lines = trace.path.read_text().splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 2
    assert parsed[0]["a"] == 1
    assert parsed[1]["b"] == "two"
    assert parsed[1]["c"] == [1, 2]
