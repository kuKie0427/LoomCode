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
