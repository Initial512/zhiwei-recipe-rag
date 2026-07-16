from pathlib import Path

from main import GraphHybridRetrieval, _difficulty


def test_graph_router_prefers_graph_for_short_relationship_questions():
    assert GraphHybridRetrieval._strategy("鸡蛋能搭配什么") == "graph"
    assert GraphHybridRetrieval._strategy("请推荐含有鸡蛋并且适合晚餐的菜") == "combined"


def test_graph_router_prefers_semantic_search_for_general_questions():
    assert GraphHybridRetrieval._strategy("今天想吃清淡一点") == "semantic"


def test_graph_assets_are_owned_by_this_project():
    root = Path(__file__).resolve().parents[1]
    graph_data = root / "data" / "graph" / "cypher"

    assert (graph_data / "nodes.csv").is_file()
    assert (graph_data / "relationships.csv").is_file()
    assert (graph_data / "neo4j_import.cypher").is_file()
    assert not (root / "What-to-eat-today").exists()


def test_graph_difficulty_is_rendered_for_the_existing_frontend():
    assert _difficulty(1) == "非常简单"
    assert _difficulty(3) == "中等"
    assert _difficulty(None) == "未知"
