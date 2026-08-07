import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import api as api_module
from api import RECIPE_IMAGE_DIR, RecipeCatalog, _parse_ingredient_groups, _parse_step_groups, _sse
from config import RAGConfig
from main import RecipeRAGSystem, _difficulty
from rag_modules.milvus_index_construction import MilvusIndexConstructionModule
from recipe_metadata import parse_query


def test_config_reads_environment_when_instance_is_created(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("MILVUS_PORT", "19531")

    config = RAGConfig()

    assert config.llm_model == "test-model"
    assert config.neo4j_password == "test-password"
    assert config.milvus_port == 19531


def test_lifespan_closes_system_on_shutdown():
    closed = []
    system = SimpleNamespace(
        initialize_system=lambda: None,
        build_knowledge_base=lambda: None,
        close=lambda: closed.append(True),
    )
    app = SimpleNamespace(state=SimpleNamespace())

    async def run_lifespan():
        with (
            patch.object(api_module, "RecipeRAGSystem", return_value=system),
            patch.object(api_module, "RecipeCatalog", return_value=SimpleNamespace()),
        ):
            async with api_module.lifespan(app):
                assert app.state.rag is system

    asyncio.run(run_lifespan())
    assert closed == [True]


def test_lifespan_closes_system_when_startup_fails():
    closed = []
    system = SimpleNamespace(
        initialize_system=lambda: (_ for _ in ()).throw(RuntimeError("startup failed")),
        close=lambda: closed.append(True),
    )
    app = SimpleNamespace(state=SimpleNamespace())

    async def run_lifespan():
        with patch.object(api_module, "RecipeRAGSystem", return_value=system):
            try:
                async with api_module.lifespan(app):
                    raise AssertionError("unreachable")
            except RuntimeError:
                pass

    asyncio.run(run_lifespan())
    assert closed == [True]


def test_graph_documents_keep_frontend_taxonomy():
    assert RecipeRAGSystem.normalize_category("\u6c64\u7c7b,\u65e9\u9910") == "\u6c64\u54c1"
    assert RecipeRAGSystem.normalize_category("\u996e\u6599") == "\u996e\u54c1"
    assert RecipeRAGSystem().get_supported_categories()[-1] == "\u534a\u6210\u54c1"
    assert _difficulty(3) == "\u4e2d\u7b49"


def test_recipe_query_classification_uses_current_parser():
    names = ["\u5bab\u4fdd\u9e21\u4e01"]
    assert (
        parse_query("\u5bab\u4fdd\u9e21\u4e01\u600e\u4e48\u505a", names)["intent"]
        == "recipe_lookup"
    )
    assert (
        parse_query("\u63a8\u8350\u51e0\u9053\u4e0b\u996d\u83dc", names)["intent"]
        == "recommendation"
    )


def test_sse_and_recipe_images_remain_compatible():
    assert _sse("delta", "\u4e00\u7897\u6c64") == "event: delta\ndata: \u4e00\u7897\u6c64\n\n"
    assert (RECIPE_IMAGE_DIR / "\u5bab\u4fdd\u9e21\u4e01.webp").is_file()


def test_milvus_index_accepts_frontend_difficulty_labels():
    assert MilvusIndexConstructionModule._difficulty_value("\u975e\u5e38\u7b80\u5355") == 1
    assert MilvusIndexConstructionModule._difficulty_value("\u4e2d\u7b49") == 3
    assert MilvusIndexConstructionModule._difficulty_value("\u672a\u77e5") == 0


def test_recipe_catalog_reuses_precomputed_summaries(monkeypatch):
    document = SimpleNamespace(
        metadata={
            "dish_name": "\u6c64\u9762",
            "category": "\u4e3b\u98df",
            "difficulty": "\u7b80\u5355",
        }
    )
    system = SimpleNamespace(
        data_module=SimpleNamespace(
            documents=[document], get_supported_categories=lambda: ["\u4e3b\u98df"]
        ),
    )
    monkeypatch.setattr(
        api_module,
        "_recipe_summary",
        lambda doc: {"dish_name": "\u6c64\u9762", "category": "\u4e3b\u98df"},
    )

    catalog = RecipeCatalog(system)

    assert catalog.find("\u6c64\u9762") is document
    assert catalog.search_names("\u6c64", 12) == [
        {"dish_name": "\u6c64\u9762", "category": "\u4e3b\u98df"}
    ]


def test_graph_recipe_detail_parser_keeps_ingredients_and_action_only():
    content = """## \u6240\u9700\u98df\u6750
1. \u767d\u8611\u83c7(200g) - \u6d17\u51c0
2. \u9c9c\u725b\u5976(300ml)
## \u5236\u4f5c\u6b65\u9aa4
### \u7b2c1\u6b65
\u6b65\u9aa4: \u6b65\u9aa41 \u63cf\u8ff0: \u767d\u8611\u83c7\u5207\u7247\u5907\u7528\uff0c\u6d0b\u8471\u5207\u672b\u5907\u7528\u3002 \u65b9\u6cd5: \u5207 \u5de5\u5177: \u5200
"""

    assert _parse_ingredient_groups(content)[0]["items"][0]["amount"] == "200g"
    assert _parse_step_groups(content)[0]["steps"] == [
        "\u767d\u8611\u83c7\u5207\u7247\u5907\u7528\uff0c\u6d0b\u8471\u5207\u672b\u5907\u7528\u3002"
    ]
