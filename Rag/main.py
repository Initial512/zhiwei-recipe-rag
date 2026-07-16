"""GraphRAG orchestration used by the existing FastAPI surface."""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from typing import Iterable

from dotenv import load_dotenv
from langchain_core.documents import Document
from neo4j import GraphDatabase

from config import DEFAULT_CONFIG, RAGConfig
from rag_modules.generation_integration import GenerationIntegrationModule
from rag_modules.milvus_index_construction import MilvusIndexConstructionModule
from rag_modules.recipe_metadata import infer_recipe_metadata

load_dotenv()
logger = logging.getLogger(__name__)


class GraphRecipeDataModule:
    """Read-only projection of the imported Neo4j recipe graph."""

    # The imported graph retains the source labels (for example “汤类” and
    # “饮料”), while the existing frontend has stable display labels.  Keep
    # that translation at the graph boundary so every API uses one taxonomy.
    CATEGORY_ORDER = ["荤菜", "素菜", "汤品", "甜品", "早餐", "主食", "水产", "调料", "饮品", "半成品"]
    CATEGORY_ALIASES = {
        "汤类": "汤品",
        "饮料": "饮品",
    }

    def __init__(self, config: RAGConfig):
        self.config = config
        self.driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        self.documents: list[Document] = []

    def get_supported_categories(self) -> list[str]:
        return self.CATEGORY_ORDER.copy()

    @classmethod
    def normalize_category(cls, value: object) -> str:
        """Choose the primary source category and convert it to a UI label."""
        raw = str(value or "").strip()
        primary = next((part.strip() for part in raw.split(",") if part.strip()), "")
        return cls.CATEGORY_ALIASES.get(primary, primary)

    def load_documents(self) -> list[Document]:
        query = """
        MATCH (r:Recipe)
        WHERE r.nodeId >= '201000000' AND r.name <> '示例菜'
        OPTIONAL MATCH (r)-[:BELONGS_TO_CATEGORY|BELONGS_TO]->(category)
        WITH r, head(collect(category.name)) AS relation_category
        OPTIONAL MATCH (r)-[req:REQUIRES]->(ingredient:Ingredient)
        WITH r, relation_category,
             collect({name: ingredient.name, amount: req.amount, unit: req.unit}) AS ingredients
        OPTIONAL MATCH (r)-[contains:CONTAINS_STEP]->(step:CookingStep)
        WITH r, relation_category, ingredients,
             collect({name: step.name, description: step.description, methods: step.methods,
                      tools: step.tools, order: coalesce(contains.stepOrder, step.stepNumber)}) AS steps
        RETURN r, relation_category, ingredients, steps
        ORDER BY r.name
        """
        docs: list[Document] = []
        with self.driver.session(database=self.config.neo4j_database) as session:
            for record in session.run(query):
                recipe = dict(record["r"])
                name = recipe["name"]
                # A recipe can have several BELONGS_TO_CATEGORY edges and
                # Neo4j does not guarantee their collection order.  The CSV
                # property preserves the source's ordered primary category.
                category = self.normalize_category(
                    recipe.get("category") or record["relation_category"]
                )
                ingredient_lines = []
                for item in record["ingredients"]:
                    if not item["name"]:
                        continue
                    amount = "".join(str(x) for x in (item["amount"], item["unit"]) if x)
                    ingredient_lines.append(f"- {item['name']}{' = ' + amount if amount else ''}")
                step_lines = []
                steps = sorted((item for item in record["steps"] if item["name"]), key=lambda item: item["order"] or 9999)
                for index, item in enumerate(steps, 1):
                    detail = "；".join(str(value) for value in (item["description"], item["methods"], item["tools"]) if value)
                    step_lines.append(f"{index}. {item['name']}{'：' + detail if detail else ''}")
                content = "\n\n".join(filter(None, [
                    f"# {name}", str(recipe.get("description") or ""),
                    "## 食材\n" + "\n".join(ingredient_lines) if ingredient_lines else "",
                    "## 操作\n" + "\n".join(step_lines) if step_lines else "",
                    "## 附加内容\n" + str(recipe.get("tags") or "") if recipe.get("tags") else "",
                ]))
                inferred = infer_recipe_metadata(
                    name,
                    content,
                    str(recipe.get("filePath") or ""),
                    category,
                )
                docs.append(Document(page_content=content, metadata={
                    "parent_id": str(recipe["nodeId"]), "node_id": str(recipe["nodeId"]),
                    "dish_name": name, "recipe_name": name, "category": category,
                    "difficulty": _difficulty(recipe.get("difficulty")),
                    "cook_time": str(recipe.get("cookTime") or ""),
                    "servings": str(recipe.get("servings") or ""), "full_text": content,
                    "dish_type": inferred["dish_type"],
                    "taste_tags": inferred["taste_tags"],
                    "ingredients": inferred["ingredients"],
                }))
        self.documents = docs
        return docs

    def get_parent_documents(self, chunks: Iterable[Document]) -> list[Document]:
        ids = {doc.metadata.get("parent_id") or doc.metadata.get("node_id") for doc in chunks}
        return [doc for doc in self.documents if doc.metadata.get("parent_id") in ids]

    def close(self) -> None:
        self.driver.close()


class GraphHybridRetrieval:
    """Combines graph candidates with Milvus semantic candidates."""

    def __init__(self, config: RAGConfig, data_module: GraphRecipeDataModule):
        self.config, self.data_module = config, data_module
        self.milvus = MilvusIndexConstructionModule(
            host=config.milvus_host,
            port=config.milvus_port,
            collection_name=config.milvus_collection_name,
            model_name=config.embedding_model,
        )

    def initialize(self) -> None:
        if self.milvus.has_collection():
            stats = self.milvus.get_collection_stats()
            try:
                indexed_count = int(stats.get("row_count", 0))
            except (TypeError, ValueError):
                indexed_count = 0
            if indexed_count != len(self.data_module.documents) or not self.milvus.load_collection():
                logger.warning(
                    "Rebuilding Milvus collection because it has %s recipes but the graph has %s",
                    indexed_count,
                    len(self.data_module.documents),
                )
                self.milvus.delete_collection()
                if not self.milvus.build_vector_index(self.data_module.documents):
                    raise RuntimeError("Unable to rebuild the Milvus collection")
            return
        # The graph is the source of truth. Only the initial empty Milvus volume is populated.
        if not self.milvus.build_vector_index(self.data_module.documents):
            raise RuntimeError("Unable to build the initial Milvus collection")

    def hybrid_search(self, query: str, top_k: int = 5) -> list[Document]:
        strategy = self._strategy(query)
        graph_docs = self._graph_search(query, top_k) if strategy != "semantic" else []
        semantic_docs = self._milvus_search(query, top_k) if strategy != "graph" else []
        by_id = {doc.metadata["parent_id"]: doc for doc in graph_docs}
        by_id.update({doc.metadata["parent_id"]: doc for doc in semantic_docs})
        if not by_id:
            terms = [term for term in re.split(r"\s+", query) if term]
            return [doc for doc in self.data_module.documents if any(term in doc.page_content for term in terms)][:top_k]
        return list(by_id.values())[:top_k]

    @staticmethod
    def _strategy(query: str) -> str:
        """Route relationship questions to graph search and broad questions to both stores."""
        relation_markers = ("搭配", "配什么", "哪些", "含有", "不用", "替代", "关系", "步骤", "食材")
        if any(marker in query for marker in relation_markers):
            return "graph" if len(query) < 12 else "combined"
        return "semantic"

    def _graph_search(self, query: str, top_k: int) -> list[Document]:
        cypher = """
        MATCH (r:Recipe)
        WHERE r.nodeId >= '201000000' AND r.name <> '示例菜'
          AND (r.name CONTAINS $query OR coalesce(r.description, '') CONTAINS $query
               OR EXISTS { MATCH (r)-[:REQUIRES]->(i:Ingredient) WHERE i.name CONTAINS $query })
        RETURN r.nodeId AS node_id
        LIMIT $limit
        """
        with self.data_module.driver.session(database=self.config.neo4j_database) as session:
            ids = [str(row["node_id"]) for row in session.run(cypher, query=query, limit=top_k)]
        docs = {doc.metadata["parent_id"]: doc for doc in self.data_module.documents}
        return [docs[item] for item in ids if item in docs]

    def _milvus_search(self, query: str, top_k: int) -> list[Document]:
        try:
            results = self.milvus.similarity_search(query, k=top_k)
        except Exception:
            logger.exception("Milvus semantic search failed; retaining graph results")
            return []
        docs = {doc.metadata["parent_id"]: doc for doc in self.data_module.documents}
        return [docs[result["metadata"].get("parent_id")] for result in results
                if result["metadata"].get("parent_id") in docs]


class RecipeRAGSystem:
    def __init__(self, config: RAGConfig | None = None):
        self.config = config or DEFAULT_CONFIG
        self.data_module: GraphRecipeDataModule | None = None
        self.retrieval_module: GraphHybridRetrieval | None = None
        self.generation_module: GenerationIntegrationModule | None = None
        self.index_module = None

    def initialize_system(self) -> None:
        if not all(os.getenv(name) for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")):
            raise ValueError("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be configured")
        self.data_module = GraphRecipeDataModule(self.config)
        self.generation_module = GenerationIntegrationModule(self.config.temperature, self.config.max_tokens)

    def build_knowledge_base(self) -> None:
        if not self.data_module:
            raise RuntimeError("System must be initialized first")
        self.data_module.load_documents()
        self.retrieval_module = GraphHybridRetrieval(self.config, self.data_module)
        self.retrieval_module.initialize()

    def close(self) -> None:
        if self.data_module:
            self.data_module.close()


def _difficulty(value: object) -> str:
    try:
        level = float(value)
    except (TypeError, ValueError):
        return "未知"
    return "非常简单" if level <= 1 else "简单" if level <= 2 else "中等" if level <= 3 else "困难"
