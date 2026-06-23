from loom.agent.llm import LLMClient

ANTHROPIC_PATCH = "loom.agent.providers.anthropic.anthropic.Anthropic"


def test_init_creates_anthropic_client(mocker, monkeypatch):
    """__init__ creates an Anthropic client instance via the provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mock_anthropic_cls = mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="test-model")

    mock_anthropic_cls.assert_called()
    assert client.client is mock_anthropic_cls.return_value


def test_init_uses_env_config(mocker, monkeypatch):
    """Uses env vars for api_key and base_url via the provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "my-api-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://custom.example.com")

    mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="test-model")

    assert client.api_key == "my-api-key"
    assert client.base_url == "https://custom.example.com"


def test_change_model_updates_model_attr(mocker, monkeypatch):
    """change_model() updates the model attribute (always 'provider/model_id')."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="original-model")

    client.change_model("new-model")

    assert client.model == "anthropic/new-model"


def test_get_context_window_known_model(mocker, monkeypatch):
    """Returns correct window for a known model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="deepseek-v4-flash")

    assert client.get_context_window() == 64_000


def test_get_context_window_unknown_model(mocker, monkeypatch):
    """Unknown model returns the default window."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="unknown-model")

    assert client.get_context_window() == 200_000


def test_get_context_window_after_change(mocker, monkeypatch):
    """Window updates after model change."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

    mocker.patch(ANTHROPIC_PATCH)
    client = LLMClient(model="unknown-model")

    client.change_model("deepseek-v4-flash")

    assert client.get_context_window() == 64_000


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
