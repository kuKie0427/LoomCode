"""Eval cases for f-multi-model-providers-p0 foundation.

Locks the contract for the LLMProvider abstraction: model ID parsing,
provider registry, type contracts, and that the agent loop / context /
tools have stopped importing anthropic SDK types directly.
"""

from __future__ import annotations

import inspect

from loom.eval.runner import EvalCase, EvalResult


def _check(name: str, condition: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, passed=bool(condition), detail=detail)


class MultiModelParseModelIdWithPrefix(EvalCase):
    name = "multi-model-p0-parse-model-id-with-prefix"

    def run(self) -> EvalResult:
        from loom.agent.providers import parse_model_id
        result = parse_model_id("anthropic/claude-sonnet-4-5")
        ok = result == ("anthropic", "claude-sonnet-4-5")
        return _check(self.name, ok, f"got {result}")


class MultiModelParseModelIdDefaultsToAnthropic(EvalCase):
    name = "multi-model-p0-parse-model-id-defaults-anthropic"

    def run(self) -> EvalResult:
        from loom.agent.providers import parse_model_id
        result = parse_model_id("claude-sonnet-4-5")
        ok = result == ("anthropic", "claude-sonnet-4-5")
        return _check(self.name, ok, f"got {result}")


class MultiModelGetProviderUnknownRaises(EvalCase):
    name = "multi-model-p0-get-provider-unknown-raises"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        from loom.agent.providers.types import ProviderError
        try:
            get_provider("nonexistent", "k")
            return _check(self.name, False, "no error raised")
        except ProviderError as e:
            return _check(self.name, e.code == "unknown_provider", f"code={e.code}")


class MultiModelAnthropicContextWindowKnown(EvalCase):
    name = "multi-model-p0-anthropic-context-window-known"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        p = get_provider("anthropic", "k")
        window = p.context_window("claude-sonnet-4-5")
        return _check(self.name, window == 200_000, f"window={window}")


class MultiModelAnthropicPricingSonnetHasExpectedShape(EvalCase):
    name = "multi-model-p0-anthropic-pricing-sonnet-shape"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        p = get_provider("anthropic", "k")
        price = p.pricing("claude-sonnet-4-5")
        if price is None:
            return _check(self.name, False, "pricing is None")
        ok = (
            price.input_usd_per_1m == 3.0
            and price.output_usd_per_1m == 15.0
            and price.cache_read_usd_per_1m == 0.30
            and price.cache_write_usd_per_1m == 3.75
        )
        return _check(
            self.name,
            ok,
            f"got input={price.input_usd_per_1m} output={price.output_usd_per_1m}",
        )


class MultiModelTypesUsageDataclass(EvalCase):
    name = "multi-model-p0-types-usage-dataclass"

    def run(self) -> EvalResult:
        from loom.agent.providers.types import Usage
        u = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        ok = u.input_tokens == 100 and u.output_tokens == 50 and u.total_tokens == 150
        return _check(self.name, ok, f"total={u.total_tokens}")


class MultiModelTypesContentBlockUnion(EvalCase):
    name = "multi-model-p0-types-content-block-union"

    def run(self) -> EvalResult:
        from loom.agent.providers.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
        text = TextBlock(text="hi")
        thinking = ThinkingBlock(thinking="reasoning")
        tu = ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})
        tr = ToolResultBlock(tool_use_id="t1", content="ok")
        ok = all([text.text == "hi", thinking.thinking == "reasoning", tu.name == "bash", tr.is_error is False])
        return _check(self.name, ok, "all blocks constructed")


class MultiModelLoopDoesNotImportAnthropicTypes(EvalCase):
    name = "multi-model-p0-loop-no-anthropic-types-import"

    def run(self) -> EvalResult:
        from loom.agent import loop as loop_mod
        src = inspect.getsource(loop_mod)
        ok = "from anthropic.types" not in src
        return _check(self.name, ok, "anthropic.types import removed from loop.py")


class MultiModelLLMClientDelegatesToProvider(EvalCase):
    name = "multi-model-p0-llm-client-delegates-to-provider"

    def run(self) -> EvalResult:
        from loom.agent.llm import LLMClient
        from loom.agent.providers import LLMProvider
        client = LLMClient(model="claude-sonnet-4-5")
        ok = isinstance(client._provider, LLMProvider) and client._provider.provider_id == "anthropic"
        return _check(self.name, ok, f"provider_id={client._provider.provider_id}")


class MultiModelLLMClientChangeModelSwitchesProvider(EvalCase):
    name = "multi-model-p0-llm-client-change-model"

    def run(self) -> EvalResult:
        from loom.agent.llm import LLMClient
        client = LLMClient(model="claude-sonnet-4-5")
        original_provider = client._provider
        client.change_model("claude-opus-4-1")
        ok = client.model == "anthropic/claude-opus-4-1" and client._provider is original_provider
        return _check(self.name, ok, f"model={client.model}")


class MultiModelRegistryContainsAnthropic(EvalCase):
    name = "multi-model-p0-registry-contains-anthropic"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS
        ok = "anthropic" in PROVIDERS
        return _check(self.name, ok, f"PROVIDERS={list(PROVIDERS)}")
