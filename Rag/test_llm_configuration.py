from types import SimpleNamespace
from unittest.mock import Mock, patch

from main import GenerationIntegrationModule
from rag_modules.generation_integration import SYSTEM_PROMPT


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


def test_generation_uses_system_message_for_identity_and_scope_rules():
    completion = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
        )
    )
    module = GenerationIntegrationModule.__new__(GenerationIntegrationModule)
    module.model_name = "test-model"
    module.temperature = 0.1
    module.max_tokens = 100
    module.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completion))
    )

    assert module.generate_adaptive_answer("你是谁？", []) == "answer"

    messages = completion.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1]["role"] == "user"
    assert "你是谁？" in messages[1]["content"]
    assert "知味 AI 饮食推荐小助手" in SYSTEM_PROMPT
    assert "不回答医疗" in SYSTEM_PROMPT
    assert "不回答编程" in SYSTEM_PROMPT
