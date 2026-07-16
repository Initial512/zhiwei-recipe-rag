from unittest.mock import patch

from rag_modules.generation_integration import GenerationIntegrationModule


def test_llm_configuration_comes_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    with patch(
        "rag_modules.generation_integration.ChatOpenAI"
    ) as chat_openai:
        module = GenerationIntegrationModule()

    assert module.base_url == "https://llm.example.com/v1"
    assert module.model_name == "example-model"
    chat_openai.assert_called_once_with(
        model="example-model",
        temperature=0.1,
        max_tokens=2048,
        api_key="test-key",
        base_url="https://llm.example.com/v1",
        request_timeout=30,
        max_retries=2,
    )
