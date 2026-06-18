"""Tests for the Kitty protocol batched unicode patch."""

from textual._xterm_parser import XTermParser

from loop.tui import kitty_patch


def test_batched_codepoints():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;20320:22909u")
    events = list(result)
    assert len(events) == 1
    assert events[0].character == "你好"


def test_batched_single_codepoint():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;20320u")
    events = list(result)
    assert len(events) == 1
    assert events[0].character == "你"


def test_batched_three_codepoints():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;20320:22909:19990u")
    events = list(result)
    assert len(events) == 1
    assert events[0].character == "你好世"


def test_batched_emoji():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;128512u")
    events = list(result)
    assert len(events) == 1
    assert events[0].character == "😀"


def test_passthrough_single_codepoint():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[1;5u")
    events = list(result)
    assert len(events) >= 1
    assert any("ctrl" in e.key for e in events)


def test_passthrough_unchanged():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[27u")
    events = list(result)
    assert len(events) >= 1
    assert any(e.key == "escape" for e in events)


def test_invalid_codepoints_no_event():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;abc:xyz u")
    events = list(result)
    assert events == []


def test_non_kitty_falls_through():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "a")
    events = list(result)
    assert len(events) >= 1
    assert events[0].character == "a"


def test_negative_codepoint_no_event():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;-1u")
    events = list(result)
    assert events == []


def test_empty_codepoints_falls_through():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;u")
    events = list(result)
    assert len(events) >= 1
    assert events[0].key == "space"


def test_works_when_kitty_disabled(monkeypatch):
    monkeypatch.setenv("TEXTUAL_DISABLE_KITTY_KEY", "1")
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_sequence_to_key_events(parser, "\x1b[32;;20320:22909u")
    events = list(result)
    assert len(events) == 1
    assert events[0].character == "你好"
