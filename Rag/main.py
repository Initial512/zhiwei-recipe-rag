"""Application orchestration for the replacement GraphRAG implementation."""

from __future__ import annotations

import logging
import os

from config import DEFAULT_CONFIG, RAGConfig
from dotenv import load_dotenv
from langchain_core.documents import Document

from rag_modules.generation_integration import GenerationIntegrationModule
from rag_modules.graph_data_preparation import GraphDataPreparationModule
from rag_modules.graph_rag_retrieval import GraphRAGRetrieval
from rag_modules.hybrid_retrieval import HybridRetrievalModule
from rag_modules.intelligent_query_router import IntelligentQueryRouter
from rag_modules.milvus_index_construction import MilvusIndexConstructionModule
from rag_modules.session_cache_manager import SessionCacheManager

load_dotenv()
logger = logging.getLogger(__name__)


class RecipeRAGSystem:
    """Compose the cloned RAG modules behind the existing web-service API."""

    CATEGORY_ORDER = [
        "荤菜",
        "素菜",
        "汤品",
        "甜品",
        "早餐",
        "主食",
        "水产",
        "调料",
        "饮品",
        "半成品",
    ]
    CATEGORY_ALIASES = {"汤类": "汤品", "饮料": "饮品"}

    def __init__(self, config: RAGConfig | None = None):
        self.config = config or DEFAULT_CONFIG
        self.data_module: GraphDataPreparationModule | None = None
        self.index_module: MilvusIndexConstructionModule | None = None
        self.retrieval_module: HybridRetrievalModule | None = None
        self.graph_retrieval_module: GraphRAGRetrieval | None = None
        self.query_router: IntelligentQueryRouter | None = None
        self.cache_manager = SessionCacheManager()
        self.generation_module: GenerationIntegrationModule | None = None
        self.chunks: list[Document] = []

    @classmethod
    def normalize_category(cls, value: object) -> str:
        raw = str(value or "").strip()
        primary = next((part.strip() for part in raw.split(",") if part.strip()), "")
        return cls.CATEGORY_ALIASES.get(primary, primary)

    def get_supported_categories(self) -> list[str]:
        return self.CATEGORY_ORDER.copy()

    def initialize_system(self) -> None:
        required = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
        if not all(os.getenv(name) for name in required):
            raise ValueError(f"Missing required model configuration: {', '.join(required)}")
        self.data_module = GraphDataPreparationModule(
            self.config.neo4j_uri,
            self.config.neo4j_user,
            self.config.neo4j_password,
            self.config.neo4j_database,
        )
        self.generation_module = GenerationIntegrationModule(
            self.config.llm_model, self.config.temperature, self.config.max_tokens
        )

    def _decorate_documents(self) -> None:
        assert self.data_module
        for doc in self.data_module.documents:
            metadata = doc.metadata
            metadata["parent_id"] = str(metadata["node_id"])
            metadata["dish_name"] = metadata["recipe_name"]
            metadata["category"] = self.normalize_category(metadata.get("category"))
            metadata["difficulty"] = _difficulty(metadata.get("difficulty"))

    def _ensure_index(self) -> None:
        assert self.index_module
        expected_count = len(self.chunks)
        rebuild = not self.index_module.has_collection()
        if not rebuild:
            try:
                indexed_count = int(self.index_module.get_collection_stats().get("row_count", 0))
            except (AttributeError, TypeError, ValueError):
                indexed_count = 0
            rebuild = indexed_count != expected_count or not self.index_module.load_collection()
        if rebuild:
            logger.warning("Rebuilding Milvus collection with %s RAG chunks", expected_count)
            if not self.index_module.build_vector_index(self.chunks):
                raise RuntimeError("Unable to build Milvus vector index")

    def build_knowledge_base(self) -> None:
        if not self.data_module or not self.generation_module:
            raise RuntimeError("System must be initialized first")
        self.data_module.load_graph_data()
        self.data_module.build_recipe_documents()
        self._decorate_documents()
        self.chunks = self.data_module.chunk_documents()
        self.index_module = MilvusIndexConstructionModule(
            self.config.milvus_host,
            self.config.milvus_port,
            self.config.milvus_collection_name,
            model_name=self.config.embedding_model,
        )
        self._ensure_index()
        self.cache_manager.embedding_model = self.index_module.embeddings
        self.retrieval_module = HybridRetrievalModule(
            self.config, self.index_module, self.data_module, self.generation_module.client
        )
        self.retrieval_module.initialize(self.chunks)
        self.graph_retrieval_module = GraphRAGRetrieval(self.config, self.generation_module.client)
        self.graph_retrieval_module.initialize()
        self.query_router = IntelligentQueryRouter(
            self.retrieval_module,
            self.graph_retrieval_module,
            self.generation_module.client,
            self.config,
        )

    def retrieve(self, question: str, top_k: int | None = None) -> list[Document]:
        if not self.query_router:
            raise RuntimeError("Knowledge base has not been built")
        documents, _analysis = self.query_router.route_query(question, top_k or self.config.top_k)
        return documents

    def close(self) -> None:
        modules = (
            self.retrieval_module,
            self.graph_retrieval_module,
            self.data_module,
            self.index_module,
        )
        for module in modules:
            if module and hasattr(module, "close"):
                module.close()


def _difficulty(value: object) -> str:
    try:
        level = float(value)
    except (TypeError, ValueError):
        return "未知"
    return "非常简单" if level <= 1 else "简单" if level <= 2 else "中等" if level <= 3 else "困难"
