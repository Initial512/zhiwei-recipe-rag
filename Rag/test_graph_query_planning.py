from types import SimpleNamespace

from langchain_core.documents import Document
from rag_modules.graph_rag_retrieval import GraphQuery, GraphRAGRetrieval, QueryType
from rag_modules.intelligent_query_router import (
    IntelligentQueryRouter,
    QueryAnalysis,
    SearchStrategy,
)


def _llm_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _graph_retrieval() -> GraphRAGRetrieval:
    return GraphRAGRetrieval.__new__(GraphRAGRetrieval)


def test_adaptive_plans_keep_llm_extracted_entities_and_add_two_complex_plans(monkeypatch):
    retrieval = _graph_retrieval()
    base_query = GraphQuery(
        query_type=QueryType.MULTI_HOP,
        source_entities=["鸡肉"],
        target_entities=["蔬菜"],
        relation_types=["REQUIRES"],
    )
    monkeypatch.setattr(retrieval, "_analyze_query_complexity", lambda _: 0.9)

    plans = retrieval.adaptive_query_planning("鸡肉搭配什么蔬菜", base_query)

    assert [plan.query_type for plan in plans] == [QueryType.SUBGRAPH, QueryType.MULTI_HOP]
    assert all(plan.source_entities == ["鸡肉"] for plan in plans)
    assert all(plan.target_entities == ["蔬菜"] for plan in plans)


def test_graph_rag_empty_result_falls_back_to_hybrid_retrieval(monkeypatch):
    fallback_document = Document(page_content="fallback", metadata={"node_id": "recipe-1"})
    traditional = SimpleNamespace(hybrid_search=lambda *_: [fallback_document])
    graph = SimpleNamespace(graph_rag_search=lambda *_: [])
    router = IntelligentQueryRouter(traditional, graph, llm_client=None, config=None)
    analysis = QueryAnalysis(
        query_complexity=0.8,
        relationship_intensity=0.8,
        reasoning_required=True,
        entity_count=2,
        recommended_strategy=SearchStrategy.GRAPH_RAG,
        confidence=0.9,
        reasoning="graph query",
    )
    monkeypatch.setattr(router, "analyze_query", lambda _: analysis)

    documents, returned_analysis = router.route_query("鸡肉和蔬菜", top_k=1)

    assert documents == [fallback_document]
    assert returned_analysis is analysis
    assert documents[0].metadata["route_strategy"] == SearchStrategy.GRAPH_RAG.value


def test_rule_based_router_uses_graph_rag_for_explicit_relationship_queries():
    router = IntelligentQueryRouter(None, None, llm_client=None, config=None)

    analysis = router._rule_based_analysis("鸡肉通过哪些菜谱与哪些蔬菜相连？请说明多跳关系。")

    assert analysis.recommended_strategy == SearchStrategy.GRAPH_RAG
    assert analysis.reasoning_required is True


def test_explicit_relationship_query_overrides_an_incorrect_llm_hybrid_strategy():
    router = IntelligentQueryRouter(None, None, llm_client=None, config=None)
    llm_analysis = QueryAnalysis(
        query_complexity=0.2,
        relationship_intensity=0.1,
        reasoning_required=False,
        entity_count=2,
        recommended_strategy=SearchStrategy.HYBRID_TRADITIONAL,
        confidence=0.9,
        reasoning="incorrect strategy",
    )

    analysis = router._ensure_graph_strategy_for_explicit_relation(
        "鸡肉通过哪些菜谱与哪些蔬菜相连？请说明多跳关系。", llm_analysis
    )

    assert analysis.recommended_strategy == SearchStrategy.GRAPH_RAG
    assert analysis.reasoning_required is True


def test_graph_search_executes_each_plan_and_keeps_best_duplicate(monkeypatch):
    retrieval = _graph_retrieval()
    retrieval.driver = object()
    base_query = GraphQuery(query_type=QueryType.MULTI_HOP, source_entities=["鸡肉"])
    first = Document(page_content="first", metadata={"node_id": "recipe-1", "relevance_score": 0.5})
    second = Document(
        page_content="second", metadata={"node_id": "recipe-1", "relevance_score": 0.9}
    )
    monkeypatch.setattr(retrieval, "understand_graph_query", lambda _: base_query)
    monkeypatch.setattr(
        retrieval,
        "adaptive_query_planning",
        lambda *_: [
            GraphQuery(query_type=QueryType.SUBGRAPH, source_entities=["鸡肉"], max_depth=3),
            GraphQuery(query_type=QueryType.MULTI_HOP, source_entities=["鸡肉"], max_depth=3),
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "_execute_graph_query",
        lambda plan, _: [first if plan.query_type == QueryType.SUBGRAPH else second],
    )

    results = retrieval.graph_rag_search("鸡肉搭配什么蔬菜", top_k=3)

    assert results == [second]
    assert results[0].metadata["graph_plan_type"] == QueryType.MULTI_HOP.value
    assert results[0].metadata["graph_plan_index"] == 1


def test_entity_relation_query_uses_target_names_not_target_labels():
    retrieval = _graph_retrieval()
    captured = {}

    class Session:
        def run(self, query, parameters):
            captured["query"] = query
            captured["parameters"] = parameters
            return []

    retrieval._find_entity_relations(
        GraphQuery(
            query_type=QueryType.ENTITY_RELATION,
            source_entities=["鸡肉"],
            target_entities=["蔬菜"],
        ),
        Session(),
    )

    assert "target_entities" in captured["parameters"]
    assert "target_labels" not in captured["parameters"]


def test_multi_hop_query_limits_sources_and_paths_and_uses_timeout():
    captured = {}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def run(self, query, parameters):
            captured["query"] = query
            captured["parameters"] = parameters
            return []

    retrieval = _graph_retrieval()
    retrieval.driver = SimpleNamespace(session=Session)
    retrieval.config = SimpleNamespace(neo4j_query_timeout_seconds=5)

    assert retrieval.multi_hop_traversal(
        GraphQuery(
            query_type=QueryType.MULTI_HOP,
            source_entities=["鸡肉"] * 8,
            max_depth=9,
            max_nodes=80,
        )
    ) == []

    assert captured["parameters"]["source_limit"] == 5
    assert captured["parameters"]["path_limit"] == 20
    assert "COUNT" not in captured["query"].text
    assert "[*1..3]" in captured["query"].text
    assert captured["query"].timeout == 5


def test_graph_paths_keep_the_neo4j_relationship_type():
    retrieval = _graph_retrieval()

    class Relationship(dict):
        type = "REQUIRES"

    record = {
        "path_nodes": [],
        "rels": [Relationship(amount="200g")],
        "path_len": 1,
        "relevance": 1.0,
    }

    assert retrieval._parse_neo4j_path(record).relationships == [
        {"type": "REQUIRES", "properties": {"amount": "200g"}}
    ]


def test_graph_intent_accepts_markdown_json_from_model():
    retrieval = _graph_retrieval()
    retrieval.config = SimpleNamespace(llm_model="test")
    retrieval.llm_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: _llm_response(
                    '```json\n{"query_type":"multi_hop","source_entities":["鸡肉"],'
                    '"target_entities":["蔬菜"],"max_depth":9}\n```'
                )
            )
        )
    )

    graph_query = retrieval.understand_graph_query("鸡肉和蔬菜有什么关系？")

    assert graph_query.query_type == QueryType.MULTI_HOP
    assert graph_query.source_entities == ["鸡肉"]
    assert graph_query.target_entities == ["蔬菜"]
    assert graph_query.max_depth == 3


def test_graph_intent_invalid_model_output_uses_matching_cached_entities():
    retrieval = _graph_retrieval()
    retrieval.config = SimpleNamespace(llm_model="test")
    retrieval.entity_cache = {
        "ingredient-chicken": {
            "name": "鸡肉",
            "category": "肉类",
            "degree": 12,
        },
        "ingredient-spinach": {
            "name": "菠菜",
            "category": "蔬菜",
            "degree": 8,
        },
    }
    retrieval.llm_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: _llm_response("模型分析：多跳关系"))
        )
    )

    graph_query = retrieval.understand_graph_query("鸡肉通过哪些菜谱与哪些蔬菜相连？")

    assert graph_query.query_type == QueryType.MULTI_HOP
    assert graph_query.source_entities == ["鸡肉"]
    assert graph_query.target_entities == ["蔬菜"]
    assert graph_query.max_depth == 3


def test_graph_intent_queries_database_when_bounded_cache_misses_an_entity():
    retrieval = _graph_retrieval()
    retrieval.config = SimpleNamespace(llm_model="test")
    retrieval.entity_cache = {}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def run(self, _query, _parameters):
            return [{"name": "洋葱", "category": "蔬菜", "degree": 8}]

    class Driver:
        @staticmethod
        def session():
            return Session()

    retrieval.driver = Driver()
    retrieval.llm_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: _llm_response("not-json"))
        )
    )

    graph_query = retrieval.understand_graph_query("洋葱通过哪些菜谱与哪些蔬菜相连？")

    assert graph_query.query_type == QueryType.MULTI_HOP
    assert graph_query.source_entities == ["洋葱"]
    assert graph_query.target_entities == ["蔬菜"]


def test_graph_index_fallback_prefers_full_entity_name_and_multi_hop_plan():
    retrieval = _graph_retrieval()
    retrieval.entity_cache = {
        "short": {"name": "葱", "category": "蔬菜", "degree": 20},
        "full": {"name": "洋葱", "category": "蔬菜", "degree": 8},
    }

    fallback_query = retrieval._graph_query_from_index("洋葱通过哪些菜谱与哪些蔬菜相连？")
    plans = retrieval.adaptive_query_planning("洋葱通过哪些菜谱与哪些蔬菜相连？", fallback_query)

    assert fallback_query.source_entities == ["洋葱"]
    assert plans[0].query_type == QueryType.MULTI_HOP


def test_router_analysis_accepts_embedded_json_from_model():
    router = IntelligentQueryRouter(
        traditional_retrieval=None,
        graph_rag_retrieval=None,
        llm_client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: _llm_response(
                        '分析结果： {"query_complexity":0.2,"relationship_intensity":0.1,'
                        '"reasoning_required":false,"entity_count":1,'
                        '"recommended_strategy":"hybrid_traditional","confidence":0.8}'
                    )
                )
            )
        ),
        config=SimpleNamespace(llm_model="test"),
    )

    analysis = router.analyze_query("今晚吃什么？")

    assert analysis.recommended_strategy == SearchStrategy.HYBRID_TRADITIONAL
    assert analysis.confidence == 0.8
