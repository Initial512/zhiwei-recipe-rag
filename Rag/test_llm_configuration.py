from unittest.mock import patch

from main import GenerationIntegrationModule


def test_llm_configuration_comes_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    with patch("rag_modules.generation_integration.OpenAI") as openai:
        module = GenerationIntegrationModule("example-model")

    assert module.base_url == "https://llm.example.com/v1"
    assert module.model_name == "example-model"
    openai.assert_called_once_with(
        api_key="test-key",
        base_url="https://llm.example.com/v1",
    )
