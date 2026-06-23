"""Mock LLMProvider for replacing MagicMock patterns in tests.

Usage:
    from tests._mock_provider import MockProvider, make_mock_provider

    provider = make_mock_provider([
        StreamEvent(kind="text", text="Hello"),
        StreamEvent(kind="usage", input_tokens=10, output_tokens=5),
    ])
"""

from __future__ import annotations

from collections.abc import Iterator

from loom.agent.providers.base import LLMProvider, PricingInfo
from loom.agent.providers.types import ProviderRequest, StreamEvent


class MockProvider(LLMProvider):
    """A test-double LLMProvider that yields configurable StreamEvents.

    Replaces ``MagicMock(spec=Anthropic)`` patterns in tests. Not registered
    in the PROVIDERS registry — it is a test mock.
    """

    provider_id = "mock"
    display_name = "Mock"
    env_var = "MOCK_API_KEY"
    default_base_url = "http://mock"
    supported_models = ["mock-model"]

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        responses: list[StreamEvent] | None = None,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url)
        self.responses: list[StreamEvent] = responses or []
        self.stream_call_count: int = 0

    def pricing(self, model: str) -> PricingInfo:
        """Return fixed mock pricing (distinct from real providers)."""
        return PricingInfo(
            input_usd_per_1m=0.001,
            output_usd_per_1m=0.002,
            cache_read_usd_per_1m=0.0001,
            cache_write_usd_per_1m=0.0002,
        )

    def count_tokens(self, messages: list[dict], model: str) -> int:
        """Return a deterministic count: len(messages) * 10."""
        return len(messages) * 10

    def context_window(self, model: str) -> int:
        """Return a fixed context window."""
        return 100000

    def stream(self, request: ProviderRequest) -> Iterator[StreamEvent]:
        """Yield configured responses, then auto-yield usage if needed.

        Increments ``stream_call_count`` on each invocation.
        """
        self.stream_call_count += 1

        if not self.responses:
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=5)
            return

        yield from self.responses

        # Auto-yield usage if the last event is not already usage.
        if self.responses[-1].kind != "usage":
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=5)


def make_mock_provider(
    events: list[StreamEvent] | None = None,
) -> MockProvider:
    """Convenience helper to construct a MockProvider.

    Args:
        events: StreamEvents to yield on the first (and each) stream call.
                Defaults to an empty list (provider yields usage only).

    Returns:
        A configured MockProvider instance.
    """
    return MockProvider(responses=events or [])
