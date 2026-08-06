from types import SimpleNamespace

import api as api_module
from api import RECIPE_IMAGE_DIR, _parse_ingredient_groups, _parse_step_groups, _sse
from config import RAGConfig
from main import RecipeRAGSystem, _difficulty
from recipe_metadata import parse_query

from rag_modules.session_cache_manager import SessionCacheManager
from rag_modules.milvus_index_construction import MilvusIndexConstructionModule


def test_existing_llm_configuration_contract_is_preserved(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    assert RAGConfig().llm_model == "test-model"


def test_graph_documents_keep_frontend_taxonomy():
    assert RecipeRAGSystem.normalize_category("汤类,早餐") == "汤品"
    assert RecipeRAGSystem.normalize_category("饮料") == "饮品"
    assert RecipeRAGSystem().get_supported_categories()[-1] == "半成品"
    assert _difficulty(3) == "中等"


def test_recipe_query_classification_requires_no_legacy_rag_module():
    assert parse_query("宫保鸡丁怎么做", ["宫保鸡丁"])["intent"] == "recipe_lookup"
    assert parse_query("推荐几道下饭菜", ["宫保鸡丁"])["intent"] == "recommendation"


def test_session_cache_returns_same_session_answer():
    cache = SessionCacheManager(
        embedding_model=SimpleNamespace(
            embed_documents=lambda values: [[float(len(value))] for value in values]
        )
    )
    cache.add_to_semantic_cache("番茄汤", "做法", "session-a")
    assert cache.check_semantic_cache("番茄汤", "session-a") == "做法"
    assert cache.check_semantic_cache("番茄汤", "session-b") is None


def test_index_rebuilds_only_when_chunk_count_changes():
    system = RecipeRAGSystem.__new__(RecipeRAGSystem)
    system.chunks = [object(), object()]
    calls = []
    system.index_module = SimpleNamespace(
        has_collection=lambda: True,
        get_collection_stats=lambda: {"row_count": "2"},
        load_collection=lambda: True,
        build_vector_index=lambda chunks: calls.append(chunks) or True,
    )
    system._ensure_index()
    assert calls == []

    system.index_module.get_collection_stats = lambda: {"row_count": "1"}
    system._ensure_index()
    assert calls == [system.chunks]


def test_sse_and_recipe_images_remain_compatible():
    assert _sse("delta", "一碗汤") == "event: delta\ndata: 一碗汤\n\n"
    assert (RECIPE_IMAGE_DIR / "宫保鸡丁.webp").is_file()


def test_milvus_index_accepts_frontend_difficulty_labels():
    assert MilvusIndexConstructionModule._difficulty_value("非常简单") == 1
    assert MilvusIndexConstructionModule._difficulty_value("中等") == 3
    assert MilvusIndexConstructionModule._difficulty_value("未知") == 0


def test_recommendation_questions_always_return_a_stream(monkeypatch):
    system = SimpleNamespace(
        config=SimpleNamespace(top_k=5),
        generation_module=SimpleNamespace(
            generate_adaptive_answer_stream=lambda question, docs: iter(["answer"])
        ),
    )
    monkeypatch.setattr(
        api_module, "_parse_user_query", lambda _system, _question: {"intent": "recommendation"}
    )
    monkeypatch.setattr(
        api_module, "_recommendation_documents", lambda *_args, **_kwargs: [object()]
    )

    docs, chunks = api_module._prepare_answer(system, "recommend a dish")

    assert docs == []
    assert list(chunks) == ["answer"]


def test_graph_recipe_detail_parser_keeps_ingredients_and_action_only():
    content = """## \u6240\u9700\u98df\u6750
1. \u767d\u8611\u83c7(200g) - \u6d17\u51c0
2. \u9c9c\u725b\u5976(300ml)
## \u5236\u4f5c\u6b65\u9aa4
### \u7b2c1\u6b65
\u6b65\u9aa4: \u6b65\u9aa41 \u63cf\u8ff0: \u767d\u8611\u83c7\u5207\u7247\u5907\u7528\uff0c\u6d0b\u8471\u5207\u672b\u5907\u7528\u3002 \u65b9\u6cd5: \u5207 \u5de5\u5177: \u5200,\u6848\u677f \u65f6\u95f4: 5\u5206\u949f
"""

    assert _parse_ingredient_groups(content) == [
        {"name": "\u6240\u9700\u98df\u6750", "items": [{"name": "\u767d\u8611\u83c7", "amount": "200g"}, {"name": "\u9c9c\u725b\u5976", "amount": "300ml"}]}
    ]
    assert _parse_step_groups(content) == [
        {"name": "\u5236\u4f5c\u6b65\u9aa4", "steps": ["\u767d\u8611\u83c7\u5207\u7247\u5907\u7528\uff0c\u6d0b\u8471\u5207\u672b\u5907\u7528\u3002"]}
    ]
