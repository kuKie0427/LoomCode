"""Tests for f-prompt-caching-p2.

Verifies the with_cache_control and with_tool_cache_control helpers
correctly wrap system prompts and tools with Anthropic's
cache_control: ephemeral marker, and that the SDK calls use them.
"""

from __future__ import annotations

from loom.agent.llm import (
    MIN_CACHEABLE_TOKENS,
    with_cache_control,
    with_tool_cache_control,
)


def test_with_cache_control_empty_returns_empty():
    assert with_cache_control("") == ""


def test_with_cache_control_short_string_wraps_as_list():
    out = with_cache_control("hello world")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["type"] == "text"
    assert out[0]["text"] == "hello world"
    assert out[0]["cache_control"] == {"type": "ephemeral"}


def test_with_cache_control_passthrough_list_unchanged():
    prebuilt = [
        {"type": "text", "text": "first", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "second"},
    ]
    out = with_cache_control(prebuilt)
    assert out is prebuilt


def test_with_tool_cache_control_empty_returns_empty():
    assert with_tool_cache_control([]) == []


def test_with_tool_cache_control_marks_last_tool():
    tools = [
        {"name": "a", "description": "..."},
        {"name": "b", "description": "..."},
        {"name": "c", "description": "..."},
    ]
    out = with_tool_cache_control(tools)
    assert len(out) == 3
    assert "cache_control" not in out[0]
    assert "cache_control" not in out[1]
    assert out[2]["cache_control"] == {"type": "ephemeral"}
    assert out[2]["name"] == "c"
    assert out[0]["name"] == "a"
    assert out[1]["name"] == "b"


def test_with_tool_cache_control_does_not_mutate_input():
    tools = [{"name": "a", "description": "..."}]
    out = with_tool_cache_control(tools)
    assert "cache_control" not in tools[0]
    assert "cache_control" in out[0]


def test_min_cacheable_tokens_constant_defined():
    assert MIN_CACHEABLE_TOKENS == 1024


def test_loop_uses_cache_control_helpers():
    """Static check: cache_control helpers must be referenced somewhere in
    the agent path. After the multi-model-provider refactor, the helpers
    are applied inside AnthropicProvider.stream() (provider-agnostic via
    ProviderRequest.system), not at the loop.py sync call site.
    """
    from pathlib import Path
    src = Path("loom/agent/providers/anthropic.py").read_text()
    assert "with_cache_control(" in src
    assert "with_tool_cache_control(" in src
