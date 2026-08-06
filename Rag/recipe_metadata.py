"""API-facing recipe query helpers, independent from the RAG implementation."""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any


def parse_query(query: str, dish_names: Iterable[str]) -> dict[str, Any]:
    value = query.strip()
    names = [name for name in dish_names if name]
    dish_name = next((name for name in names if name in value), "")
    recommendation = any(word in value for word in ("推荐", "吃什么", "哪些菜", "几道菜", "来点"))
    lookup = bool(dish_name) or any(word in value for word in ("怎么做", "做法", "食材", "步骤"))
    return {
        "intent": "recommendation"
        if recommendation and not dish_name
        else "recipe_lookup"
        if lookup
        else "chat",
        "dish_name": dish_name,
        "query": value,
    }


def canonical_retrieval_query(parsed: dict[str, Any]) -> str:
    return str(parsed.get("dish_name") or parsed.get("query") or "")


def fuzzy_name_matches(query: str, documents: Iterable[Any]) -> list[Any]:
    needle = query.strip()
    if not needle:
        return []
    scored = [
        (SequenceMatcher(None, needle, str(doc.metadata.get("dish_name", ""))).ratio(), doc)
        for doc in documents
    ]
    return [
        doc
        for score, doc in sorted(scored, key=lambda item: item[0], reverse=True)
        if score >= 0.45
    ]


def rank_recommendations(
    documents: Iterable[Any], _parsed: dict[str, Any], vector_documents=None
) -> list[Any]:
    vector_documents = vector_documents or []
    seen: set[str] = set()
    result = []
    for doc in [*vector_documents, *documents]:
        key = str(
            doc.metadata.get("parent_id")
            or doc.metadata.get("node_id")
            or doc.metadata.get("dish_name")
        )
        if key and key not in seen:
            seen.add(key)
            result.append(doc)
    return result
