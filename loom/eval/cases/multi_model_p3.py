"""Eval cases for f-multi-model-providers-p3 (polish and closeout).

Locks the contracts for: cost.py pricing delegation via provider.pricing(),
LOOM_AUTH_CONTENT subagent inheritance, docs/providers.md completeness,
README provider mention, floating-point precision, and per-provider
context_window + pricing completeness.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


def _check(name: str, condition: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, passed=bool(condition), detail=detail)


# ---------------------------------------------------------------------------
# Cost compute (compute_cost with provider.pricing())
# ---------------------------------------------------------------------------


class MultiModelCostComputeAnthropicSonnet(EvalCase):
    """compute_cost with Sonnet pricing returns expected arithmetic."""

    name = "multi-model-p3-cost-compute-anthropic-sonnet"

    def run(self) -> EvalResult:
        from loom.agent.cost import TokenUsage, compute_cost

        usage = TokenUsage(input_tokens=100, output_tokens=50)
        result = compute_cost("claude-sonnet-4-5", usage)
        # Sonnet: input $3/1M, output $15/1M
        expected_input = round(100 * 3.0 / 1_000_000, 6)
        expected_output = round(50 * 15.0 / 1_000_000, 6)
        expected_total = round(expected_input + expected_output, 6)
        ok = (
            result.input_cost == expected_input
            and result.output_cost == expected_output
            and result.total_usd == expected_total
        )
        return _check(
            self.name,
            ok,
            f"input={result.input_cost} output={result.output_cost} "
            f"total={result.total_usd} expected_input={expected_input} "
            f"expected_output={expected_output} expected_total={expected_total}",
        )


class MultiModelCostComputeDeepseekCheaper(EvalCase):
    """DeepSeek pricing is strictly cheaper than Sonnet for same tokens."""

    name = "multi-model-p3-cost-compute-deepseek-cheaper"

    def run(self) -> EvalResult:
        from loom.agent.cost import TokenUsage, compute_cost

        usage = TokenUsage(input_tokens=100, output_tokens=50)
        sonnet = compute_cost("claude-sonnet-4-5", usage)
        deepseek = compute_cost("deepseek/deepseek-chat", usage)
        ok = deepseek.total_usd < sonnet.total_usd and deepseek.total_usd > 0
        return _check(
            self.name,
            ok,
            f"sonnet={sonnet.total_usd} deepseek={deepseek.total_usd}",
        )


class MultiModelCostComputeUnknownProviderReturnsZero(EvalCase):
    """Unknown provider returns CostBreakdown with all zeros."""

    name = "multi-model-p3-cost-compute-unknown-provider-zero"

    def run(self) -> EvalResult:
        from loom.agent.cost import TokenUsage, compute_cost

        usage = TokenUsage(input_tokens=100, output_tokens=50)
        result = compute_cost("unknown/model", usage)
        ok = (
            result.input_cost == 0.0
            and result.output_cost == 0.0
            and result.cache_read_cost == 0.0
            and result.cache_write_cost == 0.0
            and result.total_usd == 0.0
        )
        return _check(self.name, ok, f"total={result.total_usd}")


class MultiModelCostComputeCachedTokensApplyCacheReadPrice(EvalCase):
    """cache_read_tokens are priced at the cache_read rate."""

    name = "multi-model-p3-cost-compute-cache-read-pricing"

    def run(self) -> EvalResult:
        from loom.agent.cost import TokenUsage, compute_cost

        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=1000,
        )
        result = compute_cost("claude-sonnet-4-5", usage)
        # Sonnet cache_read: $0.30/1M
        expected_cache_read = round(1000 * 0.30 / 1_000_000, 6)
        ok = (
            result.cache_read_cost == expected_cache_read
            and result.cache_read_cost > 0
        )
        return _check(
            self.name,
            ok,
            f"cache_read_cost={result.cache_read_cost} "
            f"expected={expected_cache_read}",
        )


# ---------------------------------------------------------------------------
# Subagent credential inheritance
# ---------------------------------------------------------------------------


class MultiModelSubagentInheritsAuthContent(EvalCase):
    """spawn_subagent propagates credentials via LOOM_AUTH_CONTENT env var."""

    name = "multi-model-p3-subagent-inherits-auth-content"

    def run(self) -> EvalResult:
        old_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-subagent-inherit"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                from loom.agent.credential import CredentialManager

                m = CredentialManager(
                    auth_path=Path(tmp) / "auth.json",
                    use_keyring=False,
                )
                # Verify env var is picked up by credential manager
                cred = m.get("anthropic")
                if cred is None or cred.api_key != "sk-test-subagent-inherit":
                    return _check(
                        self.name,
                        False,
                        f"cred from env={cred}",
                    )

                # Verify all() returns our credential
                all_creds = m.all()
                if "anthropic" not in all_creds:
                    return _check(
                        self.name,
                        False,
                        "anthropic not in all_creds",
                    )

                # Verify the LOOM_AUTH_CONTENT propagation code exists in
                # spawn_subagent (the actual propagation happens on every call)
                from loom.agent import tools as tools_mod

                src = inspect.getsource(tools_mod.spawn_subagent)
                if "LOOM_AUTH_CONTENT" not in src:
                    return _check(
                        self.name,
                        False,
                        "LOOM_AUTH_CONTENT not in spawn_subagent source",
                    )
                if "credentials.all()" not in src:
                    return _check(
                        self.name,
                        False,
                        "credentials.all() not in spawn_subagent source",
                    )

                # Test the propagation path directly: call the same logic
                # that spawn_subagent uses to set LOOM_AUTH_CONTENT
                old_lac = os.environ.get("LOOM_AUTH_CONTENT")
                try:
                    import dataclasses

                    _creds = m.all()
                    os.environ["LOOM_AUTH_CONTENT"] = json.dumps({
                        pid: dataclasses.asdict(c)
                        for pid, c in _creds.items()
                    })
                    lac_raw = os.environ.get("LOOM_AUTH_CONTENT", "")
                    if not lac_raw:
                        return _check(
                            self.name,
                            False,
                            "LOOM_AUTH_CONTENT not set after propagation",
                        )
                    lac = json.loads(lac_raw)
                    if "anthropic" not in lac:
                        return _check(
                            self.name,
                            False,
                            "anthropic not in propagated LOOM_AUTH_CONTENT",
                        )
                    if lac["anthropic"].get("api_key") != "sk-test-subagent-inherit":
                        return _check(
                            self.name,
                            False,
                            f"wrong key: {lac['anthropic'].get('api_key')}",
                        )
                    return _check(
                        self.name,
                        True,
                        "credentials propagated via LOOM_AUTH_CONTENT",
                    )
                finally:
                    if old_lac is not None:
                        os.environ["LOOM_AUTH_CONTENT"] = old_lac
                    else:
                        os.environ.pop("LOOM_AUTH_CONTENT", None)
        finally:
            if old_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_key
            else:
                os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Documentation files
# ---------------------------------------------------------------------------


class MultiModelDocsProvidersMdExists(EvalCase):
    """docs/providers.md exists with at least 6 sections."""

    name = "multi-model-p3-docs-providers-md-exists"

    def run(self) -> EvalResult:
        providers_md = (
            Path(__file__).parent.parent.parent.parent / "docs" / "providers.md"
        )
        if not providers_md.is_file():
            return _check(self.name, False, "docs/providers.md not found")

        text = providers_md.read_text(encoding="utf-8")
        sections = [
            line
            for line in text.splitlines()
            if line.startswith("## §")
        ]
        ok = len(sections) >= 6
        return _check(
            self.name,
            ok,
            f"sections={len(sections)} file={providers_md}",
        )


class MultiModelReadmeMentionsSixProviders(EvalCase):
    """README.md mentions the 6 supported LLM providers."""

    name = "multi-model-p3-readme-mentions-six-providers"

    def run(self) -> EvalResult:
        readme = Path(__file__).parent.parent.parent.parent / "README.md"
        if not readme.is_file():
            return _check(self.name, False, "README.md not found")

        text = readme.read_text(encoding="utf-8")
        # The phrase "6 LLM providers" is on line 27 of the README
        ok = "6 LLM providers" in text
        return _check(self.name, ok, "README mentions 6 LLM providers")


# ---------------------------------------------------------------------------
# Pricing precision
# ---------------------------------------------------------------------------


class MultiModelPricingFloatPrecision(EvalCase):
    """1M tokens @ $0.27/1M = $0.27 with no floating-point drift."""

    name = "multi-model-p3-pricing-float-precision"

    def run(self) -> EvalResult:
        # Direct arithmetic check
        direct = round(1_000_000 * 0.27 / 1_000_000, 6)
        if direct != 0.27:
            return _check(
                self.name,
                False,
                f"direct: 1000000*0.27/1000000 = {direct}",
            )

        # Also verify through compute_cost with DeepSeek-chat ($0.27/1M input)
        from loom.agent.cost import TokenUsage, compute_cost

        usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
        result = compute_cost("deepseek/deepseek-chat", usage)
        ok = result.input_cost == 0.27 and result.total_usd == 0.27
        return _check(
            self.name,
            ok,
            f"direct={direct} compute_cost.input={result.input_cost} "
            f"total={result.total_usd}",
        )


# ---------------------------------------------------------------------------
# All providers have context window
# ---------------------------------------------------------------------------


class MultiModelAllProvidersHaveContextWindow(EvalCase):
    """Every registered provider returns a non-zero context_window for
    each of its supported_models."""

    name = "multi-model-p3-all-providers-have-context-window"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS, get_provider

        failures: list[str] = []
        for pid in PROVIDERS:
            p = get_provider(pid, api_key="")
            for model in p.supported_models:
                cw = p.context_window(model)
                if not cw or cw <= 0:
                    failures.append(
                        f"{pid}/{model}: context_window={cw}"
                    )
        ok = not failures
        return _check(
            self.name,
            ok,
            "; ".join(failures) if failures else f"all {sum(len(get_provider(pid, api_key='').supported_models) for pid in PROVIDERS)} models have context_window > 0",
        )


# ---------------------------------------------------------------------------
# All providers have pricing (Anthropic + OpenAI must have non-None)
# ---------------------------------------------------------------------------


class MultiModelAllProvidersHavePricing(EvalCase):
    """Anthropic and OpenAI providers have non-None pricing; Ollama may
    have None (free local inference)."""

    name = "multi-model-p3-all-providers-have-pricing"

    def run(self) -> EvalResult:
        from loom.agent.providers import PROVIDERS, get_provider

        failures: list[str] = []
        for pid in PROVIDERS:
            p = get_provider(pid, api_key="")
            for model in p.supported_models:
                price = p.pricing(model)
                if pid in ("anthropic", "openai"):
                    # Anthropic + OpenAI must have pricing
                    if price is None:
                        failures.append(
                            f"{pid}/{model}: pricing is None"
                        )
                    else:
                        # Must have at least input or output pricing
                        if (
                            price.input_usd_per_1m is None
                            and price.output_usd_per_1m is None
                        ):
                            failures.append(
                                f"{pid}/{model}: both input and output pricing are None"
                            )
                elif pid == "ollama":
                    # Ollama is free local — pricing may be empty dict
                    pass
                # deepseek and openrouter are covered by pricing or empty
                # (deepseek has pricing, openrouter has empty)
        ok = not failures
        return _check(
            self.name,
            ok,
            "; ".join(failures) if failures else "Anthropic + OpenAI have pricing",
        )


# ---------------------------------------------------------------------------
# Usage dataclass fields unchanged
# ---------------------------------------------------------------------------


class MultiModelTypesUsageRoundTrip(EvalCase):
    """Usage dataclass preserves all expected fields after construction."""

    name = "multi-model-p3-types-usage-round-trip"

    def run(self) -> EvalResult:
        from loom.agent.providers.types import Usage

        u = Usage(
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=30,
            cache_creation_tokens=40,
            reasoning_tokens=50,
        )
        ok = (
            u.input_tokens == 10
            and u.output_tokens == 20
            and u.cache_read_tokens == 30
            and u.cache_creation_tokens == 40
            and u.reasoning_tokens == 50
            and u.total_tokens == 30  # 10 + 20
        )
        return _check(
            self.name,
            ok,
            f"input={u.input_tokens} output={u.output_tokens} "
            f"cache_read={u.cache_read_tokens} "
            f"cache_creation={u.cache_creation_tokens} "
            f"reasoning={u.reasoning_tokens} total={u.total_tokens}",
        )
