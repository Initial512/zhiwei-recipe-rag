from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import _event_stream, app, limiter
from main import GraphHybridRetrieval
from rag_modules.milvus_index_construction import MilvusIndexConstructionModule


def _document(parent_id: str):
    return SimpleNamespace(metadata={"parent_id": parent_id})


def _retrieval(results):
    retrieval = GraphHybridRetrieval.__new__(GraphHybridRetrieval)
    retrieval.data_module = SimpleNamespace(documents=[_document("one"), _document("two")])
    retrieval.milvus = SimpleNamespace(similarity_search=lambda query, k: results)
    return retrieval


def test_milvus_search_uses_supported_similarity_search_contract():
    retrieval = _retrieval([{"metadata": {"parent_id": "two"}}])

    assert retrieval._milvus_search("汤", top_k=3)[0].metadata["parent_id"] == "two"


def test_milvus_search_returns_empty_for_empty_results():
    assert _retrieval([])._milvus_search("汤", top_k=3) == []


def test_event_stream_does_not_expose_internal_exception_details():
    def broken_chunks():
        raise RuntimeError("internal SDK path should not reach clients")
        yield "unreachable"

    payload = "".join(_event_stream([], broken_chunks()))

    assert "生成回答失败" in payload
    assert "trace_id" in payload
    assert "internal SDK path" not in payload


def test_streaming_endpoint_is_rate_limited_per_client():
    limiter.reset()
    app.state.rag = SimpleNamespace(
        generation_module=SimpleNamespace(generate_assistant_answer_stream=lambda question: iter(["ok"]))
    )
    client = TestClient(app)

    responses = [client.post("/api/assistant/stream", json={"question": "你好"}) for _ in range(11)]

    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    limiter.reset()


def test_milvus_load_polling_accepts_loaded_state():
    module = object.__new__(MilvusIndexConstructionModule)
    module.collection_name = "recipes"
    module.client = SimpleNamespace(get_load_state=lambda collection_name: {"state": "Loaded"})

    module._wait_until_loaded(timeout_seconds=0.01, poll_interval=0)
