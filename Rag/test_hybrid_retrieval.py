from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document

from rag_modules.hybrid_retrieval import HybridRetrievalModule


def _module() -> HybridRetrievalModule:
    return HybridRetrievalModule.__new__(HybridRetrievalModule)


def test_chinese_tokenizer_keeps_single_character_ingredients():
    tokens = HybridRetrievalModule._tokenize_chinese("葱姜怎么处理")

    assert "葱" in tokens
    assert "姜" in tokens


def test_bm25_search_ranks_matching_recipe_and_preserves_metadata():
    module = _module()
    module.config = SimpleNamespace(
        neo4j_uri="bolt://example", neo4j_user="user", neo4j_password="password"
    )
    module._build_graph_index = lambda: None
    documents = [
        Document(
            page_content="葱油拌面需要香葱和面条",
            metadata={"node_id": "1", "recipe_name": "葱油拌面"},
        ),
        Document(
            page_content="番茄炒蛋需要番茄和鸡蛋",
            metadata={"node_id": "2", "recipe_name": "番茄炒蛋"},
        ),
        Document(
            page_content="红烧肉需要五花肉和酱油",
            metadata={"node_id": "3", "recipe_name": "红烧肉"},
        ),
    ]
    with patch("rag_modules.hybrid_retrieval.GraphDatabase.driver"):
        module.initialize(documents)

    results = module.bm25_search("香葱拌面", top_k=2)

    assert results[0].metadata["recipe_name"] == "葱油拌面"
    assert results[0].metadata["search_method"] == "bm25"
    assert results[0].metadata["bm25_score"] > 0


def test_rrf_merges_duplicate_recipe_and_keeps_source_metadata():
    module = _module()
    first = Document(page_content="第一段", metadata={"node_id": "recipe-1"})
    duplicate = Document(page_content="第二段", metadata={"node_id": "recipe-1"})
    second = Document(page_content="第三段", metadata={"node_id": "recipe-2"})

    results = module._rrf_merge([("dual", [first, second]), ("vector", [duplicate])], top_k=2)

    assert [doc.metadata["node_id"] for doc in results] == ["recipe-1", "recipe-2"]
    assert results[0].metadata["rrf_sources"] == ["dual", "vector"]
    assert results[0].metadata["final_score"] == results[0].metadata["rrf_score"]


def test_hybrid_search_keeps_available_results_when_a_retriever_fails():
    module = _module()
    document = Document(page_content="可用的 BM25 结果", metadata={"node_id": "recipe-1"})
    module.dual_level_retrieval = lambda *_: (_ for _ in ()).throw(
        RuntimeError("neo4j unavailable")
    )
    module.vector_search_enhanced = lambda *_: []
    module.bm25_search = lambda *_: [document]

    results = module.hybrid_search("葱油面", top_k=1)

    assert results[0].metadata["node_id"] == "recipe-1"
    assert results[0].metadata["rrf_sources"] == ["bm25"]
