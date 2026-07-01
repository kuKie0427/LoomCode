"""Tests for agent-loop bottleneck fixes (B1 / B2 / B3 / L1 / R1 / R2 / L2 / L3 / L5 / L6).

B1: _run_tool_block sets is_error=True when a tool handler raises.
B2: _token_cache invalidated after autocompact / _raw_truncate_fallback.
B3: _count_tokens_accurate skips Anthropic API for non-Anthropic providers.
L1: stream_iter retries on retryable ProviderError before first event.
R1: _token_cache has a size cap to prevent unbounded growth.
R2: cached entries verify message-list length to mitigate id() reuse.
L2: _run_tool_turn runs concurrent-safe tools (read_file/glob/grep/...) in parallel.
L3: openai_chat_stream reuses pooled httpx.Client keyed by base_url.
L5: _count_tokens_accurate is throttled to 1 fresh API call per throttle window.
L6: checkpoint / session_store offload disk I/O to a background thread.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from loom.agent.context import Context, _token_cache
from loom.agent.providers.types import (
    ProviderError,
    ProviderErrorCode,
    StreamEvent,
)


# L5: disable the count_tokens throttle by default for tests that make many
# successive _count_tokens_accurate calls in a tight loop (R1 fills the cache
# with 42 distinct lists, R2 fires 2 calls back-to-back). Without this
# autouse fixture, the L5 throttle (5s) would throttle all but the first
# call, breaking R1/R2. L5-specific tests that want to exercise the throttle
# explicitly monkeypatch _PRECISE_COUNT_THROTTLE_S back to a positive value.
@pytest.fixture(autouse=True)
def _l5_disable_throttle(monkeypatch):
    monkeypatch.setattr("loom.agent.context._PRECISE_COUNT_THROTTLE_S", 0.0)
    monkeypatch.setattr("loom.agent.context._last_precise_call_ts", 0.0)
    yield

# ═══════════════════════════════════════════════════════════════════════════
# B1: is_error flag on exception-caught tool results
# ═══════════════════════════════════════════════════════════════════════════


class _FakeBlock:
    def __init__(self, name: str, input: dict | None = None, id: str = "t1") -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input or {}
        self.id = id


class _FakeHooks:
    def trigger_hooks(self, event, *args):
        return None


def test_b1_non_subagent_exception_sets_is_error_true():
    """B1: non-subagent tool raises → is_error must be True (was always False)."""
    from loom.agent.loop import _run_tool_block

    def boom(**kwargs):
        raise ValueError("disk full")

    block = _FakeBlock("read_file", {"path": "/x"})
    with patch("loom.agent.loop.get_tool_handlers", return_value={"read_file": boom}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["is_error"] is True, (
        "is_error must be True when the tool handler raises (B1 fix)"
    )
    assert "ValueError" in result["content"]


def test_b1_subagent_exception_sets_is_error_true():
    """B1: subagent tool raises → is_error must be True."""
    from loom.agent.loop import _run_tool_block

    def boom(**kwargs):
        raise RuntimeError("subagent exploded")

    block = _FakeBlock("task", {"description": "will fail"})
    with patch("loom.agent.loop.get_tool_handlers", return_value={"task": boom}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["is_error"] is True, (
        "is_error must be True when subagent handler raises (B1 fix)"
    )


def test_b1_success_keeps_is_error_false():
    """B1: successful tool call must still return is_error=False."""
    from loom.agent.loop import _run_tool_block

    def ok(**kwargs):
        return "done"

    block = _FakeBlock("bash", {"command": "ls"})
    with patch("loom.agent.loop.get_tool_handlers", return_value={"bash": ok}):
        with patch("loom.agent.loop._active_callbacks", None):
            result = _run_tool_block(block, _FakeHooks())

    assert result["is_error"] is False


def test_b1_detect_repeated_failures_triggers_on_exception_results():
    """B1 integration: detect_repeated_failures now sees exception-caught
    failures (is_error=True), so the retry-guidance safety net fires."""
    from loom.agent.tool_errors import detect_repeated_failures

    def _tu(name, inp, tid):
        return {"type": "tool_use", "id": tid, "name": name, "input": inp}

    def _tr(tid, is_error, content="fail"):
        return {"type": "tool_result", "tool_use_id": tid,
                "is_error": is_error, "content": content}

    messages = [
        {"role": "assistant", "content": [_tu("read_file", {"path": "/x"}, "t1")]},
        {"role": "user", "content": [_tr("t1", True)]},
        {"role": "assistant", "content": [_tu("read_file", {"path": "/x"}, "t2")]},
        {"role": "user", "content": [_tr("t2", True)]},
        {"role": "assistant", "content": [_tu("read_file", {"path": "/x"}, "t3")]},
        {"role": "user", "content": [_tr("t3", True)]},
    ]
    detection = detect_repeated_failures(messages)
    assert detection is not None
    assert detection["tool"] == "read_file"
    assert detection["failure_count"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# B2: token cache invalidation after autocompact
# ═══════════════════════════════════════════════════════════════════════════


def test_b2_autocompact_invalidates_token_cache():
    """B2: after autocompact (clear+extend), _token_cache must not return
    the pre-compact stale count."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "first round " + "x" * 2000},
        {"role": "assistant", "content": [{"type": "text", "text": "reply " + "y" * 2000}]},
        {"role": "user", "content": "second round " + "z" * 2000},
        {"role": "assistant", "content": [{"type": "text", "text": "reply2 " + "w" * 2000}]},
        {"role": "user", "content": "latest question"},
    ]
    # Populate cache with a stale (large) count as if pre-compact.
    _token_cache[id(messages)] = (999999, len(messages))
    assert id(messages) in _token_cache

    fake_llm = MagicMock()
    fake_llm.model = "anthropic/claude-sonnet-4-5"
    fake_llm.invoke.return_value = MagicMock(content=[MagicMock(type="text", text="summary")])

    # Force autocompact to actually compact (head_messages exist, summary succeeds).
    with patch.object(Context, "_find_tail_cutoff", return_value=2):
        with patch.object(Context, "_align_to_round_start", return_value=2):
            with patch.object(Context, "_extract_last_todo", return_value=None):
                ctx.autocompact(messages, fake_llm, 200000)

    assert id(messages) not in _token_cache, (
        "token cache must be invalidated after autocompact (B2 fix)"
    )


def test_b2_raw_truncate_fallback_invalidates_token_cache():
    """B2: _raw_truncate_fallback must also invalidate the cache."""
    ctx = Context()
    messages = [
        {"role": "user", "content": "head " + "x" * 2000},
        {"role": "user", "content": "tail message"},
    ]
    _token_cache[id(messages)] = (999999, len(messages))

    tail = [{"role": "user", "content": "tail message"}]
    ctx._raw_truncate_fallback(messages, tail, None)

    assert id(messages) not in _token_cache


# ═══════════════════════════════════════════════════════════════════════════
# R1+R2: token cache eviction + id reuse defense
# ═══════════════════════════════════════════════════════════════════════════


def test_r1_cache_does_not_grow_unbounded(monkeypatch):
    """R1: _token_cache is cleared when it exceeds _TOKEN_CACHE_MAX_SIZE."""
    from loom.agent.context import _TOKEN_CACHE_MAX_SIZE, _count_tokens_accurate

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                return MagicMock(input_tokens=10)

    monkeypatch.setattr("loom.agent.context.Anthropic", _FakeAnthropic)
    _token_cache.clear()

    # Fill cache well beyond the cap with distinct message lists.
    for i in range(_TOKEN_CACHE_MAX_SIZE + 10):
        msgs = [{"role": "user", "content": f"msg {i}"}]
        _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")

    assert len(_token_cache) <= _TOKEN_CACHE_MAX_SIZE, (
        f"cache must be capped at {_TOKEN_CACHE_MAX_SIZE}, "
        f"got {len(_token_cache)}"
    )


def test_r2_stale_entry_with_changed_length_treated_as_miss(monkeypatch):
    """R2: if len(messages) changed since the cache entry was written,
    the entry is treated as a miss and recounted."""
    from loom.agent.context import _count_tokens_accurate

    call_count = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                call_count["n"] += 1
                return MagicMock(input_tokens=42)

    monkeypatch.setattr("loom.agent.context.Anthropic", _FakeAnthropic)
    _token_cache.clear()

    messages = [{"role": "user", "content": "short"}]
    result1 = _count_tokens_accurate(messages, "anthropic/claude-sonnet-4-5")
    assert result1 == 42
    assert call_count["n"] == 1

    # Same list object, same id — but we already cached it. No new call.
    result2 = _count_tokens_accurate(messages, "anthropic/claude-sonnet-4-5")
    assert result2 == 42
    assert call_count["n"] == 1, "same list, same length → cache hit"

    # Mutate in place: append a message (same id, different length).
    messages.append({"role": "user", "content": "added"})
    _count_tokens_accurate(messages, "anthropic/claude-sonnet-4-5")
    assert call_count["n"] == 2, (
        "length changed → cache miss → must recount"
    )


# ═══════════════════════════════════════════════════════════════════════════
# B3: _count_tokens_accurate skips non-Anthropic providers
# ═══════════════════════════════════════════════════════════════════════════


def test_b3_skips_anthropic_api_for_deepseek(monkeypatch):
    """B3: _count_tokens_accurate must NOT call Anthropic API for DeepSeek."""
    from loom.agent.context import _count_tokens_accurate

    def _no_anthropic(*args, **kwargs):
        raise AssertionError(
            "Anthropic() must not be called for non-Anthropic providers (B3)"
        )

    monkeypatch.setattr("loom.agent.context.Anthropic", _no_anthropic)
    messages = [{"role": "user", "content": "hello world"}]
    result = _count_tokens_accurate(messages, "deepseek/deepseek-v4-flash")
    assert result == -1


def test_b3_skips_anthropic_api_for_openai(monkeypatch):
    """B3: OpenAI provider also must not trigger Anthropic count_tokens."""
    from loom.agent.context import _count_tokens_accurate

    monkeypatch.setattr(
        "loom.agent.context.Anthropic",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Anthropic for OpenAI")),
    )
    result = _count_tokens_accurate(
        [{"role": "user", "content": "hi"}], "openai/gpt-4o"
    )
    assert result == -1


def test_b3_proceeds_for_anthropic_model(monkeypatch):
    """B3: Anthropic models still use the accurate count_tokens API."""
    from loom.agent.context import _count_tokens_accurate

    called = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            called["n"] += 1

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                return MagicMock(input_tokens=42)

    monkeypatch.setattr("loom.agent.context.Anthropic", _FakeAnthropic)
    # Clear cache to ensure a fresh call.
    _token_cache.clear()
    result = _count_tokens_accurate(
        [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
    )
    assert called["n"] == 1
    assert result == 42


def test_b3_bare_claude_model_proceeds(monkeypatch):
    """B3: bare 'claude-*' model strings (no provider prefix) are treated
    as Anthropic for backward compat."""
    from loom.agent.context import _count_tokens_accurate

    called = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            called["n"] += 1

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                return MagicMock(input_tokens=7)

    monkeypatch.setattr("loom.agent.context.Anthropic", _FakeAnthropic)
    _token_cache.clear()
    result = _count_tokens_accurate(
        [{"role": "user", "content": "hi"}], "claude-haiku-4-5"
    )
    assert called["n"] == 1
    assert result == 7


# ═══════════════════════════════════════════════════════════════════════════
# L1: stream_iter retries on transient errors
# ═══════════════════════════════════════════════════════════════════════════


def _make_client_with_fake_stream(fake_stream_fn):
    """Create an LLMClient shell with a fake provider.stream."""
    from loom.agent.llm import LLMClient

    client = LLMClient.__new__(LLMClient)
    client._provider = MagicMock()
    client._provider.stream = fake_stream_fn
    client._cancelled = False
    client._cancel_event = threading.Event()
    client._provider_options = None
    client.model = "test/test-model"
    return client


@pytest.fixture
def instant_backoff(monkeypatch):
    """Make Event.wait return immediately (no real sleep) so retry tests are fast."""
    monkeypatch.setattr(threading.Event, "wait", lambda self, timeout=None: False)


def test_l1_retries_on_retryable_error_before_first_event(instant_backoff):
    """L1: stream_iter retries on retryable ProviderError and succeeds."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ProviderError(
                ProviderErrorCode.RATE_LIMIT, "rate limited", retryable=True
            )
        yield StreamEvent(kind="text", text="recovered")
        yield StreamEvent(
            kind="usage", input_tokens=10, output_tokens=5, stop_reason="end_turn"
        )

    client = _make_client_with_fake_stream(fake_stream)
    events = list(
        client.stream_iter("sys", [{"role": "user", "content": "hi"}], [], 100)
    )

    assert call_count["n"] == 3, f"should retry twice, got {call_count['n']} calls"
    assert any(ev.kind == "text" and ev.text == "recovered" for ev in events)


def test_l1_does_not_retry_non_retryable_error(instant_backoff):
    """L1: non-retryable errors (auth) propagate immediately."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        raise ProviderError(ProviderErrorCode.AUTH, "bad key", retryable=False)

    client = _make_client_with_fake_stream(fake_stream)
    with pytest.raises(ProviderError) as exc_info:
        list(client.stream_iter("sys", [{"role": "user", "content": "hi"}], [], 100))

    assert call_count["n"] == 1, "non-retryable error must not retry"
    assert exc_info.value.retryable is False


def test_l1_does_not_retry_after_first_event(instant_backoff):
    """L1: once events are yielded, mid-stream errors are NOT retried."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        yield StreamEvent(kind="text", text="partial")
        raise ProviderError(
            ProviderErrorCode.NETWORK, "mid-stream drop", retryable=True
        )

    client = _make_client_with_fake_stream(fake_stream)
    events = []
    with pytest.raises(ProviderError):
        for ev in client.stream_iter("sys", [{"role": "user", "content": "hi"}], [], 100):
            events.append(ev)

    assert call_count["n"] == 1, "mid-stream errors must not retry"
    assert len(events) == 1
    assert events[0].text == "partial"


def test_l1_retries_on_network_error(instant_backoff):
    """L1: network errors are retryable."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ProviderError(
                ProviderErrorCode.NETWORK, "connection reset", retryable=True
            )
        yield StreamEvent(kind="text", text="ok after retry")

    client = _make_client_with_fake_stream(fake_stream)
    events = list(
        client.stream_iter("sys", [{"role": "user", "content": "hi"}], [], 100)
    )

    assert call_count["n"] == 2
    assert events[0].text == "ok after retry"


def test_l1_gives_up_after_max_retries(instant_backoff):
    """L1: after max_retries (3), the error propagates."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        raise ProviderError(
            ProviderErrorCode.SERVER, "5xx", retryable=True
        )

    client = _make_client_with_fake_stream(fake_stream)
    with pytest.raises(ProviderError):
        list(client.stream_iter("sys", [{"role": "user", "content": "hi"}], [], 100))

    assert call_count["n"] == 4, f"1 initial + 3 retries = 4 calls, got {call_count['n']}"


def test_l1_invoke_benefits_from_stream_iter_retry(instant_backoff):
    """L1: invoke() calls stream_iter internally, so it also gets retry."""
    call_count = {"n": 0}

    def fake_stream(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ProviderError(
                ProviderErrorCode.RATE_LIMIT, "rate limited", retryable=True
            )
        yield StreamEvent(kind="text", text="success")
        yield StreamEvent(
            kind="usage", input_tokens=5, output_tokens=3, stop_reason="end_turn"
        )

    client = _make_client_with_fake_stream(fake_stream)
    response = client.invoke("sys", [{"role": "user", "content": "hi"}], [], 100)

    assert call_count["n"] == 2
    assert any(
        getattr(b, "text", "") == "success" for b in response.content
    )


# ═══════════════════════════════════════════════════════════════════════════
# L3: httpx.Client connection pool
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _l3_reset_pool():
    """Reset the httpx.Client pool before and after each L3 test to avoid
    cross-test contamination (cached clients from one test leaking into
    the next).
    """
    from loom.agent.providers._openai_shared import _close_http_client_pool

    _close_http_client_pool()
    yield
    _close_http_client_pool()


def test_l3_pool_returns_same_client_for_same_base_url():
    """L3: same base_url → same httpx.Client instance (connection reuse)."""
    from loom.agent.providers._openai_shared import _get_pooled_http_client

    c1 = _get_pooled_http_client("https://api.deepseek.com/v1")
    c2 = _get_pooled_http_client("https://api.deepseek.com/v1")
    assert c1 is c2, "same base_url must return the same cached client"


def test_l3_pool_returns_different_clients_for_different_base_urls():
    """L3: different base_urls → different httpx.Client instances."""
    from loom.agent.providers._openai_shared import _get_pooled_http_client

    c1 = _get_pooled_http_client("https://api.deepseek.com/v1")
    c2 = _get_pooled_http_client("https://api.openai.com/v1")
    assert c1 is not c2, "different base_urls must return different clients"


def test_l3_pool_evicts_oldest_at_capacity():
    """L3: pool has a max size; oldest entry is evicted (FIFO) on overflow."""
    from loom.agent.providers._openai_shared import (
        _HTTP_CLIENT_MAX_POOL_SIZE,
        _HTTP_CLIENT_POOL,
        _get_pooled_http_client,
    )

    # Fill the pool to capacity.
    for i in range(_HTTP_CLIENT_MAX_POOL_SIZE):
        _get_pooled_http_client(f"https://host{i}.example.com/v1")
    assert len(_HTTP_CLIENT_POOL) == _HTTP_CLIENT_MAX_POOL_SIZE
    first_key = next(iter(_HTTP_CLIENT_POOL))
    first_client = _HTTP_CLIENT_POOL[first_key]

    # Add one more — should evict the oldest (first_key).
    _get_pooled_http_client("https://overflow.example.com/v1")
    assert len(_HTTP_CLIENT_POOL) == _HTTP_CLIENT_MAX_POOL_SIZE, (
        "pool size must stay at cap after eviction"
    )
    assert first_key not in _HTTP_CLIENT_POOL, (
        f"oldest entry {first_key!r} should have been evicted"
    )
    # Evicted client should be closed (its underlying connection closed).
    assert first_client.is_closed, "evicted client must be closed"


def test_l3_close_pool_clears_all_entries():
    """L3: _close_http_client_pool empties the pool and closes all clients."""
    from loom.agent.providers._openai_shared import (
        _HTTP_CLIENT_POOL,
        _close_http_client_pool,
        _get_pooled_http_client,
    )

    _get_pooled_http_client("https://a.example.com/v1")
    _get_pooled_http_client("https://b.example.com/v1")
    assert len(_HTTP_CLIENT_POOL) == 2

    clients = list(_HTTP_CLIENT_POOL.values())
    _close_http_client_pool()

    assert _HTTP_CLIENT_POOL == {}, "pool must be empty after close"
    for c in clients:
        assert c.is_closed, "each pooled client must be closed"


def test_l3_openai_chat_stream_uses_pool_when_no_http_client_supplied():
    """L3: openai_chat_stream (no http_client arg) should pull from the
    pool and NOT close the client after the iterator is exhausted.
    """
    import httpx

    from loom.agent.providers._openai_shared import (
        _HTTP_CLIENT_POOL,
        openai_chat_stream,
    )
    from loom.agent.providers.types import ProviderRequest

    # Build a canned SSE response via MockTransport so no real network call.
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'
        b'data: [DONE]\n\n'
    )

    captured_clients: list[httpx.Client] = []

    def _stream_sse(request: httpx.Request) -> httpx.Response:
        # Record the client making the call by hooking into the transport.
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(_stream_sse)
    # Inject our transport into the pool by pre-creating the client.
    base_url = "https://api.deepseek.com/v1"
    pooled_client = httpx.Client(transport=transport, timeout=None)
    _HTTP_CLIENT_POOL[base_url] = pooled_client
    captured_clients.append(pooled_client)

    req = ProviderRequest(
        system="x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model="deepseek/deepseek-chat",
        max_tokens=100,
    )
    events = list(
        openai_chat_stream(
            req,
            base_url=base_url,
            api_key="k",
            model_id="deepseek-chat",
            provider="deepseek",
        )
    )

    text_events = [e for e in events if e.kind == "text"]
    assert len(text_events) == 1
    assert text_events[0].text == "hi"
    # The pooled client must NOT have been closed by openai_chat_stream.
    assert not pooled_client.is_closed, (
        "pooled client must remain open after stream exhausts (L3 ownership rule)"
    )
    # The pool still holds the same client.
    assert _HTTP_CLIENT_POOL.get(base_url) is pooled_client


def test_l3_openai_chat_stream_does_not_close_caller_supplied_client():
    """L3: caller-supplied http_client is owned by the caller; the stream
    must not close it. (Mirrors the pre-L3 behavior for the DI seam.)
    """
    import httpx

    from loom.agent.providers._openai_shared import openai_chat_stream
    from loom.agent.providers.types import ProviderRequest

    sse_body = (
        b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
        b'data: [DONE]\n\n'
    )

    def _stream_sse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(_stream_sse)
    caller_client = httpx.Client(transport=transport, timeout=60.0)

    req = ProviderRequest(
        system="",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model="openai/gpt-4o",
        max_tokens=100,
    )
    list(
        openai_chat_stream(
            req,
            base_url="https://api.openai.com/v1",
            api_key="k",
            model_id="gpt-4o",
            provider="openai",
            http_client=caller_client,
        )
    )

    assert not caller_client.is_closed, (
        "caller-supplied client must remain open — caller owns its lifetime"
    )
    caller_client.close()


def test_l3_pool_is_thread_safe():
    """L3: concurrent _get_pooled_http_client calls for the same base_url
    return the same client (no duplicate creation under contention).
    """
    from loom.agent.providers._openai_shared import (
        _HTTP_CLIENT_POOL,
        _get_pooled_http_client,
    )

    base_url = "https://concurrent.example.com/v1"
    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(_get_pooled_http_client(base_url))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert all(r is results[0] for r in results), (
        "all threads must get the same client instance for the same base_url"
    )
    assert len(_HTTP_CLIENT_POOL) == 1, "only one pool entry should exist"


# ═══════════════════════════════════════════════════════════════════════════
# L6: background disk writes for checkpoint / session_store
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _l6_flush_before_after():
    """Flush any pending writes before and after each L6 test to isolate
    them from each other.
    """
    from loom.agent.checkpoint import flush_pending_writes

    flush_pending_writes()
    yield
    flush_pending_writes()


def _fake_llm(model: str = "test-model"):
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.model = model
    return llm


class _FakeContext:
    def __init__(self, last_input_tokens: int = 100, checked_at_index: int = 5):
        self.last_input_tokens = last_input_tokens
        self.checked_at_index = checked_at_index


def test_l6_checkpoint_async_save_returns_path_immediately(tmp_path):
    """L6: save(async_io=True) returns the path immediately; the file may
    not be on disk yet (background write still in flight).
    """
    from loom.agent.checkpoint import default_path_for, save

    path = save(
        tmp_path,
        [{"role": "user", "content": "hi"}],
        _fake_llm(),
        _FakeContext(),
        tool_call_count=1,
        async_io=True,
    )
    assert path == default_path_for(tmp_path)
    # File may or may not exist yet — we only guarantee the path returned.
    # After flush it MUST exist.
    from loom.agent.checkpoint import flush_pending_writes

    assert flush_pending_writes(timeout=2.0) is True
    assert path.exists(), "file must exist after flush"


def test_l6_checkpoint_async_write_content_matches_sync(tmp_path):
    """L6: async write produces the same on-disk content as sync write."""
    import json

    from loom.agent.checkpoint import flush_pending_writes, load, save

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
    ]
    save(tmp_path, messages, _fake_llm("m1"), _FakeContext(500, 2), tool_call_count=7, async_io=True)
    assert flush_pending_writes(timeout=2.0) is True

    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded["model"] == "m1"
    assert loaded["messages"] == messages
    assert loaded["tool_call_count"] == 7
    assert loaded["last_input_tokens"] == 500
    assert loaded["checked_at_index"] == 2
    # Verify the raw JSON is parseable + matches (no corruption from async write)
    raw = (tmp_path / ".minicode" / "checkpoint.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["messages"] == messages


def test_l6_checkpoint_sync_save_still_works(tmp_path):
    """L6: save(async_io=False) (default) remains synchronous — file on disk
    before return. Locks in backward-compat for existing callers/tests.
    """
    from loom.agent.checkpoint import load, save

    path = save(tmp_path, [{"role": "user", "content": "sync"}], _fake_llm(), _FakeContext(), tool_call_count=0)
    assert path.exists(), "sync save must write before returning"
    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded["messages"] == [{"role": "user", "content": "sync"}]


def test_l6_flush_returns_true_when_no_pending_writes():
    """L6: flush_pending_writes with empty queue returns True immediately."""
    from loom.agent.checkpoint import flush_pending_writes

    # _l6_flush_before_after fixture already flushed, so queue is empty
    assert flush_pending_writes() is True


def test_l6_multiple_async_writes_serialize_last_wins(tmp_path):
    """L6: multiple async saves to the same path are serialized by the
    single-worker executor; the final on-disk content reflects the last call.
    """
    from loom.agent.checkpoint import flush_pending_writes, load, save

    for i in range(5):
        save(
            tmp_path,
            [{"role": "user", "content": f"msg-{i}"}],
            _fake_llm(f"model-{i}"),
            _FakeContext(i * 10, i),
            tool_call_count=i,
            async_io=True,
        )
    assert flush_pending_writes(timeout=5.0) is True

    loaded = load(tmp_path)
    assert loaded is not None
    # Last write wins
    assert loaded["model"] == "model-4"
    assert loaded["messages"] == [{"role": "user", "content": "msg-4"}]
    assert loaded["tool_call_count"] == 4


def test_l6_session_store_save_session_async(tmp_path):
    """L6: SessionStore.save_session(async_io=True) offloads file write to
    background; index update stays sync.
    """
    from loom.agent.checkpoint import flush_pending_writes
    from loom.agent.session_store import SessionStore

    store = SessionStore(tmp_path)
    sid = store.create_session(name="AsyncTest")
    messages = [{"role": "user", "content": "hello"}]

    path = store.save_session(
        sid, messages, _fake_llm("m"), _FakeContext(100, 1), tool_call_count=2, async_io=True
    )
    assert path is not None

    # Index update is sync — list_sessions reflects the save immediately.
    metas = store.list_sessions()
    assert len(metas) == 1
    assert metas[0].session_id == sid
    assert metas[0].message_count == 1
    assert metas[0].tool_call_count == 2

    # Session file may not be on disk yet — flush ensures durability.
    assert flush_pending_writes(timeout=2.0) is True
    assert path.exists(), "session file must exist after flush"

    loaded = store.load_session(sid)
    assert loaded is not None
    assert loaded["session_id"] == sid
    assert loaded["session_name"] == "AsyncTest"
    assert loaded["messages"] == messages


def test_l6_background_write_error_is_swallowed(tmp_path, monkeypatch):
    """L6: if the background write raises, the error is logged + swallowed;
    it must not crash the agent loop (best-effort persistence).
    """
    from loom.agent.checkpoint import flush_pending_writes, save

    # Make tempfile.mkstemp raise to simulate disk failure.
    def _failing_mkstemp(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr("loom.agent.checkpoint.tempfile.mkstemp", _failing_mkstemp)

    # Should not raise even though the write fails
    path = save(
        tmp_path,
        [{"role": "user", "content": "x"}],
        _fake_llm(),
        _FakeContext(),
        tool_call_count=1,
        async_io=True,
    )
    # flush must not raise either
    assert flush_pending_writes(timeout=2.0) is True
    # File was never written (mkstemp failed)
    assert not path.exists()


def test_l6_checkpoint_chmod_mode_applied_for_session_files(tmp_path):
    """L6: _submit_write(chmod_mode=0o600) actually applies the mode on
    POSIX (session files use 0o600 for user-only read).
    """
    import os
    import stat

    from loom.agent.checkpoint import _submit_write, flush_pending_writes

    path = tmp_path / "secret.json"
    fut = _submit_write(path, '{"x": 1}', chmod_mode=0o600)
    fut.result(timeout=2.0)  # wait for this specific write
    flush_pending_writes(timeout=1.0)

    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ═══════════════════════════════════════════════════════════════════════════
# L2: concurrent-safe tools run in parallel within a single tool turn
# ═══════════════════════════════════════════════════════════════════════════


def test_l2_helper_returns_true_for_concurrent_safe_tools():
    """L2: is_concurrent_safe_tool returns True for read_file/glob/grep/
    web_fetch/subagent_poll/subagent_list — the tools registered with
    is_concurrent_safe=True."""
    from loom.agent.tools import is_concurrent_safe_tool

    for name in (
        "read_file", "glob", "grep", "web_fetch",
        "subagent_poll", "subagent_list",
    ):
        assert is_concurrent_safe_tool(name) is True, (
            f"{name!r} should be concurrent-safe"
        )


def test_l2_helper_returns_false_for_stateful_tools():
    """L2: is_concurrent_safe_tool returns False for bash / write_file /
    edit_file / task / review — stateful tools that must run serially."""
    from loom.agent.tools import is_concurrent_safe_tool

    for name in (
        "bash", "write_file", "edit_file", "multi_edit", "edit_lines",
        "todo_write", "task", "review", "verify", "cold_archive",
        "lsp_rename_symbol",
    ):
        assert is_concurrent_safe_tool(name) is False, (
            f"{name!r} should NOT be concurrent-safe"
        )


def test_l2_helper_returns_false_for_unknown_tool():
    """L2: unknown tool name returns False (never crash on a missing tool)."""
    from loom.agent.tools import is_concurrent_safe_tool

    assert is_concurrent_safe_tool("does_not_exist_xyz") is False
    assert is_concurrent_safe_tool("") is False


def test_l2_concurrent_safe_tools_run_in_parallel():
    """L2: when the LLM emits multiple concurrent-safe tools (read_file x3),
    they must run in parallel. Verified by a threading.Barrier: if any tool
    runs serially, the barrier times out."""
    import concurrent.futures as _cf  # noqa: F401  (sanity check import path)
    import time

    from loom.agent.loop import _run_tool_turn

    barrier = threading.Barrier(3, timeout=2.0)
    started_at: list[float] = []
    finished_at: list[float] = []
    lock = threading.Lock()

    def slow_read(**kwargs):
        t = time.monotonic()
        with lock:
            started_at.append(t)
        # Block until all 3 concurrent-safe tools have entered — proves they
        # are running in parallel. If they were serial, the first tool would
        # wait forever (barrier never fills).
        barrier.wait()
        with lock:
            finished_at.append(time.monotonic())
        return "ok"

    blocks = [
        _FakeBlock("read_file", {"path": "/a"}, id="t1"),
        _FakeBlock("read_file", {"path": "/b"}, id="t2"),
        _FakeBlock("read_file", {"path": "/c"}, id="t3"),
    ]
    with patch("loom.agent.loop.get_tool_handlers",
               return_value={"read_file": slow_read}):
        with patch("loom.agent.loop._active_callbacks", None):
            results = _run_tool_turn(blocks, _FakeHooks())

    assert len(results) == 3
    # All tools finished — barrier was satisfied → they ran concurrently.
    assert barrier.broken is False, "barrier timed out — tools ran serially"
    # Results placed at correct indices by tool_use_id.
    contents = [r["content"] for r in results]
    assert contents == ["ok", "ok", "ok"]


def test_l2_single_concurrent_safe_tool_runs_without_threadpool():
    """L2: when only one concurrent-safe tool is in the batch, it runs in
    the main thread (no ThreadPoolExecutor spawned) — single-tool fast path."""
    from loom.agent.loop import _run_tool_turn

    def read_ok(**kwargs):
        return f"read:{kwargs.get('path')}"

    block = _FakeBlock("read_file", {"path": "/x"}, id="t1")
    with patch("loom.agent.loop.get_tool_handlers",
               return_value={"read_file": read_ok}):
        with patch("loom.agent.loop._active_callbacks", None):
            results = _run_tool_turn([block], _FakeHooks())

    assert results == [{
        "type": "tool_result", "tool_use_id": "t1",
        "content": "read:/x", "is_error": False,
    }]


def test_l2_stateful_tools_run_serially_in_order():
    """L2: bash + edit_file (stateful, non-task) run serially in their
    emitted order even when both are in the same batch. Verified by
    recording start order — second must start after first finishes."""
    import time

    from loom.agent.loop import _run_tool_turn

    events: list[str] = []
    lock = threading.Lock()

    def record(name):
        def _h(**kwargs):
            with lock:
                events.append(f"{name}:start")
            # Tiny sleep so a parallel scheduler would visibly interleave.
            time.sleep(0.02)
            with lock:
                events.append(f"{name}:end")
            return name
        return _h

    blocks = [
        _FakeBlock("bash", {"command": "echo 1"}, id="b1"),
        _FakeBlock("edit_file", {"path": "/x", "old_text": "a", "new_text": "b"}, id="e1"),
    ]
    with patch("loom.agent.loop.get_tool_handlers",
               return_value={"bash": record("bash"), "edit_file": record("edit_file")}):
        with patch("loom.agent.loop._active_callbacks", None):
            results = _run_tool_turn(blocks, _FakeHooks())

    # No interleaving — full bash start..end before edit_file start..end.
    assert events == ["bash:start", "bash:end", "edit_file:start", "edit_file:end"], (
        f"stateful tools must not interleave; got {events}"
    )
    # Results placed by index (not by finish order).
    assert [r["tool_use_id"] for r in results] == ["b1", "e1"]
    assert [r["content"] for r in results] == ["bash", "edit_file"]


def test_l2_mixed_batch_places_results_at_correct_indices():
    """L2: a batch mixing serial (write_file), concurrent-safe (read_file x2),
    and task tools — results must land at the index matching the LLM's
    emitted order, regardless of finish order."""
    import time

    from loom.agent.loop import _run_tool_turn

    def write_slow(**kwargs):
        time.sleep(0.01)
        return "wrote"

    def read_fast(**kwargs):
        return f"read:{kwargs.get('path')}"

    def task_slow(**kwargs):
        time.sleep(0.01)
        return "task_done"

    # Order: write_file (idx 0) → read_file (idx 1) → task (idx 2) → read_file (idx 3)
    blocks = [
        _FakeBlock("write_file", {"path": "/w", "content": "x"}, id="w1"),
        _FakeBlock("read_file", {"path": "/r1"}, id="r1"),
        _FakeBlock("task", {"description": "do thing"}, id="tk1"),
        _FakeBlock("read_file", {"path": "/r2"}, id="r2"),
    ]
    with patch("loom.agent.loop.get_tool_handlers",
               return_value={
                   "write_file": write_slow,
                   "read_file": read_fast,
                   "task": task_slow,
               }):
        with patch("loom.agent.loop._active_callbacks", None):
            results = _run_tool_turn(blocks, _FakeHooks())

    assert len(results) == 4
    # Each result is placed at the index of its tool_use block.
    assert results[0]["tool_use_id"] == "w1"
    assert results[0]["content"] == "wrote"
    assert results[1]["tool_use_id"] == "r1"
    assert results[1]["content"] == "read:/r1"
    assert results[2]["tool_use_id"] == "tk1"
    assert results[2]["content"] == "task_done"
    assert results[3]["tool_use_id"] == "r2"
    assert results[3]["content"] == "read:/r2"


def test_l2_concurrent_safe_pool_is_bounded_at_8_workers():
    """L2: even when the LLM emits a huge batch of concurrent-safe tools,
    the ThreadPoolExecutor is capped at 8 workers (avoids thread explosion
    on pathological batches)."""
    import time

    from loom.agent.loop import _run_tool_turn

    # Track concurrent in-flight count; record the peak.
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def read_slow(**kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.03)  # hold the slot so siblings pile up
        with lock:
            in_flight -= 1
        return "ok"

    # 20 concurrent-safe tools in one batch.
    blocks = [_FakeBlock("read_file", {"path": f"/f{i}"}, id=f"r{i}") for i in range(20)]
    with patch("loom.agent.loop.get_tool_handlers",
               return_value={"read_file": read_slow}):
        with patch("loom.agent.loop._active_callbacks", None):
            results = _run_tool_turn(blocks, _FakeHooks())

    assert len(results) == 20
    assert peak <= 8, f"peak in-flight {peak} exceeded cap of 8"
    assert peak >= 2, f"peak in-flight {peak} — tools did not run concurrently"


def test_l2_empty_batch_returns_empty_list():
    """L2: empty tool_uses → empty results list (no crash on the indexing)."""
    from loom.agent.loop import _run_tool_turn

    with patch("loom.agent.loop._active_callbacks", None):
        results = _run_tool_turn([], _FakeHooks())
    assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# L5: throttle for _count_tokens_accurate fresh API calls
# ═══════════════════════════════════════════════════════════════════════════
#
# The autouse fixture above disables the throttle for the whole file so
# R1/R2/B3 tests work unchanged. L5-specific tests below re-enable it via
# monkeypatch to verify the throttle actually fires.


def test_l5_first_call_is_not_throttled(monkeypatch):
    """L5: the very first _count_tokens_accurate call (cold start,
    _last_precise_call_ts=0) is never throttled — it goes through to the
    API. Without this, the throttle would block on session start."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 5.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    call_count = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                call_count["n"] += 1
                return MagicMock(input_tokens=42)

    monkeypatch.setattr(ctx_mod, "Anthropic", _FakeAnthropic)

    msgs = [{"role": "user", "content": "hi"}]
    result = _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")

    assert result == 42
    assert call_count["n"] == 1, "first call must go through to the API"


def test_l5_second_call_within_window_is_throttled(monkeypatch):
    """L5: a second fresh-API call (different message list → cache miss)
    within the throttle window returns -1 without hitting the API. This is
    the core throttle behavior that prevents 100-300ms HTTP roundtrips on
    every should_compact() check while in the threshold zone."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 5.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    call_count = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                call_count["n"] += 1
                return MagicMock(input_tokens=42)

    monkeypatch.setattr(ctx_mod, "Anthropic", _FakeAnthropic)

    # First call — distinct list, cold throttle → API call, returns 42.
    msgs1 = [{"role": "user", "content": "first"}]
    r1 = _count_tokens_accurate(msgs1, "anthropic/claude-sonnet-4-5")
    assert r1 == 42
    assert call_count["n"] == 1

    # Second call — DIFFERENT list (cache miss by id), within 5s window.
    # Throttle fires → returns -1, no API call.
    msgs2 = [{"role": "user", "content": "second"}]
    r2 = _count_tokens_accurate(msgs2, "anthropic/claude-sonnet-4-5")
    assert r2 == -1, "second fresh call within throttle window must return -1"
    assert call_count["n"] == 1, "throttle must prevent the second API call"


def test_l5_cache_hit_bypasses_throttle(monkeypatch):
    """L5: a cache hit (same id + same len) returns the cached count even
    within the throttle window — the throttle only gates FRESH API calls,
    not cache reads. Without this, the throttle would needlessly force -1
    for the common "same message list, multiple should_compact calls" case."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 5.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    call_count = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                call_count["n"] += 1
                return MagicMock(input_tokens=42)

    monkeypatch.setattr(ctx_mod, "Anthropic", _FakeAnthropic)

    msgs = [{"role": "user", "content": "cached"}]
    r1 = _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")
    assert r1 == 42
    assert call_count["n"] == 1

    # Same list object, same len → cache hit. Throttle is irrelevant.
    r2 = _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")
    assert r2 == 42, "cache hit must return cached count"
    assert call_count["n"] == 1, "cache hit must not trigger an API call"


def test_l5_call_after_window_expires_goes_through(monkeypatch):
    """L5: after the throttle window elapses, a fresh-API cache miss once
    again goes through to the API. Verifies the throttle is time-bounded,
    not a permanent block."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 5.0)
    # Simulate "last call was 10s ago" — well past the 5s window.
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", -10.0)
    _token_cache.clear()

    call_count = {"n": 0}

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                call_count["n"] += 1
                return MagicMock(input_tokens=99)

    monkeypatch.setattr(ctx_mod, "Anthropic", _FakeAnthropic)

    msgs = [{"role": "user", "content": "after window"}]
    r = _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")
    assert r == 99
    assert call_count["n"] == 1, "call after window must go through"


def test_l5_throttle_does_not_affect_non_anthropic_providers(monkeypatch):
    """L5: the throttle is checked AFTER the B3 non-Anthropic short-circuit,
    so DeepSeek/OpenAI/Ollama providers return -1 immediately (no throttle
    state mutation). Verifies the throttle is Anthropic-only."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    # Set a hostile throttle that would block everything if it fired.
    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 1000.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    # Non-Anthropic providers must short-circuit BEFORE the throttle.
    for model in ("deepseek/deepseek-v4-flash", "openai/gpt-4o", "ollama/llama3"):
        msgs = [{"role": "user", "content": "x"}]
        # Returns -1 immediately (B3); throttle state must not change.
        ts_before = ctx_mod._last_precise_call_ts
        r = _count_tokens_accurate(msgs, model)
        assert r == -1
        assert ctx_mod._last_precise_call_ts == ts_before, (
            f"throttle state must not mutate for non-Anthropic provider {model}"
        )


def test_l5_throttle_is_thread_safe(monkeypatch):
    """L5: concurrent threads calling _count_tokens_accurate on distinct
    message lists — only one wins the throttle slot, the rest get -1.
    Verifies the lock prevents two threads from both making API calls."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 5.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    call_count = {"n": 0}
    call_lock = threading.Lock()

    class _FakeAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                # Hold the API call briefly so concurrent threads pile up
                # at the throttle check.
                import time
                time.sleep(0.05)
                with call_lock:
                    call_count["n"] += 1
                return MagicMock(input_tokens=42)

    monkeypatch.setattr(ctx_mod, "Anthropic", _FakeAnthropic)

    # 4 threads, 4 distinct message lists → 4 cache misses, but only 1
    # should win the throttle slot and actually call the API.
    msgs_per_thread = [
        [{"role": "user", "content": f"thread-{i}"}] for i in range(4)
    ]
    results: list[int] = [0] * 4

    def _worker(idx):
        results[idx] = _count_tokens_accurate(msgs_per_thread[idx], "anthropic/claude-sonnet-4-5")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread won the slot and got 42; the rest got -1.
    api_calls = call_count["n"]
    assert api_calls == 1, (
        f"throttle must allow exactly 1 API call under contention; got {api_calls}"
    )
    assert results.count(42) == 1, (
        f"exactly one thread should get the real count; got {results}"
    )
    assert results.count(-1) == 3, (
        f"three threads should be throttled to -1; got {results}"
    )


def test_l5_failed_api_call_does_not_poison_cache(monkeypatch):
    """L5: when the API call fails after the throttle slot is reserved,
    the function returns -1 but does NOT write a bogus entry to the cache.
    The next call (after window) should retry. Verifies the failure path
    doesn't leave stale -1 entries that would shadow future real counts."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import _count_tokens_accurate, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 0.0)  # no throttle
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    class _FailingAnthropic:
        def __init__(self):
            pass

        class messages:
            @staticmethod
            def count_tokens(**kwargs):
                raise RuntimeError("API down")

    monkeypatch.setattr(ctx_mod, "Anthropic", _FailingAnthropic)

    msgs = [{"role": "user", "content": "fail"}]
    r = _count_tokens_accurate(msgs, "anthropic/claude-sonnet-4-5")
    assert r == -1, "API failure must return -1"
    # Critical: no cache entry written — otherwise future calls would
    # return the stale -1 forever.
    assert id(msgs) not in _token_cache, (
        "failed API call must not poison the cache with a -1 entry"
    )


def test_l5_should_compact_uses_cheap_estimate_when_throttled(monkeypatch):
    """L5 integration: when _count_tokens_accurate is throttled (returns -1),
    should_compact falls back to the cheap estimate rather than blocking.
    Verifies the -1 contract is honored by the caller."""
    from loom.agent import context as ctx_mod
    from loom.agent.context import Context, _token_cache

    monkeypatch.setattr(ctx_mod, "_PRECISE_COUNT_THROTTLE_S", 1000.0)
    monkeypatch.setattr(ctx_mod, "_last_precise_call_ts", 0.0)
    _token_cache.clear()

    # Make _count_tokens_accurate's Anthropic client never actually called
    # (throttle fires first). If it were called, this would blow up.
    def _explode(*a, **kw):
        raise AssertionError("throttle should have prevented this API call")

    monkeypatch.setattr(ctx_mod, "Anthropic", _explode)

    ctx = Context()
    # Build messages whose cheap estimate is well above the threshold.
    # estimate_tokens = sum(len(text)) // 4. We need >= context_window * 0.85.
    # context_window=1000 → need >= 850 tokens → need >= 3400 chars.
    big = "x" * 4000
    messages = [
        {"role": "user", "content": big},
        {"role": "assistant", "content": [{"type": "text", "text": big}]},
    ]
    # should_compact must return True (cheap estimate alone exceeds threshold)
    # WITHOUT calling the API (throttled).
    result = ctx.should_compact(messages, context_window=1000, model="anthropic/claude-sonnet-4-5")
    assert result is True, (
        "should_compact must return True from cheap estimate alone when throttled"
    )
