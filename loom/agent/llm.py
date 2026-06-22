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
    StreamEvent,
    ToolDefinition,
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
        if api_key is None:
            api_key = os.getenv(self._default_env_var(), "")
        if not base_url:
            base_url = os.getenv(self._default_base_url_env(), "") or None
        self._provider: LLMProvider = get_provider(
            self._provider_id, api_key=api_key, base_url=base_url
        )
        self.model = f"{self._provider_id}/{self._model_id}"
        self.api_key = api_key
        self.base_url = base_url or ""
        self.client = getattr(self._provider, "_client", None)
        self.async_client = getattr(self._provider, "_async_client", None)
        self._cancelled = False
        self._cancel_event = threading.Event()

    def _default_env_var(self) -> str:
        if self._provider_id == "anthropic":
            return "ANTHROPIC_API_KEY"
        return ""

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
        if provider_id == self._provider_id and self._provider.api_key:
            self._model_id = model_id
            self.model = f"{self._provider_id}/{self._model_id}"
            return
        self._provider_id = provider_id
        self._model_id = model_id
        self._provider = get_provider(
            provider_id, api_key=self._provider.api_key, base_url=self._provider.base_url
        )
        self.client = getattr(self._provider, "_client", None)
        self.async_client = getattr(self._provider, "_async_client", None)
        self.model = f"{self._provider_id}/{self._model_id}"

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

    def get_context_window(self) -> int:
        return self._provider.context_window(self._model_id)

    def pricing(self) -> PricingInfo | None:
        return self._provider.pricing(self._model_id)
