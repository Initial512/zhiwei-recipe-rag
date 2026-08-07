"""FastAPI surface for the recipe RAG system."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import random
import re
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from itertools import chain
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from main import RecipeRAGSystem
from pydantic import BaseModel, Field
from recipe_metadata import (
    canonical_retrieval_query,
    fuzzy_name_matches,
    parse_query,
    rank_recommendations,
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPE_IMAGE_DIR = PROJECT_ROOT / "data" / "图片"
mimetypes.add_type("image/webp", ".webp")
DISHES_DIR = PROJECT_ROOT / "data" / "dishes"
EXCLUDED_DISH_NAMES = {"示例菜"}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


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
    for extension in (".webp", ".png"):
        image_path = RECIPE_IMAGE_DIR / f"{dish_name}{extension}"
        if image_path.is_file():
            return f"/recipe-images/{quote(f'{dish_name}{extension}')}"
    return None


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
    catalog = getattr(system, "catalog", None)
    if catalog is not None:
        return list(catalog.recipes)
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
    catalog = getattr(system, "catalog", None)
    if catalog is not None:
        return list(catalog.documents)
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


def _lookup_recipe_documents(
    system: RecipeRAGSystem, parsed: dict, limit: int
) -> tuple[list, bool]:
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
    title_aliases = {"食材": ("食材", "所需食材"), "操作": ("操作", "制作步骤")}
    titles = title_aliases.get(title, (title,))
    lines = content.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if any(
                re.match(rf"^##\s+{re.escape(candidate)}\s*$", line.strip()) for candidate in titles
            )
        ),
        None,
    )
    if start is None:
        return []
    end = next(
        (index for index in range(start, len(lines)) if re.match(r"^##\s+", lines[index].strip())),
        len(lines),
    )
    return lines[start:end]


def _fallback_tips(category: str) -> list[str]:
    tips_by_category = {
        "\u6c64\u54c1": [
            "\u51fa\u9505\u524d\u5148\u8bd5\u5473\uff0c\u518d\u6839\u636e\u54b8\u6de1\u8c03\u6574\u8c03\u5473\u3002"
        ],
        "\u6c34\u4ea7": [
            "\u6d77\u9c9c\u8bf7\u5145\u5206\u52a0\u70ed\u81f3\u719f\uff0c\u8d77\u9505\u540e\u5c3d\u5feb\u98df\u7528\u3002"
        ],
        "\u8089\u83dc": [
            "\u8089\u7c7b\u52a0\u70ed\u81f3\u719f\u900f\u540e\u518d\u88c5\u76d8\uff0c\u53ef\u4ee5\u66f4\u5b89\u5fc3\u5730\u4eab\u7528\u3002"
        ],
        "\u7d20\u83dc": [
            "\u852c\u83dc\u5efa\u8bae\u5927\u706b\u5feb\u7092\uff0c\u4e34\u51fa\u9505\u518d\u8c03\u5473\u3002"
        ],
        "\u751c\u70b9": [
            "\u6210\u54c1\u51b7\u5374\u81f3\u5b9a\u578b\u540e\u518d\u98df\u7528\uff0c\u53e3\u611f\u66f4\u4f73\u3002"
        ],
    }
    return tips_by_category.get(
        category,
        [
            "\u53ef\u4ee5\u6839\u636e\u4e2a\u4eba\u53e3\u5473\u9002\u91cf\u8c03\u6574\u8c03\u5473\u3002"
        ],
    )


def _clean_tip_line(line: str) -> str:
    """Discard Markdown source credits and retain only cooking guidance."""
    raw = line.strip()
    if re.search(
        r"https?://|www\.|b23\.tv|bilibili|youtube|douyin|xiaohongshu|xiachufang|weixin",
        raw,
        re.IGNORECASE,
    ):
        return ""
    value = _clean_markdown(raw)
    source_prefix = (
        r"^(?:\u53c2\u8003(?:\u8d44\u6599|\u94fe\u63a5|\u6765\u6e90)?|\u6765\u6e90|\u4f5c\u8005|\u539f\u6587|"
        r"\u89c6\u9891(?:\u6f14\u793a)?|\u6559\u5b66\u89c6\u9891|\u505a\u6cd5\u53c2\u8003|\u76f8\u5173\u94fe\u63a5|\u51fa\u5904|\u516c\u4f17\u53f7)"
    )
    if re.match(source_prefix, value, re.IGNORECASE):
        return ""
    if any(
        marker in value.lower()
        for marker in (
            "\u6559\u7a0b",
            "\u83dc\u8c31\u6765\u6e90",
            "\u5c0f\u7ea2\u4e66",
            "\u4e0b\u53a8\u623f",
            "\u54d4\u54e9\u54d4\u54e9",
            "b\u7ad9",
        )
    ):
        return ""
    value = re.sub(
        r"\s*(?:[-—|｜]\s*)?(?:\u6765\u6e90|\u4f5c\u8005|\u539f\u6587|\u89c6\u9891|\u516c\u4f17\u53f7)\s*[:\uff1a].*$",
        "",
        value,
    )
    if re.fullmatch(r".{1,16}(?:\u7684)?(?:\u83dc\u8c31|\u98df\u8c31)", value):
        return ""
    return value.strip()


@lru_cache(maxsize=512)
def _markdown_tips(dish_name: str, category: str = "") -> list[str]:
    """Read optional tips from the original recipe Markdown file."""
    if not dish_name or not DISHES_DIR.is_dir():
        return _fallback_tips(category)

    recipe_file = next(DISHES_DIR.rglob(f"{dish_name}.md"), None)
    if recipe_file is None:
        return _fallback_tips(category)
    try:
        content = recipe_file.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Unable to read recipe Markdown for tips: %s", recipe_file)
        return _fallback_tips(category)

    tips = []
    for raw in _section_lines(content, "\u9644\u52a0\u5185\u5bb9"):
        line = raw.strip()
        if not line or line.startswith(("#", "![")):
            continue
        line = re.sub(r"^(?:[-*+]|\d+[.)\u3001])\s+", "", line)
        value = _clean_tip_line(line)
        if value:
            tips.append(value)
    return tips or _fallback_tips(category)


def _split_amount(value: str) -> tuple[str, str]:
    value = _clean_markdown(value)
    value = re.sub(r"\s+-\s+.*$", "", value).strip()
    if "=" in value:
        name, amount = value.split("=", 1)
        return name.strip(), amount.strip()
    parenthesized = re.match(r"^(.*?)[(\uff08]([^()\uff08\uff09]+)[)\uff09]$", value)
    if parenthesized and parenthesized.group(1).strip():
        return parenthesized.group(1).strip(), parenthesized.group(2).strip()
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
    lines = _section_lines(content, "食材")
    if not lines:
        lines = _section_lines(content, "必备原料和工具")
    groups: list[dict] = []
    current = {"name": "所需食材", "items": []}

    for index, raw in enumerate(lines):
        line = raw.strip()
        heading = re.match(r"^###\s+(.+)$", line)
        bullet = re.match(r"^\s*(?:[-*+]|\d+[.)\u3001])\s+(.+)$", raw)
        nested = re.match(r"^\s{2,}(?:[-*+]|\d+[.)\u3001])\s+(.+)$", raw)
        if heading:
            if current["items"]:
                groups.append(current)
            current = {"name": _clean_markdown(heading.group(1)), "items": []}
        elif bullet:
            value = bullet.group(1)
            if nested:
                value = nested.group(1)
            next_line = next(
                (candidate for candidate in lines[index + 1 :] if candidate.strip()), ""
            )
            is_group_label = (
                not nested and "=" not in value and bool(re.match(r"^\s{2,}[-*+]\s+", next_line))
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


def _clean_step_text(text: str) -> str:
    """Keep the actual action while dropping graph-node metadata from recipe steps."""
    text = _clean_markdown(text)
    description = re.search(
        r"\u63cf\u8ff0\s*[:\uff1a]\s*(.*?)(?=\s*(?:\u65b9\u6cd5|\u5de5\u5177|\u65f6\u95f4)\s*[:\uff1a]|$)",
        text,
    )
    if description:
        return description.group(1).strip()
    text = re.sub(r"^(?:\u6b65\u9aa4\s*[:\uff1a]\s*)?(?:\u6b65\u9aa4)?\d+\s*", "", text)
    return re.sub(r"\s*(?:\u65b9\u6cd5|\u5de5\u5177|\u65f6\u95f4)\s*[:\uff1a].*$", "", text).strip()


def _parse_step_groups(content: str) -> list[dict]:
    lines = _section_lines(content, "操作")
    groups: list[dict] = []
    current = {"name": "制作步骤", "steps": []}
    paragraph_buffer: list[str] = []

    def flush_paragraph():
        if paragraph_buffer:
            text = _clean_step_text(" ".join(paragraph_buffer))
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
            heading_name = _clean_markdown(heading.group(1))
            if re.fullmatch(r"\u7b2c\s*\d+\s*\u6b65(?:\s*[:\uff1a].*)?", heading_name):
                continue
            if current["steps"]:
                groups.append(current)
            current = {"name": heading_name, "steps": []}
        elif step:
            flush_paragraph()
            text = _clean_step_text(step.group(1))
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
    if not tips:
        tips = _markdown_tips(str(source["dish_name"]), str(source["category"]))
    plain = _clean_markdown(content)
    time_match = re.search(
        r"(?:烹饪|制作|耗时|用时|需时)[^\d]{0,8}(\d+(?:\s*[-~至]\s*\d+)?)\s*(分钟|小时)", plain
    )
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
    catalog = getattr(system, "catalog", None)
    if catalog is not None:
        return catalog.find(target)
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
    source = _source_from_doc(doc)
    return {
        **source,
        "description": _extract_description(doc.page_content),
    }


class RecipeCatalog:
    """Read-only API catalog built once after the knowledge base is ready."""

    def __init__(self, system: RecipeRAGSystem):
        supported = set(_visible_categories(system))
        documents_by_key = {}
        for doc in system.data_module.documents:
            category = doc.metadata.get("category")
            dish_name = doc.metadata.get("dish_name")
            if category in supported and _is_visible_recipe(dish_name):
                documents_by_key.setdefault((category, dish_name), doc)

        self.documents = tuple(documents_by_key.values())
        self.by_name = {
            str(doc.metadata.get("dish_name", "")).casefold(): doc for doc in self.documents
        }
        self.recipes = tuple(_recipe_summary(doc) for doc in self.documents)
        self.by_category = {
            category: tuple(
                sorted(
                    (recipe for recipe in self.recipes if recipe["category"] == category),
                    key=lambda recipe: recipe["dish_name"],
                )
            )
            for category in _visible_categories(system)
        }

    def find(self, dish_name: str):
        return self.by_name.get(dish_name.strip().casefold())

    def search_names(self, query: str, limit: int) -> list[dict[str, str]]:
        keyword = query.casefold()
        return [
            recipe
            for recipe in self.recipes
            if keyword in recipe["dish_name"].casefold()
            or recipe["dish_name"].casefold() in keyword
        ][:limit]


def _prepare_answer(system: RecipeRAGSystem, question: str):
    parsed = _parse_user_query(system, question)
    if parsed["intent"] == "recipe_lookup" and parsed.get("dish_name"):
        docs, _ = _lookup_recipe_documents(system, parsed, limit=3)
        if not docs:
            prefix = iter(["数据库中暂时没有找到完全匹配的菜品。\n\n大模型补充建议："])
            return [], chain(
                prefix,
                system.generation_module.generate_adaptive_answer_stream(question, []),
            )
        return docs, system.generation_module.generate_adaptive_answer_stream(question, docs)

    # 除明确菜名查询外，统一通过查询路由。这样关系推理问题可进入
    # GraphRAG 的自适应查询规划，GraphRAG 失效时仍由路由降级到混合检索。
    try:
        docs = system.retrieve(question, top_k=max(system.config.top_k, 8))
    except Exception:
        logger.exception("网页问答的查询路由失败")
        docs = []

    # 开放式问题不展示来源卡片，避免把图节点或推荐候选误呈现为精确菜谱来源。
    if not docs:
        return [], system.generation_module.generate_adaptive_answer_stream(question, [])
    return [], system.generation_module.generate_adaptive_answer_stream(question, docs)


def _prepare_ingredients(system: RecipeRAGSystem, dish_name: str):
    chunks = system.retrieve(dish_name, top_k=3)
    docs = system.data_module.get_parent_documents(chunks)
    if not docs:
        return [], iter(["抱歉，没有找到这道菜的食材信息。"])
    question = f"{dish_name}需要什么食材？"
    return docs, system.generation_module.generate_adaptive_answer_stream(question, docs)


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


def _stream_response(docs, chunks: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(docs, chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    system = RecipeRAGSystem()
    try:
        system.initialize_system()
        system.build_knowledge_base()
        system.catalog = RecipeCatalog(system)
        app.state.rag = system
        yield
    finally:
        system.close()


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
    catalog = getattr(system, "catalog", None)
    if catalog is not None:
        return [{"name": label, "count": len(catalog.by_category[label])} for label in labels]
    dishes_by_category = {label: set() for label in labels}
    for doc in system.data_module.documents:
        category = doc.metadata.get("category")
        dish_name = doc.metadata.get("dish_name")
        if category in dishes_by_category and _is_visible_recipe(dish_name):
            dishes_by_category[category].add(dish_name)
    return [{"name": label, "count": len(dishes_by_category[label])} for label in labels]


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
    catalog = getattr(system, "catalog", None)
    if catalog is not None and category in supported:
        rows = catalog.by_category[category]
        keyword = query.strip().casefold()
        return [row for row in rows if not keyword or keyword in row["dish_name"].casefold()]
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
        "local_message": (None if matched_docs else "数据库中暂时没有找到完全匹配的菜品。"),
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
    catalog = getattr(request.app.state.rag, "catalog", None)
    if catalog is not None:
        return {"query": value, "results": catalog.search_names(value, limit)}
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
    system = request.app.state.rag
    question = payload.question.strip()
    if _classify_query(system, question) == "assistant":
        return _stream_response(
            [], system.generation_module.generate_adaptive_answer_stream(question, [])
        )
    docs, chunks = _prepare_answer(system, question)
    return _stream_response(docs, chunks)


@app.post("/api/assistant/stream")
@limiter.limit("10/minute")
def assistant_stream(payload: ChatRequest, request: Request):
    chunks = request.app.state.rag.generation_module.generate_adaptive_answer_stream(
        payload.question.strip(), []
    )
    return _stream_response([], chunks)


@app.post("/api/recipes/{dish_name}/ingredients/stream")
@limiter.limit("10/minute")
def ingredients_stream(dish_name: str, request: Request):
    docs, chunks = _prepare_ingredients(request.app.state.rag, unquote(dish_name))
    return _stream_response(docs, chunks)
