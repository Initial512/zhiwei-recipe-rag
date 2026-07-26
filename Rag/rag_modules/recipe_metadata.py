"""Rule-based recipe metadata, query intent parsing, and recommendation ranking."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Iterable


TASTE_KEYWORDS = {
    "辣": (
        "麻辣",
        "香辣",
        "酸辣",
        "重口味",
        "辣椒",
        "尖椒",
        "花椒",
        "豆瓣酱",
        "剁椒",
        "小米辣",
        "辣子",
    ),
    "清淡": ("清淡", "不辣", "少油"),
    "甜": ("甜品", "甜味", "香甜", "糖"),
    "酸": ("酸辣", "酸甜", "酸味", "醋"),
}

DISH_TYPE_KEYWORDS = {
    "饮品": (
        "奶茶",
        "红茶",
        "绿茶",
        "果茶",
        "特调",
        "鸡尾酒",
        "金汤力",
        "长岛冰茶",
        "酸梅汤",
        "饮料",
        "饮品",
        "茶",
    ),
    "蒸蛋": ("蒸水蛋", "鸡蛋羹", "水蒸蛋"),
    "羹": ("羹",),
    "粥": ("粥",),
    "面": ("拌面", "汤面", "面条", "面"),
    "饭": ("炒饭", "盖饭", "炊饭", "焖饭", "饭"),
    "甜品": ("甜品", "蛋糕", "冰淇淋", "雪媚娘", "司康"),
    "小吃": ("小吃", "炸串", "煎饺", "水饺", "馅饼"),
    "汤": ("罗宋汤", "羊肉汤", "排骨汤", "蛋花汤", "鸡蛋汤", "炖汤", "煲汤", "汤"),
}

QUERY_DISH_TYPE_KEYWORDS = {
    "汤": ("喝汤", "汤类", "炖汤", "煲汤", "汤"),
    "粥": ("喝粥", "粥"),
    "面": ("面条", "拌面", "汤面", "吃面", "面"),
    "饭": ("炒饭", "盖饭", "炊饭", "吃饭", "饭"),
    "饮品": ("饮料", "饮品", "奶茶", "喝茶", "特调", "鸡尾酒", "茶"),
    "甜品": ("甜品", "蛋糕", "冰淇淋", "雪媚娘", "司康"),
    "小吃": ("小吃", "炸串", "煎饺", "水饺", "馅饼"),
}

INGREDIENT_KEYWORDS = {
    "鸡蛋": ("鸡蛋", "蛋花", "蒸水蛋", "炒蛋"),
    "牛肉": ("牛肉", "牛腩", "肥牛", "牛排"),
    "猪肉": ("猪肉", "五花肉", "里脊", "排骨", "肉末"),
    "鸡肉": ("鸡肉", "鸡丁", "鸡腿", "鸡翅", "鸡胸", "鸡块"),
    "鱼": ("鱼", "鲈鱼", "鲤鱼", "鳕鱼"),
    "虾": ("虾", "虾仁"),
    "土豆": ("土豆", "马铃薯"),
    "茄子": ("茄子",),
    "豆腐": ("豆腐",),
    "青菜": ("青菜", "油麦菜", "生菜", "菠菜", "小白菜"),
}

RECOMMENDATION_MARKERS = (
    "想吃",
    "想喝",
    "吃什么",
    "喝什么",
    "推荐",
    "有什么",
    "来点",
    "几个",
    "几道",
    "下饭菜",
    "早餐",
    "午餐",
    "晚餐",
    "夜宵",
    "清淡",
    "少油",
    "不辣",
    "重口味",
)

RECIPE_SUFFIXES = ("怎么做", "的做法", "做法", "食谱", "菜谱", "需要什么食材", "的食材")
SPICY_NAME_HINTS = ("麻辣", "香辣", "酸辣", "辣子", "剁椒", "小米辣", "尖椒", "水煮", "小炒肉")
SOUP_NAME_HINTS = ("罗宋汤", "羊肉汤", "排骨汤", "蛋花汤", "鸡蛋汤", "紫菜汤")
REPRESENTATIVE_NAMES = {
    "辣": ("水煮肉片", "水煮牛肉", "辣子鸡", "小炒肉", "尖椒炒牛肉"),
    "汤": ("西红柿鸡蛋汤", "紫菜蛋花汤", "罗宋汤", "羊肉汤", "排骨苦瓜汤"),
    "鸡蛋": ("西红柿炒鸡蛋", "鸡蛋火腿炒黄瓜", "洋葱炒鸡蛋", "蒸水蛋"),
}


def normalize_query(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;“”\"'（）()]+", "", value).casefold()


def _first_heading(content: str) -> str | None:
    match = re.search(r"^\s*#\s+(.+?)\s*$", content, re.MULTILINE)
    if not match:
        return None
    title = re.sub(r"的做法\s*$", "", match.group(1).strip())
    return title or None


def infer_recipe_metadata(
    file_stem: str, content: str, file_path: str, category: str
) -> dict[str, Any]:
    name = _first_heading(content) or file_stem
    searchable = f"{name}\n{content}"

    dish_type = None
    for candidate, keywords in DISH_TYPE_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            dish_type = candidate
            break

    taste_tags = [
        tag
        for tag, keywords in TASTE_KEYWORDS.items()
        if any(keyword in searchable for keyword in keywords)
    ]
    ingredients = [
        ingredient
        for ingredient, keywords in INGREDIENT_KEYWORDS.items()
        if any(keyword in searchable for keyword in keywords)
    ]
    return {
        "name": name,
        "full_text": content,
        "file_path": file_path,
        "dish_type": dish_type,
        "taste_tags": taste_tags,
        "ingredients": ingredients,
        "category": category,
    }


def parse_query(query: str, dish_names: Iterable[str]) -> dict[str, Any]:
    normalized = normalize_query(query)
    normalized_names = sorted(
        ((normalize_query(name), name) for name in dish_names if name),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    stripped = normalized
    for suffix in RECIPE_SUFFIXES:
        stripped = stripped.removesuffix(normalize_query(suffix))

    dish_name = next((name for key, name in normalized_names if key == stripped), None)
    if dish_name is None:
        dish_name = next(
            (
                name
                for key, name in normalized_names
                if len(key) >= 2
                and key in normalized
                and any(marker in normalized for marker in ("怎么做", "做法", "食谱", "菜谱"))
            ),
            None,
        )
    if dish_name is None and stripped != normalized and stripped:
        dish_name = stripped
    if dish_name:
        return {
            "intent": "recipe_lookup",
            "dish_name": dish_name,
            "taste_tags": [],
            "dish_type": None,
            "ingredients": [],
        }

    taste_tags = []
    for tag, keywords in TASTE_KEYWORDS.items():
        if tag == "辣" and "不辣" in normalized:
            continue
        if tag in normalized or any(keyword in normalized for keyword in keywords):
            taste_tags.append(tag)
    dish_type = next(
        (
            candidate
            for candidate, keywords in QUERY_DISH_TYPE_KEYWORDS.items()
            if any(keyword in normalized for keyword in keywords)
        ),
        None,
    )
    ingredients = [
        ingredient
        for ingredient, keywords in INGREDIENT_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    is_recommendation = bool(
        taste_tags
        or dish_type
        or ingredients
        or any(marker in normalized for marker in RECOMMENDATION_MARKERS)
    )
    return {
        "intent": "recommendation" if is_recommendation else "chat",
        "dish_name": None,
        "taste_tags": taste_tags,
        "dish_type": dish_type,
        "ingredients": ingredients,
    }


def fuzzy_name_matches(query: str, documents: Iterable[Any]) -> list[Any]:
    normalized = normalize_query(query)
    for suffix in RECIPE_SUFFIXES:
        normalized = normalized.removesuffix(normalize_query(suffix))
    scored = []
    for doc in documents:
        name = str(doc.metadata.get("dish_name", ""))
        key = normalize_query(name)
        if not key:
            continue
        score = (
            1.0
            if normalized in key or key in normalized
            else SequenceMatcher(None, normalized, key).ratio()
        )
        if score >= 0.58:
            scored.append((score, doc))
    return [doc for _, doc in sorted(scored, key=lambda item: item[0], reverse=True)]


def canonical_retrieval_query(parsed: dict[str, Any]) -> str:
    terms = [*parsed["taste_tags"], parsed["dish_type"], *parsed["ingredients"]]
    return " ".join(term for term in terms if term)


def rank_recommendations(
    documents: Iterable[Any],
    parsed: dict[str, Any],
    vector_documents: Iterable[Any] = (),
) -> list[Any]:
    vector_parent_scores: dict[str, float] = {}
    vector_docs = list(vector_documents)
    for rank, doc in enumerate(vector_docs):
        parent_id = doc.metadata.get("parent_id")
        if parent_id and parent_id not in vector_parent_scores:
            vector_parent_scores[parent_id] = 1.0 - (rank / max(len(vector_docs), 1))

    requested_tastes = set(parsed["taste_tags"])
    requested_ingredients = set(parsed["ingredients"])
    requested_type = parsed["dish_type"]
    ranked = []

    for doc in documents:
        metadata = doc.metadata
        doc_tastes = set(metadata.get("taste_tags", []))
        doc_ingredients = set(metadata.get("ingredients", []))
        if requested_tastes and not requested_tastes.issubset(doc_tastes):
            continue
        if requested_type and metadata.get("dish_type") != requested_type:
            continue
        if requested_ingredients and not requested_ingredients.issubset(doc_ingredients):
            continue

        condition_count = (
            len(requested_tastes) + len(requested_ingredients) + int(bool(requested_type))
        )
        matched_count = (
            len(requested_tastes & doc_tastes)
            + len(requested_ingredients & doc_ingredients)
            + int(bool(requested_type and metadata.get("dish_type") == requested_type))
        )
        metadata_score = matched_count / max(condition_count, 1)

        name = str(metadata.get("dish_name", ""))
        text = str(metadata.get("full_text", doc.page_content))
        terms = [
            *requested_tastes,
            *requested_ingredients,
            *([requested_type] if requested_type else []),
        ]
        term_scores = []
        for term in terms:
            aliases = INGREDIENT_KEYWORDS.get(term, (term,))
            name_hit = term in name or any(alias in name for alias in aliases)
            text_hit = term in text
            exact_name_hit = term in name
            term_scores.append(
                1.0 if exact_name_hit else 0.80 if name_hit else 0.55 if text_hit else 0.0
            )
        keyword_score = sum(term_scores) / max(len(term_scores), 1)
        if "辣" in requested_tastes and any(hint in name for hint in SPICY_NAME_HINTS):
            keyword_score = 1.0
        if requested_type == "汤" and any(hint in name for hint in SOUP_NAME_HINTS):
            keyword_score = 1.0
        representative_score = 0
        representative_terms = [*requested_tastes, *requested_ingredients]
        if requested_type:
            representative_terms.append(requested_type)
        for term in representative_terms:
            if any(hint in name for hint in REPRESENTATIVE_NAMES.get(term, ())):
                representative_score = 1
                break
        vector_score = vector_parent_scores.get(metadata.get("parent_id"), 0.0)
        score = metadata_score * 0.60 + keyword_score * 0.25 + vector_score * 0.15
        ranked.append((representative_score, score, keyword_score, name, doc))

    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [doc for _, _, _, _, doc in ranked]
