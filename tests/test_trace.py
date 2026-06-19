from __future__ import annotations

import json
import threading

import pytest

from loom.agent.trace import Trace, current, default_path_for, start, stop


@pytest.fixture
def trace_workdir(tmp_path):
    return tmp_path


def test_default_path_for(trace_workdir):
    p = default_path_for(trace_workdir)
    assert p == trace_workdir / ".minicode" / "trace.jsonl"


def test_record_writes_valid_jsonl(trace_workdir):
    tr = Trace(trace_workdir, session_id="abc")
    tr.record("session_start", workdir="/x", initial_messages=3)
    lines = tr.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "session_start"
    assert row["session_id"] == "abc"
    assert row["workdir"] == "/x"
    assert row["initial_messages"] == 3
    assert "ts" in row


def test_record_appends_in_order(trace_workdir):
    tr = Trace(trace_workdir, session_id="s1")
    tr.record("a", n=1)
    tr.record("b", n=2)
    tr.record("c", n=3)
    events = [json.loads(line)["event"] for line in tr.path.read_text(encoding="utf-8").splitlines()]
    assert events == ["a", "b", "c"]


def test_recent_returns_last_n(trace_workdir):
    tr = Trace(trace_workdir, session_id="s1")
    for i in range(10):
        tr.record("e", i=i)
    out = tr.recent(n=3)
    assert len(out) == 3
    assert [r["i"] for r in out] == [7, 8, 9]


def test_recent_handles_missing_file(tmp_path):
    tr = Trace(tmp_path / "no-such-dir", session_id="s")
    tr.path = tmp_path / "absent.jsonl"
    assert tr.recent() == []


def test_recent_skips_blank_and_invalid_lines(trace_workdir):
    tr = Trace(trace_workdir, session_id="s")
    tr.record("ok", n=1)
    raw = tr.path.read_text(encoding="utf-8")
    raw = raw + "\n\nnot-json\n"
    tr.path.write_text(raw, encoding="utf-8")
    out = tr.recent()
    assert len(out) == 1
    assert out[0]["event"] == "ok"


def test_thread_safety(trace_workdir):
    tr = Trace(trace_workdir, session_id="t1")

    def writer(prefix: str):
        for i in range(50):
            tr.record("e", tag=f"{prefix}-{i}")

    threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    lines = [ln for ln in tr.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 200
    seen_tags = {json.loads(ln)["tag"] for ln in lines}
    assert len(seen_tags) == 200


def test_start_makes_current(trace_workdir):
    stop()
    assert current() is None
    tr = start(trace_workdir, session_id="cli")
    try:
        assert current() is tr
        assert tr.session_id == "cli"
    finally:
        stop()
    assert current() is None


def test_stop_resets_current(trace_workdir):
    start(trace_workdir, session_id="x")
    assert current() is not None
    stop()
    assert current() is None


def test_record_with_non_serializable_value_falls_back_to_str(trace_workdir):
    tr = Trace(trace_workdir, session_id="s")

    class Weird:
        def __repr__(self):
            return "<weird>"

    tr.record("e", obj=Weird())
    row = json.loads(tr.path.read_text(encoding="utf-8").splitlines()[0])
    assert row["obj"] == "<weird>"