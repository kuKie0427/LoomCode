"""OpenAI-compatible provider for third-party services (DeepSeek, Ollama,
OpenRouter, etc.) that implement the OpenAI Chat Completions API.

The shared wire logic lives in ``_openai_shared.openai_chat_stream``;
this class adds the per-profile lookup tables (context windows, pricing)
and the ``register_compatible_profiles()`` factory.

To add a new compatible provider, add an entry to
``_openai_shared.MODEL_PROFILES`` — no new code is needed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any, ClassVar

from loom.agent.providers._openai_shared import (
    DEFAULT_WINDOW,
    MODEL_PROFILES,
    _strip_provider_prefix,
    make_compatible_provider_class,
    openai_chat_stream,
)
from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.registry import PROVIDERS, register
from loom.agent.providers.types import ProviderRequest, StreamEvent


class OpenAICompatibleProvider(LLMProvider):
    """Generic OpenAI Chat Completions provider.

    Concrete subclasses (e.g. ``OpenAICompatible_deepseek``) are produced
    by ``register_compatible_profiles()`` and pin their ``provider_id``,
    ``display_name``, ``env_var``, ``default_base_url``, ``supported_models``,
    ``_CONTEXT_WINDOWS`` and ``_PRICING`` from ``MODEL_PROFILES``.

    Do not instantiate this base class directly — use the registered
    per-profile subclasses (e.g. via ``get_provider("deepseek", api_key)``).
    """

    provider_id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    env_var: ClassVar[str] = ""
    default_base_url: ClassVar[str | None] = None
    supported_models: ClassVar[list[str]] = []

    _CONTEXT_WINDOWS: ClassVar[dict[str, int]] = {}
    _PRICING: ClassVar[dict[str, PricingInfo]] = {}
    _DEFAULT_BODY: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        *,
        _provider_id: str = "",
        _env_var: str = "",
        _base_url: str | None = None,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url)
        if not self.base_url and _base_url:
            self.base_url = _base_url
        if not self.api_key and _env_var:
            self.api_key = os.getenv(_env_var, "")
        # Canonical id survives even when the base class is instantiated
        # directly (self.provider_id is empty in that case) so error
        # messages and trace events still carry the right provider name.
        self._canonical_provider_id = _provider_id or self.provider_id

    def context_window(self, model: str) -> int:
        from loom.agent.models_dev import lookup_context_window

        # Try the model ID as-is first (e.g. "deepseek-chat").
        ctx = lookup_context_window(self.provider_id, model)
        if ctx is not None:
            return ctx
        # Some callers pass "provider/model" — strip prefix and try again.
        if "/" in model:
            short = model.split("/", 1)[1]
            ctx = lookup_context_window(self.provider_id, short)
            if ctx is not None:
                return ctx
        return self._CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)

    def pricing(self, model: str) -> PricingInfo | None:
        return self._PRICING.get(model)

    def count_tokens(self, messages: list[dict], model: str) -> int:
        # OpenAI-compatible providers have no public count_tokens API.
        # Use the inherited char/4 heuristic with a 50% safety margin,
        # matching OpenAIProvider.
        base = super().count_tokens(messages, model)
        return int(base * 1.5)

    def _model_id_from_request(self, request: ProviderRequest) -> str:
        return _strip_provider_prefix(request.model)

    def stream(self, request: ProviderRequest) -> Iterator[StreamEvent]:
        model_id = self._model_id_from_request(request)
        base_url = (
            self.base_url
            or self.default_base_url
            or "https://api.openai.com/v1"
        )
        # Per-request provider_options override the default body.
        # If set to None (or empty), use the profile's _DEFAULT_BODY.
        extra_body = request.provider_options or self._DEFAULT_BODY or None
        return openai_chat_stream(
            request,
            base_url=base_url,
            api_key=self.api_key,
            model_id=model_id,
            provider=self._canonical_provider_id or self.provider_id or "openai_compatible",
            extra_body=extra_body,
        )

    def cancel(self) -> None:  # pragma: no cover
        # Per-call connections; nothing to cancel.
        return

    def profile_summary(self) -> dict[str, Any]:
        """Return a snapshot of the bound profile (for diagnostics / tests)."""
        return {
            "provider_id": self.provider_id,
            "env_var": self.env_var,
            "default_base_url": self.default_base_url,
            "supported_models": list(self.supported_models),
            "context_windows": dict(self._CONTEXT_WINDOWS),
        }


def register_compatible_profiles() -> None:
    """Walk ``MODEL_PROFILES`` and register one ``OpenAICompatibleProvider``
    subclass per entry. Idempotent: re-registering the same profile is
    a no-op (the second registration overwrites with the same class).
    """
    for provider_id, profile in MODEL_PROFILES.items():
        if provider_id in PROVIDERS:
            continue
        cls = make_compatible_provider_class(provider_id, profile)
        register(cls)


def register_models_dev_providers() -> int:
    """Register OpenAI-compatible providers from the models.dev catalog.

    Iterates over the cached models.dev data and creates an
    ``OpenAICompatibleProvider`` subclass for every provider that
    exposes an ``api`` field (base URL).  Providers already registered
    (e.g. the hand-written ``AnthropicProvider``, ``OpenAIProvider``,
    or the ``MODEL_PROFILES`` entries) are **not** overwritten.

    Returns the number of newly registered providers.
    """
    from loom.agent.models_dev import _get_provider_data

    data = _get_provider_data()
    if not data:
        return 0

    count = 0
    for pid, entry in sorted(data.items()):
        if pid in PROVIDERS:
            continue  # existing provider takes priority
        api_url = entry.get("api") or ""
        if not api_url:
            continue  # no OpenAI-compatible base URL
        name = entry.get("name", pid)
        env_list = entry.get("env") or []
        env_var = env_list[0] if env_list else f"{pid.upper()}_API_KEY"

        # Append /v1 if the URL doesn't already have a path.
        base_url = api_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        models = sorted(entry.get("models", {}).keys())

        profile = {
            "display_name": name,
            "base_url": base_url,
            "env_var": env_var,
            "models": models,
            "context_windows": {},
            "pricing": {},
        }
        cls = make_compatible_provider_class(pid, profile)
        register(cls)
        count += 1

    return count
