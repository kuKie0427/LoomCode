"""Thin wrapper around the LLMProvider abstraction.

`LLMClient` is kept for backward compatibility with existing call sites
(loop.py, tui/app.py, eval cases, tests). The Anthropic-specific logic
lives in `loom.agent.providers.anthropic.AnthropicProvider`; this module
is just a thin facade that resolves the model string to a provider and
delegates calls.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from loguru import logger

from loom.agent.config import LLM_CONFIG
from loom.agent.providers import (
    LLMProvider,
    PricingInfo,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    StopReason,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolUseBlock,
    Usage,
    get_provider,
    parse_model_id,
)
from loom.agent.providers.anthropic import (
    MIN_CACHEABLE_TOKENS,  # noqa: F401  (re-exported for test back-compat)
    with_cache_control,
    with_tool_cache_control,
)

DEFAULT_WINDOW = 128_000

__all__ = ["LLMClient", "StreamEvent", "with_cache_control", "with_tool_cache_control"]


class LLMClient:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self._provider_id, self._model_id = parse_model_id(model)
        api_key, base_url = self._resolve_credential(self._provider_id, api_key)
        self._provider: LLMProvider = get_provider(
            self._provider_id, api_key=api_key or "", base_url=base_url
        )
        # Provider-aware api_key fallback (explicit kwarg > OPENAI_API_KEY / DEEPSEEK_API_KEY / ...).
        if not self._provider.api_key:
            env_var = self._provider_env_var()
            if env_var:
                self._provider.api_key = os.getenv(env_var, "")
        self.model = f"{self._provider_id}/{self._model_id}"
        self.api_key = self._provider.api_key
        self.base_url = self._provider.base_url or ""
        self.client = getattr(self._provider, "_client", None)
        self.async_client = getattr(self._provider, "_async_client", None)
        self._cancelled = False
        self._cancel_event = threading.Event()
        self._provider_options: dict | None = None

    def _default_base_url_env(self, provider_id: str | None = None) -> str:
        pid = provider_id or self._provider_id
        if pid == "anthropic":
            return "ANTHROPIC_BASE_URL"
        return ""

    def _provider_env_var(self) -> str:
        return getattr(self._provider, "env_var", "")

    def _resolve_credential(
        self, provider_id: str, explicit_api_key: str | None
    ) -> tuple[str, str | None]:
        """Priority: explicit kwarg > CredentialManager > per-provider env var.
        Returns (api_key, base_url). base_url from cred only if not in env.
        """
        from loom.agent.credential import credentials

        api_key = explicit_api_key or ""
        base_url: str | None = None
        if not explicit_api_key:
            cred = credentials.get(provider_id)
            if cred is not None:
                api_key = cred.api_key
                base_url = cred.base_url
        if not base_url:
            base_url = os.getenv(self._default_base_url_env(provider_id=provider_id), "") or None
        return api_key, base_url

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def set_provider_options(self, options: dict | None) -> None:
        """Set per-request provider options (e.g. reasoning_effort, thinking).

        Passed through to ``ProviderRequest.provider_options`` and merged
        into the request body by the provider's ``stream()`` method.  Set
        to ``None`` to use the provider's default body.
        """
        self._provider_options = options

    @property
    def supports_extended_thinking(self) -> bool:
        """Whether the current model emits thinking/reasoning stream events.

        Only the Anthropic provider with specific thinking-capable models
        (Claude Sonnet 4.5+, Opus 4.1+) produces ``StreamEvent(kind="thinking")``.
        Other providers (OpenAI-compatible DeepSeek, etc.) send thinking
        content through different channels (``reasoning_content`` in delta)
        that are not currently surfaced as thinking events — for those
        models the thinking spinner would spin without content until the
        first text delta, which is visual noise.
        """
        if self._provider_id != "anthropic":
            return False
        thinking_capable = frozenset({"claude-sonnet-4-5", "claude-opus-4-1"})
        return self._model_id in thinking_capable

    def change_model(self, new_model: str) -> None:
        provider_id, model_id = parse_model_id(new_model)
        # Same-provider fast path: just update model_id without re-instantiating.
        if provider_id == self._provider_id and self._provider.api_key:
            self._model_id = model_id
            self.model = f"{self._provider_id}/{self._model_id}"
            return
        # Cross-provider switch: do NOT carry over api_key/base_url from the
        # old provider — that key was issued for the old provider's service.
        api_key, base_url = self._resolve_credential(provider_id, None)
        self._provider_id = provider_id
        self._model_id = model_id
        self._provider = get_provider(provider_id, api_key=api_key, base_url=base_url)
        self.client = getattr(self._provider, "_client", None)
        self.async_client = getattr(self._provider, "_async_client", None)
        self.model = f"{self._provider_id}/{self._model_id}"
        # Mirror the new provider's state so callers see consistent api_key / base_url.
        self.api_key = self._provider.api_key
        self.base_url = self._provider.base_url or ""

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_event.set()
        cancel = getattr(self._provider, "cancel", None)
        if callable(cancel):
            cancel()

    def stream_iter(
        self,
        system,
        messages,
        tools,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        self._cancelled = False
        self._cancel_event = threading.Event()
        if max_tokens is None:
            max_tokens = LLM_CONFIG.max_output_tokens

        tool_defs: list[ToolDefinition] = []
        for t in tools or []:
            if isinstance(t, dict):
                tool_defs.append(
                    ToolDefinition(
                        name=t["name"],
                        description=t.get("description", ""),
                        input_schema=t.get("input_schema", {}),
                    )
                )
            else:
                tool_defs.append(
                    ToolDefinition(
                        name=t.name, description=t.description, input_schema=t.input_schema
                    )
                )

        from loom.agent.providers.types import TextBlock, ToolUseBlock

        # Replace domain objects with plain dicts so both the Anthropic
        # and OpenAI-compatible paths can JSON-serialize without crashing.
        clean_messages: list[dict] = []
        for msg in messages:
            msg = dict(msg)  # shallow copy
            content = msg.get("content")
            if isinstance(content, list):
                cleaned: list[dict] = []
                for block in content:
                    if isinstance(block, TextBlock):
                        cleaned.append({"type": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        cleaned.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                    elif isinstance(block, dict):
                        cleaned.append(block)
                    else:
                        cleaned.append({"type": "text", "text": str(block)})
                msg["content"] = cleaned
            clean_messages.append(msg)

        request = ProviderRequest(
            system=system,
            messages=list(clean_messages),
            tools=tool_defs,
            max_tokens=max_tokens,
            model=self.model,
            provider_options=self._provider_options,
        )

        # L1: retry on transient provider errors (network / rate_limit / server 5xx).
        # ProviderError.retryable gates whether we retry; once we've yielded any
        # event to the caller we stop retrying — mid-stream errors can't be
        # safely retried because partial output has already been consumed by
        # the caller (TUI showed it / agent_loop appended it to content_blocks).
        # Non-retryable errors (auth, invalid_request) propagate immediately.
        max_retries = 3
        for attempt in range(max_retries + 1):
            emitted_any = False
            try:
                for ev in self._provider.stream(request):
                    emitted_any = True
                    yield ev
                return  # stream completed successfully
            except ProviderError as exc:
                if not exc.retryable or emitted_any or attempt >= max_retries:
                    raise
                delay = min(2.0 ** attempt, 8.0)  # 1s, 2s, 4s capped at 8s
                logger.warning(
                    "LLM stream retryable error (attempt {}/{}): {} — retrying in {:.1f}s",
                    attempt + 1, max_retries, exc, delay,
                )
                # Sleep on the cancel event so user cancellation wakes us
                # immediately instead of waiting the full backoff delay.
                if self._cancel_event.wait(delay):
                    raise  # cancelled during backoff — re-raise ProviderError

    def invoke(
        self,
        system,
        messages,
        tools,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        """Single-turn provider-agnostic invoke.

        Consumes the provider stream and returns a ``ProviderResponse``
        with content blocks + usage + stop_reason. Used by call sites
        that previously reached for ``llm_client.client.messages.create(...)``
        (which is Anthropic-specific) — namely ``spawn_subagent`` and
        ``context._generate_summary``.

        Text deltas are coalesced: consecutive ``text`` stream events are
        accumulated into a single ``TextBlock`` rather than one TextBlock
        per delta. Without coalescing, OpenAI-compatible providers (DeepSeek,
        etc.) that emit one token per delta produce content lists with
        hundreds of single-char TextBlocks, which ``extract_text`` then
        joins with ``\\n`` — fragmenting protocol tags like ``<delta_report>``
        into ``<\\ndelta\\n_report\\n>`` and breaking every regex-based
        parser in the Triangle Protocol. Anthropic's SDK coalesces internally;
        this method must match that behavior for stream=False parity.
        """
        content: list = []
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        reasoning_tokens = 0
        stop_reason = StopReason.END_TURN
        # Coalesce consecutive text deltas into one TextBlock to match
        # the non-streaming Anthropic/OpenAI response shape.
        text_buf: list[str] = []

        def _flush_text() -> None:
            if text_buf:
                content.append(TextBlock(text="".join(text_buf)))
                text_buf.clear()

        for ev in self.stream_iter(system, messages, tools, max_tokens):
            if ev.kind == "text":
                text_buf.append(ev.text)
            elif ev.kind == "thinking":
                _flush_text()
                content.append(ThinkingBlock(thinking=ev.text))
            elif ev.kind == "tool_use":
                _flush_text()
                content.append(ToolUseBlock(
                    id=ev.tool_id,
                    name=ev.tool_name,
                    input=ev.tool_input or {},
                ))
            elif ev.kind == "usage":
                if ev.input_tokens:
                    input_tokens = ev.input_tokens
                if ev.output_tokens:
                    output_tokens = ev.output_tokens
                if ev.cache_read_tokens:
                    cache_read_tokens = ev.cache_read_tokens
                if ev.cache_creation_tokens:
                    cache_creation_tokens = ev.cache_creation_tokens
                if ev.reasoning_tokens:
                    reasoning_tokens = ev.reasoning_tokens
                if ev.stop_reason:
                    try:
                        stop_reason = StopReason(ev.stop_reason)
                    except ValueError:
                        stop_reason = StopReason.END_TURN
            elif ev.kind == "error":
                _flush_text()
                raise ProviderError(
                    ev.error_code or "unknown",
                    ev.error_message or "provider error",
                )
        _flush_text()

        return ProviderResponse(
            model=self.model,
            content=content,
            stop_reason=stop_reason,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                reasoning_tokens=reasoning_tokens,
            ),
        )

    def get_context_window(self) -> int:
        return self._provider.context_window(self._model_id)

    def pricing(self) -> PricingInfo | None:
        return self._provider.pricing(self._model_id)
