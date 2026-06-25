import pytest

from loom.agent.llm import LLMClient
from tests._mock_provider import MockProvider


@pytest.fixture(autouse=True)
def _clear_provider_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear provider env vars that may have been loaded from .env by
    loom.agent.loop's module-level dotenv.load_dotenv().

    Also mock ``credentials.get`` to return None for all providers so
    tests don't pick up real keys from ``~/.loom/auth.json``. Without
    this, tests that set ``DEEPSEEK_API_KEY`` via monkeypatch still
    see the real DeepSeek key from auth.json (which has higher priority
    than env vars in ``LLMClient._resolve_credential``). This is a
    test-isolation concern, not a production issue — production correctly
    prefers auth.json over env vars.
    """
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
                "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "LOOM_AUTH_CONTENT"):
        monkeypatch.delenv(var, raising=False)
    # Mock credentials.get so auth.json doesn't leak real keys into tests.
    monkeypatch.setattr(
        "loom.agent.credential.credentials.get", lambda *a, **kw: None
    )


def test_init_creates_anthropic_client(mocker, monkeypatch):
    """__init__ creates an Anthropic client instance via the provider.

    Note: after the _resolve_credential refactor, get_provider is called
    with api_key="" when no explicit kwarg and no auth.json entry. The
    ANTHROPIC_API_KEY env var is then applied to the provider instance
    via the post-get_provider fallback (llm.py:51-54). This test verifies
    the env var fallback path: MockProvider starts with empty api_key,
    env_var is set to ANTHROPIC_API_KEY, and the fallback fills it in.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mock_prov = MockProvider(api_key="", base_url="https://example.com")
    # Override env_var so the fallback picks up ANTHROPIC_API_KEY.
    mock_prov.env_var = "ANTHROPIC_API_KEY"
    mocker.patch(
        "loom.agent.llm.get_provider",
        return_value=mock_prov,
    )
    client = LLMClient(model="test-model")

    # Env var fallback fills in the api_key after get_provider returns.
    assert client.api_key == "fake-key"
    assert client.model == "anthropic/test-model"


def test_init_uses_env_config(mocker, monkeypatch):
    """Uses env vars for api_key and base_url via the provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "my-api-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://custom.example.com")

    mock_prov = MockProvider(api_key="my-api-key", base_url="https://custom.example.com")
    mocker.patch(
        "loom.agent.llm.get_provider",
        return_value=mock_prov,
    )
    client = LLMClient(model="test-model")

    assert client.api_key == "my-api-key"
    assert client.base_url == "https://custom.example.com"


def test_change_model_updates_model_attr(mocker, monkeypatch):
    """change_model() updates the model attribute (always 'provider/model_id')."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mock_prov = MockProvider(api_key="fake-key", base_url="https://example.com")
    mocker.patch(
        "loom.agent.llm.get_provider",
        return_value=mock_prov,
    )
    client = LLMClient(model="original-model")

    client.change_model("new-model")

    assert client.model == "anthropic/new-model"


def test_get_context_window_known_model(monkeypatch):
    """Returns correct window for a known model.

    Uses ``deepseek/deepseek-chat`` so parse_model_id routes to the
    deepseek provider. Mocks models_dev cache to ensure deterministic
    results (the cache at ~/.loom/models_dev.json may have updated
    context windows that differ from the hardcoded fallback).
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setattr(
        "loom.agent.models_dev.lookup_context_window", lambda *a, **kw: None
    )

    client = LLMClient(model="deepseek/deepseek-chat")

    assert client.get_context_window() == 64_000  # hardcoded fallback


def test_get_context_window_unknown_model(monkeypatch):
    """Unknown model returns the default window."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    client = LLMClient(model="unknown-model")

    assert client.get_context_window() == 200_000


def test_get_context_window_after_change(monkeypatch):
    """Window updates after model change.

    Same models_dev mock as test_get_context_window_known_model for
    deterministic results.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setattr(
        "loom.agent.models_dev.lookup_context_window", lambda *a, **kw: None
    )

    client = LLMClient(model="unknown-model")

    client.change_model("deepseek/deepseek-chat")

    assert client.get_context_window() == 64_000  # hardcoded fallback


# ---------------------------------------------------------------------------
# Cross-provider change_model (f-multi-model-providers-p1 plan task 4)
# ---------------------------------------------------------------------------


def test_change_model_switches_provider_anthropic_to_openai(monkeypatch):
    """change_model switches from anthropic to openai and uses OPENAI_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    client = LLMClient(model="claude-sonnet-4-5")
    assert client.provider_id == "anthropic"

    client.change_model("openai/gpt-4o")

    assert client.provider_id == "openai"
    assert client.model_id == "gpt-4o"
    assert client.model == "openai/gpt-4o"
    # Cross-provider switch must re-resolve the api_key from the new provider's env var
    # (not carry the empty value from the openai provider's default).
    assert client.api_key == "openai-key"


def test_change_model_switches_to_deepseek(monkeypatch):
    """change_model switches to a deepseek profile and uses DEEPSEEK_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    client = LLMClient(model="claude-sonnet-4-5")

    client.change_model("deepseek/deepseek-chat")

    assert client.provider_id == "deepseek"
    assert client.model_id == "deepseek-chat"
    assert client.model == "deepseek/deepseek-chat"
    assert client.api_key == "deepseek-key"
    assert client.base_url == "https://api.deepseek.com/v1"


def test_change_model_resolves_new_provider_env_var_on_cross_provider_switch(monkeypatch):
    """Cross-provider switch re-resolves api_key from the new provider's env_var.
    Carrying over the old provider's key would be a security/correctness bug
    (a key issued for service A is invalid on service B).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    client = LLMClient(model="openai/gpt-4o", api_key="explicit-openai-key")
    assert client.api_key == "explicit-openai-key"

    client.change_model("deepseek/deepseek-chat")

    # The old OpenAI key is dropped; the new provider picks up DEEPSEEK_API_KEY.
    assert client.provider_id == "deepseek"
    assert client.api_key == "deepseek-key"


def test_init_openai_uses_env_var(monkeypatch):
    """LLMClient(model='openai/...') without explicit api_key picks up OPENAI_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")

    client = LLMClient(model="openai/gpt-4o")

    assert client.provider_id == "openai"
    assert client.api_key == "env-openai-key"


def test_init_deepseek_uses_env_var(monkeypatch):
    """LLMClient(model='deepseek/...') without explicit api_key picks up DEEPSEEK_API_KEY."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek-key")

    client = LLMClient(model="deepseek/deepseek-chat")

    assert client.provider_id == "deepseek"
    assert client.api_key == "env-deepseek-key"


def test_init_ollama_no_key_required(monkeypatch):
    """LLMClient(model='ollama/...') works with no API key (local default)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = LLMClient(model="ollama/llama3")

    assert client.provider_id == "ollama"
    assert client.api_key == ""
    assert client.base_url == "http://localhost:11434/v1"
