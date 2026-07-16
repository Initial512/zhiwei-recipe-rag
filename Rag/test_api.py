from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import (
    RECIPE_IMAGE_DIR,
    _all_unique_recipes,
    _classify_query,
    _image_url,
    _local_query_type,
    _parse_recipe_doc,
    _recipe_summary,
    _sse,
    _unique_sources,
    app,
)
from main import GraphRecipeDataModule
from rag_modules.data_preparation import DataPreparationModule


def test_unique_sources_deduplicates_by_dish_and_category():
    docs = [
        SimpleNamespace(metadata={"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单"}),
        SimpleNamespace(metadata={"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单"}),
        SimpleNamespace(metadata={"dish_name": "奶茶", "category": "饮品", "difficulty": "中等"}),
    ]

    assert _unique_sources(docs) == [
        {"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单", "image_url": None},
        {
            "dish_name": "奶茶",
            "category": "饮品",
            "difficulty": "中等",
            "image_url": "/recipe-images/%E5%A5%B6%E8%8C%B6.webp",
        },
    ]


def test_sse_keeps_unicode_and_event_name():
    assert _sse("delta", "一碗汤") == "event: delta\ndata: 一碗汤\n\n"


def test_sse_preserves_multiline_chunks():
    assert _sse("delta", "食材\n- 番茄") == (
        "event: delta\ndata: 食材\ndata: - 番茄\n\n"
    )


def test_categories_have_stable_declared_order():
    assert DataPreparationModule.get_supported_categories() == [
        "荤菜",
        "素菜",
        "汤品",
        "甜品",
        "早餐",
        "主食",
        "水产",
        "调料",
        "饮品",
    ]


def test_all_unique_recipes_excludes_unknown_categories_and_deduplicates():
    documents = [
        SimpleNamespace(metadata={"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单"}),
        SimpleNamespace(metadata={"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单"}),
        SimpleNamespace(metadata={"dish_name": "示例菜", "category": "荤菜", "difficulty": "未知"}),
        SimpleNamespace(metadata={"dish_name": "奶茶", "category": "饮品", "difficulty": "中等"}),
        SimpleNamespace(metadata={"dish_name": "地方小吃", "category": "其他", "difficulty": "未知"}),
    ]
    system = SimpleNamespace(
        data_module=SimpleNamespace(
            documents=documents,
            get_supported_categories=lambda: ["汤品", "饮品"],
        )
    )

    assert _all_unique_recipes(system) == [
        {"dish_name": "番茄汤", "category": "汤品", "difficulty": "简单", "image_url": None},
        {
            "dish_name": "奶茶",
            "category": "饮品",
            "difficulty": "中等",
            "image_url": "/recipe-images/%E5%A5%B6%E8%8C%B6.webp",
        },
    ]


def test_graph_category_normalization_keeps_source_recipes_visible():
    assert GraphRecipeDataModule.normalize_category("汤类") == "汤品"
    assert GraphRecipeDataModule.normalize_category("饮料") == "饮品"
    assert GraphRecipeDataModule.normalize_category("汤类,早餐,主食") == "汤品"
    assert GraphRecipeDataModule.normalize_category("甜品") == "甜品"


def test_image_url_uses_encoded_unicode_filename():
    assert (RECIPE_IMAGE_DIR / "宫保鸡丁.webp").is_file()
    assert _image_url("宫保鸡丁") == "/recipe-images/%E5%AE%AB%E4%BF%9D%E9%B8%A1%E4%B8%81.webp"
    assert _image_url("示例菜") is None


def test_static_image_route_serves_unicode_filename():
    response = TestClient(app).get("/recipe-images/%E5%AE%AB%E4%BF%9D%E9%B8%A1%E4%B8%81.webp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_every_real_recipe_has_a_matching_image():
    recipe_dir = RECIPE_IMAGE_DIR.parent / "dishes"
    recipe_names = {
        path.stem
        for path in recipe_dir.rglob("*.md")
        if path.stem != "示例菜"
    }
    image_names = {path.stem for path in RECIPE_IMAGE_DIR.glob("*.webp")}

    assert recipe_names <= image_names
    assert image_names - recipe_names == {"肉末茄子"}


def test_every_recipe_image_is_a_small_webp():
    images = list(RECIPE_IMAGE_DIR.glob("*.webp"))

    assert len(images) == 322
    for image in images:
        payload = image.read_bytes()
        assert len(payload) < 1024 * 1024
        assert payload[:4] == b"RIFF"
        assert payload[8:12] == b"WEBP"


def test_parse_recipe_doc_returns_structured_sections_and_optional_metrics():
    doc = SimpleNamespace(
        metadata={"dish_name": "测试菜", "category": "荤菜", "difficulty": "简单"},
        page_content="""# 测试菜的做法

一道家常快手菜。

预估烹饪难度：★★

## 计算

- 主料
  - 鸡肉 = 200g
  - 青椒 1个

## 操作

### 家常做法

1. 鸡肉切丁。
2. 下锅翻炒至熟。

## 附加内容

可按口味增减辣椒。
制作耗时约20分钟，适合2人份。
""",
    )

    parsed = _parse_recipe_doc(doc)

    assert parsed["description"] == "一道家常快手菜。"
    assert parsed["image_url"] is None
    assert parsed["ingredient_groups"][0]["name"] == "主料"
    assert {"name": "鸡肉", "amount": "200g"} in parsed["ingredient_groups"][0]["items"]
    assert parsed["step_groups"][0]["steps"] == ["鸡肉切丁。", "下锅翻炒至熟。"]
    assert parsed["tips"] == ["可按口味增减辣椒。", "制作耗时约20分钟，适合2人份。"]
    assert parsed["cook_time"] == "20分钟"
    assert parsed["servings"] == "2人份"


def test_parse_recipe_doc_omits_unknown_metrics():
    doc = SimpleNamespace(
        metadata={"dish_name": "清炒菜", "category": "素菜", "difficulty": "未知"},
        page_content="# 清炒菜的做法\n\n清爽可口。\n\n## 操作\n\n- 洗净后炒熟。",
    )

    parsed = _parse_recipe_doc(doc)

    assert "cook_time" not in parsed
    assert "servings" not in parsed


def test_recipe_summary_preserves_image_url():
    doc = SimpleNamespace(
        metadata={"dish_name": "宫保鸡丁", "category": "荤菜", "difficulty": "困难"},
        page_content="# 宫保鸡丁的做法\n\n经典川菜。",
    )

    assert _recipe_summary(doc)["image_url"] == (
        "/recipe-images/%E5%AE%AB%E4%BF%9D%E9%B8%A1%E4%B8%81.webp"
    )


def _classification_system(model_result="assistant"):
    documents = [
        SimpleNamespace(metadata={"dish_name": "宫保鸡丁", "category": "荤菜", "difficulty": "困难"}),
    ]
    return SimpleNamespace(
        data_module=SimpleNamespace(
            documents=documents,
            get_supported_categories=lambda: ["荤菜"],
        ),
        generation_module=SimpleNamespace(
            classify_query_scope=lambda question: model_result,
        ),
    )


def test_local_query_type_recognizes_recipe_intent_and_dish_name():
    system = _classification_system()

    assert _local_query_type(system, "推荐几道辣菜") == "recipe"
    assert _local_query_type(system, "宫保鸡丁怎么做") == "recipe"
    assert _local_query_type(system, "你是谁") is None


def test_classification_uses_model_for_non_recipe_queries():
    system = _classification_system(model_result="assistant")

    assert _classify_query(system, "你能做什么") == "assistant"


def test_local_recipe_rule_takes_priority_over_model():
    system = _classification_system(model_result="assistant")

    assert _classify_query(system, "推荐几道辣菜") == "recipe"
