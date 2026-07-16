"""Runtime configuration for the GraphRAG recipe service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RAGConfig:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    milvus_host: str = os.getenv("MILVUS_HOST", "localhost")
    milvus_port: int = int(os.getenv("MILVUS_PORT", "19530"))
    milvus_collection_name: str = os.getenv("MILVUS_COLLECTION", "cooking_knowledge")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    temperature: float = 0.1
    max_tokens: int = 2048


DEFAULT_CONFIG = RAGConfig()
