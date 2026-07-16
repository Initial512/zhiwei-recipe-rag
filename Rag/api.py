"""FastAPI surface for the recipe RAG system."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from itertools import chain
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from main import RecipeRAGSystem
from rag_modules.recipe_metadata import (
    canonical_retrieval_query,
    fuzzy_name_matches,
    parse_query,
    rank_recommendations,
)

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPE_IMAGE_DIR = PROJECT_ROOT / "data" / "图片"
mimetypes.add_type("image/webp", ".webp")
EXCLUDED_DISH_NAMES = {"示例菜"}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


RECIPE_QUERY_MARKERS = (
    "怎么做", "做法", "食材", "原料", "制作步骤", "菜谱", "推荐",
    "吃什么", "想吃", "几道菜", "哪些菜", "菜有哪些", "配菜",
    "早餐", "午餐", "晚餐", "夜宵", "汤品", "甜品", "主食",
    "荤菜", "素菜", "水产", "饮品", "调料",
)


def _local_query_type(system: RecipeRAGSystem, question: str) -> str | None:
    if not question.strip():
        return None
    parsed = _parse_user_query(system, question)
    if parsed["intent"] in {"recipe_lookup", "recommendation"}:
        return "recipe"
    return None


def _classify_query(system: RecipeRAGSystem, question: str) -> str:
    local_type = _local_query_type(system, question)
    if local_type:
        return local_type
    return "assistant"


def _image_url(dish_name: str) -> str | None:
    image_path = RECIPE_IMAGE_DIR / f"{dish_name}.webp"
    if not image_path.is_file():
        return None
    return f"/recipe-images/{quote(f'{dish_name}.webp')}"


def _is_visible_recipe(dish_name: str | None) -> bool:
    return bool(dish_name and dish_name not in EXCLUDED_DISH_NAMES)


def _visible_categories(system: RecipeRAGSystem) -> list[str]:
    return list(system.data_module.get_supported_categories())


def _source_from_doc(doc) -> dict[str, str | None]:
    dish_name = doc.metadata.get("dish_name", "未知菜品")
    return {
        "dish_name": dish_name,
        "category": doc.metadata.get("category", "其他"),
        "difficulty": doc.metadata.get("difficulty", "未知"),
        "image_url": _image_url(dish_name),
    }


def _unique_sources(docs) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    sources = []
    for doc in docs:
        source = _source_from_doc(doc)
        if not _is_visible_recipe(source["dish_name"]):
            continue
        key = (source["dish_name"], source["category"])
        if key not in seen:
            seen.add(key)
            sources.append(source)
    return sources


def _all_unique_recipes(system: RecipeRAGSystem) -> list[dict[str, str]]:
    supported = set(_visible_categories(system))
    recipes_by_key = {}
    for doc in system.data_module.documents:
        category = doc.metadata.get("category")
        dish_name = doc.metadata.get("dish_name")
        if category not in supported or not _is_visible_recipe(dish_name):
            continue
        key = (category, dish_name)
        recipes_by_key.setdefault(
            key,
            {
                "dish_name": dish_name,
                "category": category,
                "difficulty": doc.metadata.get("difficulty", "未知"),
                "image_url": _image_url(dish_name),
            },
        )
    return list(recipes_by_key.values())


def _recipe_documents(system: RecipeRAGSystem) -> list:
    supported = set(_visible_categories(system))
    documents_by_key = {}
    for doc in system.data_module.documents:
        category = doc.metadata.get("category")
        dish_name = doc.metadata.get("dish_name")
        if category in supported and _is_visible_recipe(dish_name):
            documents_by_key.setdefault((category, dish_name), doc)
    return list(documents_by_key.values())


def _parse_user_query(system: RecipeRAGSystem, question: str) -> dict:
    names = [doc.metadata.get("dish_name", "") for doc in _recipe_documents(system)]
    return parse_query(question, names)


def _vector_candidates(system: RecipeRAGSystem, parsed: dict, top_k: int) -> list:
    query = canonical_retrieval_query(parsed) or parsed.get("dish_name") or ""
    if not query:
        return []
    try:
        return system.retrieval_module.hybrid_search(query, top_k=top_k)
    except Exception:
        logger.exception("结构化查询的辅助向量检索失败")
        return []


def _lookup_recipe_documents(system: RecipeRAGSystem, parsed: dict, limit: int) -> tuple[list, bool]:
    dish_name = parsed.get("dish_name") or ""
    exact = _find_recipe_doc(system, dish_name)
    if exact:
        return [exact], True

    documents = _recipe_documents(system)
    fuzzy = fuzzy_name_matches(dish_name, documents)
    if fuzzy:
        return fuzzy[:limit], False

    chunks = _vector_candidates(system, parsed, max(limit * 2, 12))
    return system.data_module.get_parent_documents(chunks)[:limit], False


def _recommendation_documents(system: RecipeRAGSystem, parsed: dict, limit: int) -> list:
    vector_docs = _vector_candidates(system, parsed, max(limit * 3, 24))
    return rank_recommendations(
        _recipe_documents(system),
        parsed,
        vector_documents=vector_docs,
    )[:limit]


def _clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _section_lines(content: str, title: str) -> list[str]:
    lines = content.splitlines()
    start = next(
        (index + 1 for index, line in enumerate(lines) if re.match(rf"^##\s+{re.escape(title)}\s*$", line.strip())),
        None,
    )
    if start is None:
        return []
    end = next(
        (index for index in range(start, len(lines)) if re.match(r"^##\s+", lines[index].strip())),
        len(lines),
    )
    return lines[start:end]


def _split_amount(value: str) -> tuple[str, str]:
    value = _clean_markdown(value)
    if "=" in value:
        name, amount = value.split("=", 1)
        return name.strip(), amount.strip()
    match = re.match(
        r"^(.*?)[：:\s]+((?:约|大约|适量|少许)?\s*\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:克|g|千克|kg|毫升|ml|升|L|个|只|根|片|勺|汤匙|茶匙|碗|杯|份|斤|两|颗|瓣|包|块|枚|滴|撮|把|张|条|罐|盒|瓶)?|适量|少许)$",
        value,
        re.IGNORECASE,
    )
    if match and match.group(1).strip():
        return match.group(1).strip(), match.group(2).strip()
    return value, ""


def _parse_ingredient_groups(content: str) -> list[dict]:
    lines = _section_lines(content, "计算")
    if not lines:
        lines = _section_lines(content, "必备原料和工具")
    groups: list[dict] = []
    current = {"name": "所需食材", "items": []}

    for index, raw in enumerate(lines):
        line = raw.strip()
        heading = re.match(r"^###\s+(.+)$", line)
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", raw)
        nested = re.match(r"^\s{2,}[-*+]\s+(.+)$", raw)
        if heading:
            if current["items"]:
                groups.append(current)
            current = {"name": _clean_markdown(heading.group(1)), "items": []}
        elif bullet:
            value = bullet.group(1)
            if nested:
                value = nested.group(1)
            next_line = next((candidate for candidate in lines[index + 1:] if candidate.strip()), "")
            is_group_label = (
                not nested
                and "=" not in value
                and bool(re.match(r"^\s{2,}[-*+]\s+", next_line))
            )
            if is_group_label:
                if current["items"]:
                    groups.append(current)
                current = {"name": _clean_markdown(value), "items": []}
                continue
            name, amount = _split_amount(value)
            if name and not name.startswith(("图：", "注：")):
                current["items"].append({"name": name, "amount": amount})

    if current["items"]:
        groups.append(current)
    return groups


def _parse_step_groups(content: str) -> list[dict]:
    lines = _section_lines(content, "操作")
    groups: list[dict] = []
    current = {"name": "制作步骤", "steps": []}
    paragraph_buffer: list[str] = []

    def flush_paragraph():
        if paragraph_buffer:
            text = _clean_markdown(" ".join(paragraph_buffer))
            if text:
                current["steps"].append(text)
            paragraph_buffer.clear()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("!["):
            flush_paragraph()
            continue
        heading = re.match(r"^###\s+(.+)$", line)
        step = re.match(r"^(?:[-*+]|\d+[.)、])\s+(.+)$", line)
        if heading:
            flush_paragraph()
            if current["steps"]:
                groups.append(current)
            current = {"name": _clean_markdown(heading.group(1)), "steps": []}
        elif step:
            flush_paragraph()
            text = _clean_markdown(step.group(1))
            if text:
                current["steps"].append(text)
        elif not line.startswith("#"):
            paragraph_buffer.append(line)
    flush_paragraph()
    if current["steps"]:
        groups.append(current)
    return groups


def _extract_description(content: str) -> str:
    lines = content.splitlines()
    description = []
    started = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("# "):
            started = True
            continue
        if not started:
            continue
        if line.startswith("## ") or line.startswith("预估烹饪难度"):
            break
        if line and not line.startswith("!["):
            description.append(line)
    return _clean_markdown(" ".join(description))


def _parse_recipe_doc(doc) -> dict:
    content = doc.page_content
    source = _source_from_doc(doc)
    tips = [
        _clean_markdown(line)
        for line in _section_lines(content, "附加内容")
        if line.strip() and not line.strip().startswith(("#", "!["))
    ]
    plain = _clean_markdown(content)
    time_match = re.search(r"(?:烹饪|制作|耗时|用时|需时)[^\d]{0,8}(\d+(?:\s*[-~至]\s*\d+)?)\s*(分钟|小时)", plain)
    serving_match = re.search(r"(\d+(?:\s*[-~至]\s*\d+)?)\s*(?:人份|人|份量)", plain)
    result = {
        **source,
        "description": _extract_description(content),
        "ingredient_groups": _parse_ingredient_groups(content),
        "step_groups": _parse_step_groups(content),
        "tips": tips,
    }
    if doc.metadata.get("cook_time"):
        result["cook_time"] = str(doc.metadata["cook_time"])
    if doc.metadata.get("servings"):
        result["servings"] = str(doc.metadata["servings"])
    if time_match:
        result["cook_time"] = f"{time_match.group(1)}{time_match.group(2)}"
    if serving_match:
        result["servings"] = f"{serving_match.group(1)}人份"
    return result


def _find_recipe_doc(system: RecipeRAGSystem, dish_name: str):
    target = dish_name.strip().casefold()
    if dish_name.strip() in EXCLUDED_DISH_NAMES:
        return None
    supported = set(_visible_categories(system))
    return next(
        (
            doc
            for doc in system.data_module.documents
            if doc.metadata.get("category") in supported
            and str(doc.metadata.get("dish_name", "")).casefold() == target
        ),
        None,
    )


def _recipe_summary(doc) -> dict[str, str]:
    parsed = _parse_recipe_doc(doc)
    return {
        "dish_name": parsed["dish_name"],
        "category": parsed["category"],
        "difficulty": parsed["difficulty"],
        "description": parsed["description"],
        "image_url": parsed["image_url"],
    }


def _prepare_answer(system: RecipeRAGSystem, question: str):
    parsed = _parse_user_query(system, question)
    if parsed["intent"] == "chat":
        return [], system.generation_module.generate_assistant_answer_stream(question)

    if parsed["intent"] == "recipe_lookup":
        docs, _ = _lookup_recipe_documents(system, parsed, limit=3)
        if not docs:
            prefix = iter(["数据库中暂时没有找到完全匹配的菜品。\n\n大模型补充建议："])
            return [], chain(
                prefix,
                system.generation_module.generate_assistant_answer_stream(question),
            )
        return docs, system.generation_module.generate_step_by_step_answer_stream(
            question, docs
        )

    # 开放式需求先从 RAG 召回，再让大模型基于召回内容回答。
    # 不将召回文档作为 SSE sources 返回，前端因此不会展示菜品卡片。
    docs = _recommendation_documents(system, parsed, limit=max(system.config.top_k, 8))
    if not docs:
        return [], system.generation_module.generate_assistant_answer_stream(question)
    return [], system.generation_module.generate_basic_answer_stream(question, docs)


def _prepare_ingredients(system: RecipeRAGSystem, dish_name: str):
    chunks = system.retrieval_module.hybrid_search(dish_name, top_k=3)
    docs = system.data_module.get_parent_documents(chunks)
    if not docs:
        return [], iter(["抱歉，没有找到这道菜的食材信息。"])
    question = f"{dish_name}需要什么食材？"
    return docs, system.generation_module.generate_basic_answer_stream(question, docs)


def _sse(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    data_lines = "\n".join(f"data: {line}" for line in payload.split("\n"))
    return f"event: {event}\n{data_lines}\n\n"


def _event_stream(docs, chunks: Iterator[str]):
    try:
        yield _sse("sources", _unique_sources(docs))
        for chunk in chunks:
            if not chunk:
                continue
            yield _sse("delta", chunk)
        yield _sse("done", {"ok": True})
    except Exception:
        trace_id = uuid.uuid4().hex[:12]
        logger.exception("流式回答失败 trace_id=%s", trace_id)
        yield _sse("error", {"message": "生成回答失败", "trace_id": trace_id})


@asynccontextmanager
async def lifespan(app: FastAPI):
    system = RecipeRAGSystem()
    system.initialize_system()
    system.build_knowledge_base()
    app.state.rag = system
    yield


app = FastAPI(title="知味 AI Recipe API", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/recipe-images", StaticFiles(directory=RECIPE_IMAGE_DIR), name="recipe-images")
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health(request: Request):
    system = request.app.state.rag
    return {
        "status": "ok",
        "ready": bool(system.retrieval_module and system.generation_module),
    }


@app.get("/api/categories")
def categories(request: Request):
    system = request.app.state.rag
    labels = _visible_categories(system)
    dishes_by_category = {label: set() for label in labels}
    for doc in system.data_module.documents:
        category = doc.metadata.get("category")
        dish_name = doc.metadata.get("dish_name")
        if category in dishes_by_category and _is_visible_recipe(dish_name):
            dishes_by_category[category].add(dish_name)
    return [
        {"name": label, "count": len(dishes_by_category[label])}
        for label in labels
    ]


@app.post("/api/query/classify")
def classify_query(payload: ChatRequest, request: Request):
    parsed = _parse_user_query(request.app.state.rag, payload.question.strip())
    return {
        "type": "assistant" if parsed["intent"] == "chat" else "recipe",
        **parsed,
    }


@app.get("/api/recipes")
def recipes(
    request: Request,
    category: str = Query(..., min_length=1),
    query: str = Query("", max_length=100),
):
    system = request.app.state.rag
    supported = set(_visible_categories(system))
    if category not in supported:
        raise HTTPException(status_code=400, detail="不支持的菜谱分类")
    keyword = query.strip().casefold()
    rows_by_name = {}
    for doc in system.data_module.documents:
        if doc.metadata.get("category") != category:
            continue
        dish_name = doc.metadata.get("dish_name", "未知菜品")
        if not _is_visible_recipe(dish_name):
            continue
        if keyword and keyword not in dish_name.casefold():
            continue
        rows_by_name.setdefault(
            dish_name,
            {
                "dish_name": dish_name,
                "category": category,
                "difficulty": doc.metadata.get("difficulty", "未知"),
                "image_url": _image_url(dish_name),
            },
        )
    return sorted(rows_by_name.values(), key=lambda item: item["dish_name"])


@app.get("/api/search")
def search_recipes(
    request: Request,
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(12, ge=1, le=24),
):
    system = request.app.state.rag
    value = query.strip()
    parsed = _parse_user_query(system, value)

    if parsed["intent"] == "recipe_lookup":
        docs, exact_match = _lookup_recipe_documents(system, parsed, limit)
        return {
            "query": value,
            "intent": parsed["intent"],
            "parsed_query": parsed,
            "exact_match": exact_match,
            "results": [_recipe_summary(doc) for doc in docs],
        }

    if parsed["intent"] == "recommendation":
        matched_docs = _recommendation_documents(system, parsed, limit)
    else:
        matched_docs = []

    return {
        "query": value,
        "intent": parsed["intent"],
        "parsed_query": parsed,
        "exact_match": False,
        "results": [_recipe_summary(doc) for doc in matched_docs],
        "local_message": (
            None if matched_docs
            else "数据库中暂时没有找到完全匹配的菜品。"
        ),
    }


@app.get("/api/search/recipes")
def search_recipe_names(
    request: Request,
    query: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(12, ge=1, le=24),
):
    """Return only local recipe-name keyword matches; never invoke the LLM."""
    value = query.strip()
    if not value:
        raise HTTPException(status_code=422, detail="查询内容不能为空")
    keyword = value.casefold()
    matches = []
    for doc in _recipe_documents(request.app.state.rag):
        dish_name = str(doc.metadata.get("dish_name", "")).casefold()
        if dish_name and (keyword in dish_name or dish_name in keyword):
            matches.append(_recipe_summary(doc))
    return {
        "query": value,
        "results": sorted(matches, key=lambda item: item["dish_name"])[:limit],
    }


@app.get("/api/recipes/{dish_name}")
def recipe_detail(dish_name: str, request: Request):
    system = request.app.state.rag
    decoded_name = unquote(dish_name)
    doc = _find_recipe_doc(system, decoded_name)
    if not doc:
        raise HTTPException(status_code=404, detail="未找到这道菜谱")
    return _parse_recipe_doc(doc)


@app.get("/api/recommendations")
def recommendations(
    request: Request,
    limit: int = Query(6, ge=1, le=24),
):
    recipes = _all_unique_recipes(request.app.state.rag)
    return random.sample(recipes, k=min(limit, len(recipes)))


@app.post("/api/chat/stream")
@limiter.limit("10/minute")
def chat_stream(payload: ChatRequest, request: Request):
    docs, chunks = _prepare_answer(request.app.state.rag, payload.question.strip())
    return StreamingResponse(
        _event_stream(docs, chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/assistant/stream")
@limiter.limit("10/minute")
def assistant_stream(payload: ChatRequest, request: Request):
    chunks = request.app.state.rag.generation_module.generate_assistant_answer_stream(
        payload.question.strip()
    )
    return StreamingResponse(
        _event_stream([], chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/recipes/{dish_name}/ingredients/stream")
@limiter.limit("10/minute")
def ingredients_stream(dish_name: str, request: Request):
    docs, chunks = _prepare_ingredients(request.app.state.rag, unquote(dish_name))
    return StreamingResponse(
        _event_stream(docs, chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
