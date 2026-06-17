"""Tests for the Kitty protocol batched unicode patch."""
from textual._xterm_parser import XTermParser

from loop.tui import kitty_patch


def test_batched_codepoints():
    """\\x1b[32;;20320:22909u parses to 你好 (20320=你, 22909=好)."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;20320:22909u")
    assert result is not None
    assert result.character == "你好"


def test_batched_single_codepoint():
    """\\x1b[32;;20320u parses to 你 alone."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;20320u")
    assert result is not None
    assert result.character == "你"


def test_batched_three_codepoints():
    """\\x1b[32;;20320:22909:19990u parses to 你好世."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;20320:22909:19990u")
    assert result is not None
    assert result.character == "你好世"


def test_batched_emoji():
    """\\x1b[32;;128512u parses to 😀 (U+1F600)."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;128512u")
    assert result is not None
    assert result.character == "😀"


def test_passthrough_single_codepoint():
    """Non-batched sequences still go to the original parser."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[1;5u")
    assert result is not None
    assert "ctrl" in result.key


def test_passthrough_unchanged():
    """Regular escape sequence is parsed by the original parser."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[27u")
    assert result is not None
    assert result.key == "escape"


def test_invalid_codepoints_returns_none():
    """Invalid codepoints return None, falling through to reissue."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;abc:xyz u")
    assert result is None


def test_non_kitty_returns_none():
    """Non-Kitty sequences return None."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "a")
    assert result is None


def test_empty_codepoints_falls_through():
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;u")
    assert result is not None
    assert result.key == "space"


def test_negative_codepoint_returns_none():
    """Negative codepoint returns None (chr fails)."""
    parser = XTermParser.__new__(XTermParser)
    result = kitty_patch._patched_parse_extended_key(parser, "\x1b[32;;-1u")
    assert result is None
