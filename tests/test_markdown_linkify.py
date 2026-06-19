"""Tests for the TUI markdown parser's linkify behavior.

The default ``textual.widgets.Markdown`` uses the ``gfm-like`` markdown-it
preset, which enables ``linkify-it`` to auto-detect URLs in plain text.
``linkify-it`` matches any ``domain.tld`` against the public-suffix list,
so file names whose extension is a real TLD (``conftest.py``,
``setup.sh``, ``README.md``, ``a.py``) are silently re-rendered as
``http://conftest.py`` and become clickable — clicking them opens the OS
default browser at a domain that doesn't exist.

We disable linkify in ``_markdown_parser_factory`` and pass it to every
``Markdown`` subclass via ``parser_factory``. These tests guard that:

1. The factory disables linkify (and the other gfm features survive).
2. Every ``Markdown`` subclass in the chat log wires the factory in.
3. Real (TUI-mounted) messages containing TLD-shaped file names do not
   emit any segment with a link style.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from loop.tui.app import AgentTUIApp
from loop.tui.chat_log import (
    AssistantMessage,
    StreamingOverlay,
    ThinkingDisplay,
    UserMessage,
    _markdown_parser_factory,
)

# -- Factory unit tests ---------------------------------------------------


def test_parser_factory_disables_linkify():
    parser = _markdown_parser_factory()
    assert parser.options["linkify"] is False, (
        "linkify must be off so TLD-shaped file names like conftest.py "
        "do not get re-rendered as clickable http://conftest.py links"
    )


def test_parser_factory_does_not_linkify_tld_shaped_filenames():
    parser = _markdown_parser_factory()
    text = "I created conftest.py for tests"
    tokens = parser.parse(text)
    for tok in tokens:
        if tok.type == "inline" and tok.children:
            for child in tok.children:
                assert child.type != "link_open", (
                    f"linkify must not create a link for a TLD-shaped "
                    f"filename; got attrs={dict(child.attrs)!r}"
                )


def test_parser_factory_keeps_explicit_markdown_links():
    parser = _markdown_parser_factory()
    text = "See [example](https://example.com) please"
    tokens = parser.parse(text)
    found_href: str | None = None
    for tok in tokens:
        if tok.type == "inline" and tok.children:
            for child in tok.children:
                if child.type == "link_open":
                    found_href = dict(child.attrs).get("href")
    assert found_href == "https://example.com", (
        "Explicit [text](url) syntax must still produce a link; "
        f"got href={found_href!r}"
    )


def test_parser_factory_keeps_tables():
    parser = _markdown_parser_factory()
    text = "| A | B |\n|---|---|\n| 1 | 2 |"
    tokens = parser.parse(text)
    assert any(t.type == "table_open" for t in tokens), (
        "tables should still be parsed by the gfm-like preset"
    )


def test_parser_factory_keeps_strikethrough():
    parser = _markdown_parser_factory()
    tokens = parser.parse("~~deleted~~")
    found = False
    for tok in tokens:
        if tok.type == "inline" and tok.children:
            for child in tok.children:
                if child.type == "s_open":
                    found = True
    assert found, "strikethrough should still be parsed by the gfm-like preset"


# -- Subclass wiring tests ------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [UserMessage, AssistantMessage, StreamingOverlay, ThinkingDisplay],
)
def test_markdown_subclass_uses_safe_parser(cls):
    """Every Markdown subclass in the chat log must use the safe parser.

    Without this, a TLD-shaped filename (e.g. ``conftest.py``) in any
    chat-log message becomes a clickable bogus link and opens the
    default browser at a non-existent domain. The wiring lives in
    each subclass's ``__init__``.
    """
    src = inspect.getsource(cls.__init__)
    assert "_markdown_parser_factory" in src, (
        f"{cls.__name__}.__init__ must wire the safe parser via "
        f"parser_factory; got: {src!r}"
    )


# -- End-to-end test: mounted message must not emit link segments ---------


def test_mounted_assistant_message_with_filename_has_no_link_segment():
    """End-to-end guard: a fully-mounted AssistantMessage that contains a
    TLD-shaped filename must not produce any segment with a link style.
    The TUI clicks a segment with a link style; if no segment has a
    link, nothing is clickable, and clicking the filename does nothing.
    """

    async def driver():
        from loop.tui.chat_log import _markdown_parser_factory

        app = AgentTUIApp()
        async with app.run_test(size=(80, 20)) as pilot:
            msg = AssistantMessage("I created conftest.py for tests")
            await app.screen.mount(msg)
            await pilot.pause(0.2)
            factory = msg._parser_factory or _markdown_parser_factory
            parser = factory()
            tokens = parser.parse("I created conftest.py for tests")
            for tok in tokens:
                if tok.type == "inline" and tok.children:
                    for child_tok in tok.children:
                        assert child_tok.type != "link_open", (
                            "mounted AssistantMessage must not contain "
                            "an auto-generated link for a TLD-shaped filename"
                        )

    asyncio.run(driver())
