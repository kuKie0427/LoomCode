"""OpenAI Chat Completions provider (api.openai.com).

Streams `POST /chat/completions` with `stream: true` and yields
``StreamEvent``s of the loom provider abstraction. All wire-level
details (SSE parsing, tool-call delta accumulation, error mapping)
are delegated to ``_openai_shared``.

Not implemented in P1 (deferred):
  * OpenAI Responses API (newer protocol)
  * ``reasoning_effort`` parameter (o-series)
  * vision input
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import ClassVar

import httpx

from loom.agent.providers._openai_shared import (
    DEFAULT_WINDOW,
    build_request_body,
    openai_chat_stream,
)
from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.registry import register
from loom.agent.providers.types import ProviderRequest, StreamEvent


@register
class OpenAIProvider(LLMProvider):
    provider_id: ClassVar[str] = "openai"
    display_name: ClassVar[str] = "OpenAI"
    env_var: ClassVar[str] = "OPENAI_API_KEY"
    default_base_url: ClassVar[str | None] = "https://api.openai.com/v1"

    supported_models: ClassVar[list[str]] = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
        "o1",
        "o1-mini",
        "o3-mini",
    ]

    _CONTEXT_WINDOWS: ClassVar[dict[str, int]] = {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-3.5-turbo": 16_000,
        "o1": 200_000,
        "o1-mini": 128_000,
        "o3-mini": 200_000,
    }

    _PRICING: ClassVar[dict[str, PricingInfo]] = {
        "gpt-4o": PricingInfo(2.5, 10.0, None, None),
        "gpt-4o-mini": PricingInfo(0.15, 0.6, None, None),
        "gpt-4-turbo": PricingInfo(10.0, 30.0, None, None),
        "gpt-3.5-turbo": PricingInfo(0.5, 1.5, None, None),
        "o1": PricingInfo(15.0, 60.0, None, None),
        "o1-mini": PricingInfo(3.0, 12.0, None, None),
        "o3-mini": PricingInfo(1.1, 4.4, None, None),
    }

    def __init__(self, api_key: str = "", base_url: str | None = None) -> None:
        super().__init__(api_key=api_key, base_url=base_url)
        self._http: httpx.Client | None = None

    def _http_client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=60.0)
        return self._http

    def context_window(self, model: str) -> int:
        return self._CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)

    def pricing(self, model: str) -> PricingInfo | None:
        return self._PRICING.get(model)

    def count_tokens(self, messages: list[dict], model: str) -> int:
        # OpenAI does not expose a count_tokens API in the public surface.
        # Use the inherited char/4 heuristic with a 50% safety margin.
        base = super().count_tokens(messages, model)
        return int(base * 1.5)

    def _model_id_from_request(self, request: ProviderRequest) -> str:
        model_id = request.model
        if "/" in model_id:
            model_id = model_id.split("/", 1)[1]
        return model_id

    def stream(self, request: ProviderRequest) -> Iterator[StreamEvent]:
        model_id = self._model_id_from_request(request)
        return openai_chat_stream(
            request,
            base_url=self.base_url or self.default_base_url or "https://api.openai.com/v1",
            api_key=self.api_key,
            model_id=model_id,
            provider=self.provider_id,
        )

    def cancel(self) -> None:
        # No persistent connection to cancel — connections are per-call
        # and torn down when the iterator is exhausted. This method is
        # present for API parity with AnthropicProvider.
        return

    def build_body_for_test(self, request: ProviderRequest) -> dict:
        """Public helper used by tests to assert the body shape without
        making a real HTTP call."""
        return build_request_body(request, model_id=self._model_id_from_request(request), stream=True)

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Heuristic token estimate using json.dumps serialization."""
        try:
            return len(json.dumps(messages)) // 4
        except (TypeError, ValueError):
            return 0
