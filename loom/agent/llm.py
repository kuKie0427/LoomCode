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

from loom.agent.config import LLM_CONFIG
from loom.agent.providers import (
    LLMProvider,
    PricingInfo,
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
        # base_url first: ANTHROPIC_BASE_URL must be read before provider instantiation.
        if not base_url:
            base_url = os.getenv(self._default_base_url_env(), "") or None
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

    def _default_base_url_env(self) -> str:
        if self._provider_id == "anthropic":
            return "ANTHROPIC_BASE_URL"
        return ""

    def _provider_env_var(self) -> str:
        return getattr(self._provider, "env_var", "")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def change_model(self, new_model: str) -> None:
        provider_id, model_id = parse_model_id(new_model)
        # Same-provider fast path: just update model_id without re-instantiating.
        if provider_id == self._provider_id and self._provider.api_key:
            self._model_id = model_id
            self.model = f"{self._provider_id}/{self._model_id}"
            return
        # Cross-provider switch: do NOT carry over api_key/base_url from the
        # old provider — that key was issued for the old provider's service.
        # Let the new provider re-resolve its own env_var (e.g. OPENAI_API_KEY,
        # DEEPSEEK_API_KEY) inside its constructor.
        self._provider_id = provider_id
        self._model_id = model_id
        self._provider = get_provider(provider_id)
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

        request = ProviderRequest(
            system=system,
            messages=list(messages),
            tools=tool_defs,
            max_tokens=max_tokens,
            model=self.model,
        )

        yield from self._provider.stream(request)

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
        """
        content: list = []
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        reasoning_tokens = 0
        stop_reason = StopReason.END_TURN

        for ev in self.stream_iter(system, messages, tools, max_tokens):
            if ev.kind == "text":
                content.append(TextBlock(text=ev.text))
            elif ev.kind == "thinking":
                content.append(ThinkingBlock(thinking=ev.text))
            elif ev.kind == "tool_use":
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
                from loom.agent.providers.types import ProviderError
                raise ProviderError(
                    ev.error_code or "unknown",
                    ev.error_message or "provider error",
                )

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
