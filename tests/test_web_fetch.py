"""Tests for f-web-fetch-tool-p1.

Covers the public contract: URL validation, content-type routing,
HTML extraction, truncation, error paths, registry/subagent exposure.
Network calls are mocked via httpx MockTransport so tests are
deterministic and offline.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

import loom.agent.tools as main


def _html_response(text: str, status: int = 200, content_type: str = "text/html; charset=utf-8"):
    return httpx.Response(status, content=text.encode("utf-8"), headers={"content-type": content_type})


def _with_mocked_client(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return patch.object(main.httpx, "Client", return_value=client)


def test_web_fetch_rejects_empty_url():
    out = main.run_web_fetch("")
    assert "Error" in out


def test_web_fetch_rejects_non_http_scheme():
    out = main.run_web_fetch("file:///etc/passwd")
    assert "Error" in out
    assert "scheme" in out


def test_web_fetch_rejects_ftp_scheme():
    out = main.run_web_fetch("ftp://example.com/file")
    assert "Error" in out
    assert "scheme" in out


def test_web_fetch_html_basic():
    def handler(req):
        return _html_response("<html><body><h1>Hello</h1><p>World</p></body></html>")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "Hello" in out
    assert "World" in out


def test_web_fetch_html_strips_script_tags():
    def handler(req):
        return _html_response(
            "<html><body><script>alert('xss')</script><p>Safe content</p></body></html>"
        )
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "alert" not in out
    assert "Safe content" in out


def test_web_fetch_html_strips_style_tags():
    def handler(req):
        return _html_response(
            "<html><head><style>body { color: red; }</style></head><body><p>Visible</p></body></html>"
        )
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "color: red" not in out
    assert "Visible" in out


def test_web_fetch_html_adds_block_separators():
    def handler(req):
        return _html_response("<html><body><p>Para 1</p><p>Para 2</p></body></html>")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "Para 1\n\nPara 2" in out


def test_web_fetch_html_decodes_entities():
    def handler(req):
        return _html_response("<html><body><p>Tom &amp; Jerry &lt;3</p></body></html>")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "Tom & Jerry <3" in out


def test_web_fetch_plain_text_passthrough():
    def handler(req):
        return _html_response("Hello plain text world", content_type="text/plain")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/data.txt")
    assert "Hello plain text world" in out


def test_web_fetch_unsupported_content_type_rejected():
    def handler(req):
        return _html_response("binary blob", content_type="application/octet-stream")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/binary")
    assert "Error" in out
    assert "unsupported" in out


def test_web_fetch_truncates_at_max_chars():
    long_text = "x" * 5000
    def handler(req):
        return _html_response(f"<p>{long_text}</p>")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/", max_chars=500)
    assert "truncated" in out
    assert "xxx" in out


def test_web_fetch_404_returns_error():
    def handler(req):
        return _html_response("Not Found", status=404)
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/missing")
    assert "Error" in out
    assert "404" in out


def test_web_fetch_empty_content_reports_empty():
    def handler(req):
        return _html_response("<html><body></body></html>")
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/empty")
    assert "empty" in out.lower()


def test_web_fetch_includes_status_in_header():
    def handler(req):
        return _html_response("<p>OK</p>", status=200)
    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://example.com/")
    assert "status=200" in out
    assert "example.com" in out


def test_web_fetch_network_error_returns_error():
    def handler(req):
        raise httpx.ConnectError("connection refused")

    with _with_mocked_client(handler):
        out = main.run_web_fetch("https://unreachable.test/")
    assert "Error" in out


def test_web_fetch_registered_in_tool_registry():
    from loom.agent.tools import TOOL_REGISTRY
    tool = TOOL_REGISTRY.get("web_fetch")
    assert tool is not None
    assert tool.is_read_only is True
    assert tool.is_concurrent_safe is True
    assert "url" in tool.input_schema["required"]


def test_web_fetch_in_subagent_tools():
    from loom.agent.tools import SUB_HANDLERS, SUB_TOOLS
    sub_names = {t["name"] for t in SUB_TOOLS}
    assert "web_fetch" in sub_names
    assert "web_fetch" in SUB_HANDLERS
