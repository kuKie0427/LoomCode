"""Tests for the line-based JSON codec — handles framing, partial reads,
malformed input, and backpressure."""

from __future__ import annotations

import io
import json
import threading

import pytest

from loom.rpc.codec import LineCodec
from loom.rpc.protocol import Event


def test_write_event_appends_newline():
    buf = io.StringIO()
    codec = LineCodec(writer=buf)
    codec.write_event(Event.text_delta(text="hi"))
    assert buf.getvalue() == '{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"hi"}}\n'


def test_write_event_is_thread_safe():
    """Two threads writing simultaneously must not interleave their lines."""
    buf = io.StringIO()
    codec = LineCodec(writer=buf)
    barrier = threading.Barrier(2)

    def writer(n: int):
        barrier.wait()
        for i in range(50):
            codec.write_event(Event.text_delta(text=f"t{n}-{i}"))

    t1 = threading.Thread(target=writer, args=(1,))
    t2 = threading.Thread(target=writer, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    lines = buf.getvalue().split("\n")
    # Trailing empty string from final newline
    lines = [line for line in lines if line]
    assert len(lines) == 100
    # Every line must be valid JSON (no interleaving)
    for line in lines:
        json.loads(line)  # raises if malformed


def test_read_request_returns_parsed_message():
    buf = io.StringIO('{"jsonrpc":"2.0","method":"request/send_message","id":"r1","params":{"text":"hi"}}\n')
    codec = LineCodec(reader=buf)
    msg = codec.read_message()
    assert msg is not None
    assert msg.method == "request/send_message"
    assert msg.id == "r1"


def test_read_event_returns_parsed_message():
    buf = io.StringIO('{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"hi"}}\n')
    codec = LineCodec(reader=buf)
    msg = codec.read_message()
    assert msg is not None
    assert msg.method == "event/text_delta"


def test_read_returns_none_on_eof():
    buf = io.StringIO("")
    codec = LineCodec(reader=buf)
    assert codec.read_message() is None


def test_read_returns_none_on_blank_line_only():
    buf = io.StringIO("\n\n")
    codec = LineCodec(reader=buf)
    assert codec.read_message() is None


def test_read_skips_blank_lines_between_messages():
    buf = io.StringIO('\n{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"a"}}\n\n{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"b"}}\n')
    codec = LineCodec(reader=buf)
    m1 = codec.read_message()
    m2 = codec.read_message()
    m3 = codec.read_message()
    assert m1.params == {"text": "a"}
    assert m2.params == {"text": "b"}
    assert m3 is None  # EOF


def test_read_malformed_json_raises_value_error():
    buf = io.StringIO("not json\n")
    codec = LineCodec(reader=buf)
    with pytest.raises(ValueError, match="invalid JSON"):
        codec.read_message()


def test_read_message_missing_method_raises():
    buf = io.StringIO('{"jsonrpc":"2.0","params":{}}\n')
    codec = LineCodec(reader=buf)
    with pytest.raises(ValueError, match="missing 'method'"):
        codec.read_message()


def test_write_event_flushes_immediately():
    """The codec must flush after every write so the TUI sees events without
    waiting for the buffer to fill — critical for streaming text deltas."""
    class _TrackingWriter(io.TextIOBase):
        def __init__(self):
            self.flush_count = 0
            self.buf = ""
        def write(self, s):
            self.buf += s
            return len(s)
        def flush(self):
            self.flush_count += 1

    w = _TrackingWriter()
    codec = LineCodec(writer=w)
    codec.write_event(Event.text_delta(text="a"))
    codec.write_event(Event.text_delta(text="b"))
    assert w.flush_count == 2, "must flush after every write_event"


def test_write_event_swallows_broken_pipe():
    """If the TUI process dies (broken pipe), write_event must not raise —
    the agent loop would crash otherwise. Logs instead."""
    class _BrokenWriter:
        def write(self, s):
            raise BrokenPipeError("TUI gone")
        def flush(self):
            raise BrokenPipeError("TUI gone")

    codec = LineCodec(writer=_BrokenWriter())
    # Must not raise
    codec.write_event(Event.text_delta(text="still streaming"))


def test_write_event_works_without_writer():
    """A codec with no writer is a no-op (useful for tests)."""
    codec = LineCodec()
    codec.write_event(Event.text_delta(text="hi"))  # must not raise


def test_read_message_works_without_reader():
    """A codec with no reader returns None immediately."""
    codec = LineCodec()
    assert codec.read_message() is None


def test_read_message_can_parse_request_and_event():
    """Verify the codec dispatches to the right subtype based on method prefix."""
    buf = io.StringIO(
        '{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"a"}}\n'
        '{"jsonrpc":"2.0","method":"request/cancel","id":"r1","params":{}}\n'
    )
    codec = LineCodec(reader=buf)
    m1 = codec.read_message()
    m2 = codec.read_message()
    # Both should be parsed; the right type depends on the method prefix.
    # We just check that .method is set correctly — full type dispatch is
    # covered by test_rpc_protocol.py.
    assert m1.method == "event/text_delta"
    assert m2.method == "request/cancel"
    # The request should also carry an id
    assert getattr(m2, "id", "") == "r1"
