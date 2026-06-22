"""Eval cases for f-multi-model-providers-p1 (concrete providers).

Locks the contracts for the OpenAI provider, the OpenAI-compatible
profiles (DeepSeek, Ollama, OpenRouter), the shared streaming logic,
the error mapping table, and the unified provider registry.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


def _check(name: str, condition: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, passed=bool(condition), detail=detail)


class MultiModelOpenAIProviderRegistered(EvalCase):
    name = "multi-model-p1-openai-provider-registered"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS
        ok = "openai" in PROVIDERS
        return _check(self.name, ok, f"PROVIDERS={list(PROVIDERS)}")


class MultiModelOpenAIPricingGpt4oHasExpectedShape(EvalCase):
    name = "multi-model-p1-openai-pricing-gpt4o-shape"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        p = get_provider("openai", "k")
        price = p.pricing("gpt-4o")
        if price is None:
            return _check(self.name, False, "pricing is None")
        ok = (
            price.input_usd_per_1m == 2.5
            and price.output_usd_per_1m == 10.0
        )
        return _check(
            self.name,
            ok,
            f"got input={price.input_usd_per_1m} output={price.output_usd_per_1m}",
        )


class MultiModelOpenAIContextWindowGpt4o128k(EvalCase):
    name = "multi-model-p1-openai-context-window-gpt4o"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        p = get_provider("openai", "k")
        window = p.context_window("gpt-4o")
        return _check(self.name, window == 128_000, f"window={window}")


class MultiModelOpenAIErrorMappingAuth(EvalCase):
    name = "multi-model-p1-openai-error-401-auth"

    def run(self) -> EvalResult:
        from loom.agent.providers._openai_shared import _map_status_error
        err = _map_status_error(
            status_code=401,
            body='{"error":{"message":"bad key"}}',
            provider="openai",
        )
        ok = err.code == "auth" and err.retryable is False
        return _check(self.name, ok, f"code={err.code} retryable={err.retryable}")


class MultiModelOpenAIErrorMappingRateLimit(EvalCase):
    name = "multi-model-p1-openai-error-429-rate-limit"

    def run(self) -> EvalResult:
        from loom.agent.providers._openai_shared import _map_status_error
        err = _map_status_error(
            status_code=429,
            body='{"error":{"message":"rate limited"}}',
            provider="openai",
        )
        ok = err.code == "rate_limit" and err.retryable is True
        return _check(self.name, ok, f"code={err.code} retryable={err.retryable}")


class MultiModelOpenAIErrorMappingContextOverflow(EvalCase):
    name = "multi-model-p1-openai-error-400-context-overflow"

    def run(self) -> EvalResult:
        from loom.agent.providers._openai_shared import _map_status_error
        err = _map_status_error(
            status_code=400,
            body='{"error":{"code":"context_length_exceeded","message":"too long"}}',
            provider="openai",
        )
        ok = err.code == "context_overflow"
        return _check(self.name, ok, f"code={err.code}")


class MultiModelOpenAICompatibleProfileDeepseekResolved(EvalCase):
    name = "multi-model-p1-compatible-profile-deepseek"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider, parse_model_id
        pid, mid = parse_model_id("deepseek/deepseek-chat")
        p = get_provider(pid, api_key="fake")
        ok = (
            p.provider_id == "deepseek"
            and p.base_url == "https://api.deepseek.com/v1"
            and p.context_window("deepseek-chat") == 64_000
        )
        return _check(
            self.name, ok,
            f"pid={p.provider_id} base={p.base_url} ctx={p.context_window('deepseek-chat')}",
        )


class MultiModelOpenAICompatibleProfileOllamaNoKey(EvalCase):
    name = "multi-model-p1-compatible-profile-ollama-no-key"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider
        p = get_provider("ollama", api_key="")
        ok = (
            p.provider_id == "ollama"
            and p.base_url == "http://localhost:11434/v1"
            and "llama3" in p.supported_models
        )
        return _check(self.name, ok, f"base={p.base_url} models={p.supported_models}")


class MultiModelOpenAICompatibleProfileOpenrouterWithSlashInModelId(EvalCase):
    name = "multi-model-p1-compatible-profile-openrouter-slash-in-model-id"

    def run(self) -> EvalResult:
        from loom.agent.providers import get_provider, parse_model_id
        pid, mid = parse_model_id("openrouter/anthropic/claude-3.5-sonnet")
        p = get_provider(pid, api_key="fake")
        ok = (
            pid == "openrouter"
            and mid == "anthropic/claude-3.5-sonnet"
            and p.base_url == "https://openrouter.ai/api/v1"
            and "anthropic/claude-3.5-sonnet" in p.supported_models
        )
        return _check(self.name, ok, f"pid={pid} mid={mid} base={p.base_url}")


class MultiModelRegistryContains6Providers(EvalCase):
    name = "multi-model-p1-registry-contains-5-providers"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS
        # P1 ships anthropic, openai, deepseek, ollama, openrouter
        # (the `custom` provider lands in P2 with the credential system).
        expected = {"anthropic", "openai", "deepseek", "ollama", "openrouter"}
        ok = expected.issubset(PROVIDERS.keys()) and len(PROVIDERS) >= 5
        return _check(
            self.name, ok,
            f"PROVIDERS={sorted(PROVIDERS)} expected_superset={sorted(expected)}",
        )


class MultiModelParseModelIdAllSupportedProviders(EvalCase):
    name = "multi-model-p1-parse-model-id-all-supported"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS, get_provider, parse_model_id

        cases = [
            "anthropic/claude-sonnet-4-5",
            "openai/gpt-4o",
            "deepseek/deepseek-chat",
            "ollama/llama3",
            "openrouter/anthropic/claude-3.5-sonnet",
        ]
        failures: list[str] = []
        for model_str in cases:
            try:
                pid, mid = parse_model_id(model_str)
                if pid not in PROVIDERS:
                    failures.append(f"{model_str!r}: pid {pid!r} not in PROVIDERS")
                    continue
                p = get_provider(pid, api_key="k")
                if p.provider_id != pid:
                    failures.append(
                        f"{model_str!r}: got provider_id={p.provider_id!r}"
                    )
            except Exception as exc:
                failures.append(f"{model_str!r}: {type(exc).__name__}: {exc}")

        ok = not failures
        return _check(self.name, ok, "; ".join(failures) if failures else "all parsed")
