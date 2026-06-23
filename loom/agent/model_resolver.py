"""Model resolution with precedence: --model CLI flag > env MODEL > ProjectConfig
> ModelState default > PROVIDERS first default.
"""

from __future__ import annotations

from pathlib import Path


def resolve_model(
    workdir: Path,
    cli_model: str | None = None,
    env_model: str | None = None,
    config_model: str | None = None,
    state_model: str | None = None,
    provider_ids: list[str] | None = None,
) -> str:
    """Resolve the effective model string using a precedence chain.

    Priority (highest -> lowest):
      1. cli_model (from --model CLI flag)
      2. env_model (from MODEL env var)
      3. config_model (from ProjectConfig.model)
      4. state_model (from ModelState.default_model())
      5. First registered provider's first model (e.g. "anthropic/claude-sonnet-4-5")

    Args:
        workdir: Project working directory (for future use).
        cli_model: Model string from --model CLI flag.
        env_model: Model string from MODEL env var.
        config_model: Model string from ProjectConfig.model.
        state_model: Model string from ModelState.default_model().
        provider_ids: List of registered provider IDs. If None, loaded lazily.

    Returns:
        Resolved model string (e.g. "anthropic/claude-sonnet-4-5").
        Never returns None -- falls through to the first provider's first model.
    """
    if cli_model:
        return cli_model
    if env_model:
        return env_model
    if config_model:
        return config_model
    if state_model:
        return state_model
    # Final fallback: first provider's first model
    if provider_ids:
        return f"{provider_ids[0]}/claude-sonnet-4-5"
    # Lazy import to avoid circular imports
    from loom.agent.providers import PROVIDERS  # noqa: PLC0415

    if PROVIDERS:
        first_id = sorted(PROVIDERS.keys())[0]
        first_cls = PROVIDERS[first_id]
        try:
            inst = first_cls(api_key="", base_url=None)
            models = inst.supported_models
            if models:
                return f"{first_id}/{models[0]}"
        except Exception:
            pass
        return f"{first_id}/claude-sonnet-4-5"
    return "anthropic/claude-sonnet-4-5"
