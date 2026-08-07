"""
真正的图RAG检索模块
基于图结构的知识推理和检索，而非简单的关键词匹配
"""

import hashlib
import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from langchain_core.documents import Document
from neo4j import GraphDatabase

from .structured_output import bounded_int, parse_json_object, string_list

logger = logging.getLogger(__name__)
GRAPH_INTENT_TIMEOUT_SECONDS = 15
GRAPH_RELATION_KEYWORDS = ("关系", "相连", "关联", "多跳", "路径", "通过", "搭配")


class QueryType(Enum):
    """查询类型枚举"""

    ENTITY_RELATION = "entity_relation"  # 实体关系查询：A和B有什么关系？
    MULTI_HOP = "multi_hop"  # 多跳查询：A通过什么连接到C？
    SUBGRAPH = "subgraph"  # 子图查询：A相关的所有信息
    PATH_FINDING = "path_finding"  # 路径查找：从A到B的最佳路径
    CLUSTERING = "clustering"  # 聚类查询：和A相似的都有什么？


@dataclass
class GraphQuery:
    """图查询结构"""

    query_type: QueryType
    source_entities: list[str]
    target_entities: list[str] = None
    relation_types: list[str] = None
    max_depth: int = 2
    max_nodes: int = 50
    constraints: dict[str, Any] = None


@dataclass
class GraphPath:
    """图路径结构"""

    nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    path_length: int
    relevance_score: float
    path_type: str


@dataclass
class KnowledgeSubgraph:
    """知识子图结构"""

    central_nodes: list[dict[str, Any]]
    connected_nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    graph_metrics: dict[str, float]
    reasoning_chains: list[list[str]]


class GraphRAGRetrieval:
    """
    真正的图RAG检索系统
    核心特点：
    1. 查询意图理解：识别图查询模式
    2. 多跳图遍历：深度关系探索
    3. 子图提取：相关知识网络
    4. 图结构推理：基于拓扑的推理
    5. 动态查询规划：自适应遍历策略
    """

    def __init__(self, config, llm_client):
        self.config = config
        self.llm_client = llm_client
        self.driver = None

        # 图结构缓存
        self.entity_cache = {}
        self.relation_cache = {}
        self.subgraph_cache = {}

    def initialize(self):
        """初始化图RAG检索系统"""
        logger.info("初始化图RAG检索系统...")

        # 连接Neo4j
        try:
            self.driver = GraphDatabase.driver(
                self.config.neo4j_uri, auth=(self.config.neo4j_user, self.config.neo4j_password)
            )
            # 测试连接
            with self.driver.session() as session:
                session.run("RETURN 1")
            logger.info("Neo4j连接成功")
        except Exception as e:
            logger.error(f"Neo4j连接失败: {e}")
            return

        # 预热：构建实体和关系索引
        self._build_graph_index()

    def _build_graph_index(self):
        """构建图索引以加速查询"""
        logger.info("构建图结构索引...")

        try:
            with self.driver.session() as session:
                # 构建实体索引 - 修复Neo4j语法兼容性问题
                entity_query = """
                MATCH (n)
                WHERE n.nodeId IS NOT NULL
                WITH n, COUNT { (n)--() } as degree
                RETURN labels(n) as node_labels, n.nodeId as node_id, 
                       n.name as name, n.category as category, degree
                ORDER BY degree DESC
                LIMIT 1000
                """

                result = session.run(entity_query)
                for record in result:
                    node_id = record["node_id"]
                    self.entity_cache[node_id] = {
                        "labels": record["node_labels"],
                        "name": record["name"],
                        "category": record["category"],
                        "degree": record["degree"],
                    }

                # 构建关系类型索引
                relation_query = """
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as frequency
                ORDER BY frequency DESC
                """

                result = session.run(relation_query)
                for record in result:
                    rel_type = record["rel_type"]
                    self.relation_cache[rel_type] = record["frequency"]

                logger.info(
                    f"索引构建完成: {len(self.entity_cache)}个实体, {len(self.relation_cache)}个关系类型"
                )

        except Exception as e:
            logger.error(f"构建图索引失败: {e}")

    def understand_graph_query(self, query: str) -> GraphQuery:
        """
        理解查询的图结构意图
        这是图RAG的核心：从自然语言到图查询的转换
        """
        prompt = f"""
        作为图数据库专家，分析以下查询的图结构意图：
        
        查询：{query}
        
        请识别：
        1. 查询类型：
           - entity_relation: 询问实体间的直接关系（如：鸡肉和胡萝卜能一起做菜吗？）
           - multi_hop: 需要多跳推理（如：鸡肉配什么蔬菜？需要：鸡肉→菜品→食材→蔬菜）
           - subgraph: 需要完整子图（如：川菜有什么特色？需要川菜相关的完整知识网络）
           - path_finding: 路径查找（如：从食材到成品菜的制作路径）
           - clustering: 聚类相似性（如：和宫保鸡丁类似的菜有哪些？）
        
        2. 核心实体：查询中的关键实体名称
        3. 目标实体：期望找到的实体类型
        4. 关系类型：涉及的关系类型
        5. 遍历深度：需要的图遍历深度（1-3跳）
        
        示例：
        查询："鸡肉配什么蔬菜好？"
        分析：这是multi_hop查询，需要通过"鸡肉→使用鸡肉的菜品→这些菜品使用的蔬菜"的路径推理
        
        返回JSON格式：
        {{
            "query_type": "multi_hop",
            "source_entities": ["鸡肉"],
            "target_entities": ["蔬菜类食材"],
            "relation_types": ["REQUIRES", "BELONGS_TO_CATEGORY"],
            "max_depth": 3,
            "reasoning": "需要多跳推理：鸡肉→菜品→食材→蔬菜"
        }}
        """

        try:
            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
                timeout=GRAPH_INTENT_TIMEOUT_SECONDS,
            )

            result, source = parse_json_object(response.choices[0].message.content)

            graph_query = GraphQuery(
                query_type=QueryType(result.get("query_type", "subgraph")),
                source_entities=string_list(result.get("source_entities")),
                target_entities=string_list(result.get("target_entities")),
                relation_types=string_list(result.get("relation_types")),
                max_depth=bounded_int(result.get("max_depth"), default=2, minimum=1, maximum=3),
                max_nodes=50,
            )
            logger.info(
                "Graph intent parsed source=%s entities=%s targets=%s",
                source,
                len(graph_query.source_entities),
                len(graph_query.target_entities or []),
            )
            return graph_query

        except Exception as exc:
            logger.warning("Graph intent parsing failed; using index fallback (%s)", exc)
            return self._graph_query_from_index(query)

    def _graph_query_from_index(self, query: str) -> GraphQuery:
        """Build a bounded graph query from cached graph entities when the LLM is unavailable."""
        normalized_query = query.casefold()
        entries = sorted(
            self.entity_cache.values(), key=lambda entry: entry.get("degree", 0), reverse=True
        )
        source_entities, target_entities = self._matching_graph_entities(entries, normalized_query)

        if not source_entities:
            database_entries = self._query_matching_graph_entities(query)
            source_entities, target_entities = self._matching_graph_entities(
                database_entries, normalized_query
            )

        if not source_entities:
            logger.warning("Graph index fallback found no matching entities")
            return GraphQuery(query_type=QueryType.SUBGRAPH, source_entities=[], max_depth=2)

        relationship_query = any(keyword in query for keyword in GRAPH_RELATION_KEYWORDS)
        query_type = (
            QueryType.MULTI_HOP
            if relationship_query or target_entities
            else QueryType.ENTITY_RELATION
        )
        max_depth = 3 if query_type == QueryType.MULTI_HOP else 1
        logger.warning(
            "Graph index fallback entities=%s targets=%s query_type=%s",
            len(source_entities),
            len(target_entities),
            query_type.value,
        )
        return GraphQuery(
            query_type=query_type,
            source_entities=source_entities[:5],
            target_entities=target_entities[:3],
            max_depth=max_depth,
            max_nodes=50,
        )

    @staticmethod
    def _matching_graph_entities(
        entries: list[dict[str, Any]], normalized_query: str
    ) -> tuple[list[str], list[str]]:
        source_entities = []
        target_entities = []
        sorted_entries = sorted(
            entries,
            key=lambda entry: (
                len(str(entry.get("name") or "")),
                entry.get("degree", 0),
            ),
            reverse=True,
        )
        for entry in sorted_entries:
            name = str(entry.get("name") or "").strip()
            category = str(entry.get("category") or "").strip()
            is_duplicate_substring = any(
                name in matched_name or matched_name in name for matched_name in source_entities
            )
            if name and name.casefold() in normalized_query and not is_duplicate_substring:
                source_entities.append(name)
            if (
                category
                and category.casefold() in normalized_query
                and category not in target_entities
            ):
                target_entities.append(category)
            if len(source_entities) >= 5 and len(target_entities) >= 3:
                break
        return source_entities, target_entities

    def _query_matching_graph_entities(self, query: str) -> list[dict[str, Any]]:
        """Look up exact names/categories outside the bounded in-memory graph cache."""
        if not self.driver:
            return []
        cypher_query = """
        MATCH (n)
        WHERE n.name IS NOT NULL
          AND (
              toLower($query) CONTAINS toLower(n.name)
              OR (n.category IS NOT NULL AND toLower($query) CONTAINS toLower(n.category))
          )
        WITH n, COUNT { (n)--() } AS degree
        RETURN n.name AS name, n.category AS category, degree
        ORDER BY degree DESC
        LIMIT 50
        """
        try:
            with self.driver.session() as session:
                records = session.run(cypher_query, {"query": query})
                return [
                    {
                        "name": record["name"],
                        "category": record["category"],
                        "degree": record["degree"],
                    }
                    for record in records
                ]
        except Exception:
            logger.exception("Graph index fallback database lookup failed")
            return []

    def multi_hop_traversal(self, graph_query: GraphQuery) -> list[GraphPath]:
        """
        多跳图遍历：这是图RAG的核心优势
        通过图结构发现隐含的知识关联
        """
        logger.info(f"执行多跳遍历: {graph_query.source_entities} -> {graph_query.target_entities}")

        paths = []

        if not self.driver:
            logger.error("Neo4j连接未建立")
            return paths

        try:
            with self.driver.session() as session:
                # 构建多跳遍历查询
                source_entities = graph_query.source_entities
                target_entities = graph_query.target_entities or []
                max_depth = graph_query.max_depth

                # 根据查询类型选择不同的遍历策略
                if graph_query.query_type == QueryType.MULTI_HOP:
                    cypher_query = f"""
                    // 多跳推理查询
                    UNWIND $source_entities as source_name
                    MATCH (source)
                    WHERE source.name CONTAINS source_name OR source.nodeId = source_name
                    
                    // 执行多跳遍历
                    MATCH path = (source)-[*1..{max_depth}]-(target)
                    WHERE NOT source = target
                    AND (
                        size($target_entities) = 0
                        OR ANY(target_name IN $target_entities WHERE
                            target.name CONTAINS target_name
                            OR target_name CONTAINS target.name
                            OR coalesce(target.category, '') CONTAINS target_name
                        )
                    )
                    
                    // 计算路径相关性
                    WITH path, source, target,
                         length(path) as path_len,
                         relationships(path) as rels,
                         nodes(path) as path_nodes
                    
                    // 路径评分：短路径 + 高度数节点 + 关系类型匹配
                    WITH path, source, target, path_len, rels, path_nodes,
                         (1.0 / path_len) + 
                         (REDUCE(s = 0.0, n IN path_nodes | s + COUNT {{ (n)--() }}) / 10.0 / size(path_nodes)) +
                         (CASE WHEN ANY(r IN rels WHERE type(r) IN $relation_types) THEN 0.3 ELSE 0.0 END) as relevance
                    
                    ORDER BY relevance DESC
                    LIMIT 20
                    
                    RETURN path, source, target, path_len, rels, path_nodes, relevance
                    """

                    result = session.run(
                        cypher_query,
                        {
                            "source_entities": source_entities,
                            "target_entities": target_entities,
                            "relation_types": graph_query.relation_types or [],
                        },
                    )

                    for record in result:
                        path_data = self._parse_neo4j_path(record)
                        if path_data:
                            paths.append(path_data)

                elif graph_query.query_type == QueryType.ENTITY_RELATION:
                    # 实体间关系查询
                    paths.extend(self._find_entity_relations(graph_query, session))

                elif graph_query.query_type == QueryType.PATH_FINDING:
                    # 最短路径查找
                    paths.extend(self._find_shortest_paths(graph_query, session))

        except Exception as e:
            logger.error(f"多跳遍历失败: {e}")

        logger.info(f"多跳遍历完成，找到 {len(paths)} 条路径")
        return paths

    def extract_knowledge_subgraph(self, graph_query: GraphQuery) -> KnowledgeSubgraph:
        """
        提取知识子图：获取实体相关的完整知识网络
        这体现了图RAG的整体性思维
        """
        logger.info(f"提取知识子图: {graph_query.source_entities}")

        if not self.driver:
            logger.error("Neo4j连接未建立")
            return self._fallback_subgraph_extraction(graph_query)

        try:
            with self.driver.session() as session:
                # 简化的子图提取（不依赖APOC）
                cypher_query = f"""
                // 找到源实体
                UNWIND $source_entities as entity_name
                MATCH (source)
                WHERE source.name CONTAINS entity_name 
                   OR source.nodeId = entity_name
                
                // 获取指定深度的邻居
                MATCH (source)-[r*1..{graph_query.max_depth}]-(neighbor)
                WITH source, collect(DISTINCT neighbor) as neighbors, 
                     collect(DISTINCT r) as relationships
                WHERE size(neighbors) <= $max_nodes
                
                // 计算图指标
                WITH source, neighbors, relationships,
                     size(neighbors) as node_count,
                     size(relationships) as rel_count
                
                RETURN 
                    source,
                    neighbors[0..{graph_query.max_nodes}] as nodes,
                    relationships[0..{graph_query.max_nodes}] as rels,
                    {{
                        node_count: node_count,
                        relationship_count: rel_count,
                        density: CASE WHEN node_count > 1 THEN toFloat(rel_count) / (node_count * (node_count - 1) / 2) ELSE 0.0 END
                    }} as metrics
                """

                result = session.run(
                    cypher_query,
                    {
                        "source_entities": graph_query.source_entities,
                        "max_nodes": graph_query.max_nodes,
                    },
                )

                record = result.single()
                if record:
                    return self._build_knowledge_subgraph(record)

        except Exception as e:
            logger.error(f"子图提取失败: {e}")

        # 降级方案：简单邻居查询
        return self._fallback_subgraph_extraction(graph_query)

    def graph_structure_reasoning(self, subgraph: KnowledgeSubgraph, query: str) -> list[str]:
        """
        基于图结构的推理：这是图RAG的智能之处
        不仅检索信息，还能进行逻辑推理
        """
        reasoning_chains = []

        try:
            # 1. 识别推理模式
            reasoning_patterns = self._identify_reasoning_patterns(subgraph)

            # 2. 构建推理链
            for pattern in reasoning_patterns:
                chain = self._build_reasoning_chain(pattern, subgraph)
                if chain:
                    reasoning_chains.append(chain)

            # 3. 验证推理链的可信度
            validated_chains = self._validate_reasoning_chains(reasoning_chains, query)

            logger.info(f"图结构推理完成，生成 {len(validated_chains)} 条推理链")
            return validated_chains

        except Exception as e:
            logger.error(f"图结构推理失败: {e}")
            return []

    def adaptive_query_planning(
        self, query: str, base_query: GraphQuery | None = None
    ) -> list[GraphQuery]:
        """
        自适应查询规划：根据查询复杂度动态调整策略
        """
        # Reuse the LLM-extracted entities whenever they are available. The old
        # implementation used the entire sentence as an entity, which cannot
        # match the graph's node names.
        base_query = base_query or GraphQuery(
            query_type=QueryType.SUBGRAPH, source_entities=[], max_depth=2
        )
        if not base_query.source_entities:
            return [replace(base_query, query_type=QueryType.SUBGRAPH, max_depth=2, max_nodes=50)]

        # 分析查询复杂度
        complexity_score = self._analyze_query_complexity(query)

        if complexity_score < 0.3:
            return [
                replace(
                    base_query,
                    query_type=QueryType.ENTITY_RELATION,
                    max_depth=1,
                    max_nodes=20,
                )
            ]

        if complexity_score < 0.7:
            return [
                replace(
                    base_query,
                    query_type=QueryType.MULTI_HOP,
                    max_depth=max(2, min(base_query.max_depth, 3)),
                    max_nodes=50,
                )
            ]

        return [
            replace(
                base_query,
                query_type=QueryType.SUBGRAPH,
                max_depth=3,
                max_nodes=100,
            ),
            replace(
                base_query,
                query_type=QueryType.MULTI_HOP,
                max_depth=3,
                max_nodes=50,
            ),
        ]

    def graph_rag_search(self, query: str, top_k: int = 5) -> list[Document]:
        """
        图RAG主搜索接口：整合所有图RAG能力
        """
        logger.info("Starting GraphRAG retrieval")

        if not self.driver:
            logger.warning("Neo4j连接未建立，返回空结果")
            return []

        # The LLM is invoked once to identify graph entities. Planning then
        # creates one or more bounded execution strategies from that result.
        base_query = self.understand_graph_query(query)
        plans = self.adaptive_query_planning(query, base_query)
        logger.warning(
            "GraphRAG adaptive plans count=%s types=%s",
            len(plans),
            [plan.query_type.value for plan in plans],
        )
        results = []

        for index, plan in enumerate(plans):
            try:
                plan_documents = self._execute_graph_query(plan, query)
            except Exception:
                logger.exception("GraphRAG plan %s failed", index)
                continue

            for document in plan_documents:
                document.metadata.update(
                    {
                        "graph_plan_type": plan.query_type.value,
                        "graph_plan_index": index,
                        "graph_plan_depth": plan.max_depth,
                    }
                )
            results.extend(plan_documents)

        results = self._merge_plan_documents(results, query)
        logger.info("GraphRAG completed plans=%s documents=%s", len(plans), len(results))
        return results[:top_k]

    def _execute_graph_query(self, graph_query: GraphQuery, query: str) -> list[Document]:
        """Execute one planned graph query without affecting other plans."""
        if not graph_query.source_entities:
            return []

        if graph_query.query_type == QueryType.CLUSTERING:
            graph_query = replace(graph_query, query_type=QueryType.SUBGRAPH, max_depth=2)

        if graph_query.query_type in {
            QueryType.ENTITY_RELATION,
            QueryType.MULTI_HOP,
            QueryType.PATH_FINDING,
        }:
            return self._paths_to_documents(self.multi_hop_traversal(graph_query), query)

        if graph_query.query_type == QueryType.SUBGRAPH:
            subgraph = self.extract_knowledge_subgraph(graph_query)
            reasoning_chains = self.graph_structure_reasoning(subgraph, query)
            return self._subgraph_to_documents(subgraph, reasoning_chains, query)

        return []

    def _merge_plan_documents(self, documents: list[Document], query: str) -> list[Document]:
        """Deduplicate planned graph results and keep the highest-scoring document."""
        unique_documents: dict[str, Document] = {}
        for document in documents:
            identity = str(document.metadata.get("node_id") or "")
            if not identity:
                identity = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()
            previous = unique_documents.get(identity)
            if previous is None or document.metadata.get(
                "relevance_score", 0.0
            ) > previous.metadata.get("relevance_score", 0.0):
                unique_documents[identity] = document
        return self._rank_by_graph_relevance(list(unique_documents.values()), query)

    # ========== 辅助方法 ==========

    def _parse_neo4j_path(self, record) -> GraphPath | None:
        """解析Neo4j路径记录"""
        try:
            path_nodes = []
            for node in record["path_nodes"]:
                path_nodes.append(
                    {
                        "id": node.get("nodeId", ""),
                        "name": node.get("name", ""),
                        "labels": list(node.labels),
                        "properties": dict(node),
                    }
                )

            relationships = []
            for rel in record["rels"]:
                relationships.append({"type": type(rel).__name__, "properties": dict(rel)})

            return GraphPath(
                nodes=path_nodes,
                relationships=relationships,
                path_length=record["path_len"],
                relevance_score=record["relevance"],
                path_type="multi_hop",
            )

        except Exception as e:
            logger.error(f"路径解析失败: {e}")
            return None

    def _build_knowledge_subgraph(self, record) -> KnowledgeSubgraph:
        """构建知识子图对象"""
        try:
            central_nodes = [dict(record["source"])]
            connected_nodes = [dict(node) for node in record["nodes"]]
            relationships = [dict(rel) for rel in record["rels"]]

            return KnowledgeSubgraph(
                central_nodes=central_nodes,
                connected_nodes=connected_nodes,
                relationships=relationships,
                graph_metrics=record["metrics"],
                reasoning_chains=[],
            )
        except Exception as e:
            logger.error(f"构建知识子图失败: {e}")
            return KnowledgeSubgraph(
                central_nodes=[],
                connected_nodes=[],
                relationships=[],
                graph_metrics={},
                reasoning_chains=[],
            )

    def _paths_to_documents(self, paths: list[GraphPath], query: str) -> list[Document]:
        """将图路径转换为Document对象"""
        documents = []

        for _i, path in enumerate(paths):
            # 构建路径描述
            path_desc = self._build_path_description(path)

            doc = Document(
                page_content=path_desc,
                metadata={
                    "search_type": "graph_path",
                    "path_length": path.path_length,
                    "relevance_score": path.relevance_score,
                    "path_type": path.path_type,
                    "node_count": len(path.nodes),
                    "relationship_count": len(path.relationships),
                    "node_id": path.nodes[0].get("id", "") if path.nodes else "",
                    "recipe_name": path.nodes[0].get("name", "图结构结果")
                    if path.nodes
                    else "图结构结果",
                },
            )
            documents.append(doc)

        return documents

    def _subgraph_to_documents(
        self, subgraph: KnowledgeSubgraph, reasoning_chains: list[str], query: str
    ) -> list[Document]:
        """将知识子图转换为Document对象"""
        documents = []

        # 子图整体描述
        subgraph_desc = self._build_subgraph_description(subgraph)

        doc = Document(
            page_content=subgraph_desc,
            metadata={
                "search_type": "knowledge_subgraph",
                "node_count": len(subgraph.connected_nodes),
                "relationship_count": len(subgraph.relationships),
                "graph_density": subgraph.graph_metrics.get("density", 0.0),
                "reasoning_chains": reasoning_chains,
                "recipe_name": subgraph.central_nodes[0].get("name", "知识子图")
                if subgraph.central_nodes
                else "知识子图",
            },
        )
        documents.append(doc)

        return documents

    def _build_path_description(self, path: GraphPath) -> str:
        """构建路径的自然语言描述"""
        if not path.nodes:
            return "空路径"

        desc_parts = []
        for i, node in enumerate(path.nodes):
            desc_parts.append(node.get("name", f"节点{i}"))
            if i < len(path.relationships):
                rel_type = path.relationships[i].get("type", "相关")
                desc_parts.append(f" --{rel_type}--> ")

        return "".join(desc_parts)

    def _build_subgraph_description(self, subgraph: KnowledgeSubgraph) -> str:
        """构建子图的自然语言描述"""
        central_names = [node.get("name", "未知") for node in subgraph.central_nodes]
        node_count = len(subgraph.connected_nodes)
        rel_count = len(subgraph.relationships)

        return f"关于 {', '.join(central_names)} 的知识网络，包含 {node_count} 个相关概念和 {rel_count} 个关系。"

    def _rank_by_graph_relevance(self, documents: list[Document], query: str) -> list[Document]:
        """基于图结构相关性排序"""
        return sorted(documents, key=lambda x: x.metadata.get("relevance_score", 0.0), reverse=True)

    def _analyze_query_complexity(self, query: str) -> float:
        """分析查询复杂度"""
        complexity_indicators = ["什么", "如何", "为什么", "哪些", "关系", "影响", "原因"]
        score = sum(1 for indicator in complexity_indicators if indicator in query)
        complexity = score / len(complexity_indicators)
        if any(keyword in query for keyword in ("多跳", "路径", "通过", "相连", "搭配")):
            return max(complexity, 0.4)
        return min(complexity, 1.0)

    def _identify_reasoning_patterns(self, subgraph: KnowledgeSubgraph) -> list[str]:
        """识别推理模式"""
        return ["因果关系", "组成关系", "相似关系"]

    def _build_reasoning_chain(self, pattern: str, subgraph: KnowledgeSubgraph) -> str | None:
        """构建推理链"""
        return f"基于{pattern}的推理链"

    def _validate_reasoning_chains(self, chains: list[str], query: str) -> list[str]:
        """验证推理链"""
        return chains[:3]

    def _find_entity_relations(self, graph_query: GraphQuery, session) -> list[GraphPath]:
        """Find direct relationships, optionally constrained by target entities."""
        cypher_query = """
        UNWIND $source_entities AS source_name
        MATCH (source)
        WHERE source.name CONTAINS source_name OR source.nodeId = source_name
        MATCH (source)-[relationship]-(target)
        WHERE size($target_entities) = 0
           OR ANY(target_name IN $target_entities WHERE
                target.name CONTAINS target_name
                OR target_name CONTAINS target.name
                OR coalesce(target.category, '') CONTAINS target_name
           )
        WITH source, target, relationship,
             CASE WHEN type(relationship) IN $relation_types THEN 1.3 ELSE 1.0 END AS relevance
        RETURN [source, target] AS path_nodes,
               [relationship] AS rels,
               1 AS path_len,
               relevance
        ORDER BY relevance DESC
        LIMIT $limit
        """
        records = session.run(
            cypher_query,
            {
                "source_entities": graph_query.source_entities,
                "target_entities": graph_query.target_entities or [],
                "relation_types": graph_query.relation_types or [],
                "limit": graph_query.max_nodes,
            },
        )
        return [path for record in records if (path := self._parse_neo4j_path(record)) is not None]

    def _find_shortest_paths(self, graph_query: GraphQuery, session) -> list[GraphPath]:
        """Find bounded shortest paths between parsed source and target entities."""
        if not graph_query.target_entities:
            return self.multi_hop_traversal(
                replace(
                    graph_query,
                    query_type=QueryType.MULTI_HOP,
                    max_depth=max(2, graph_query.max_depth),
                )
            )

        cypher_query = f"""
        UNWIND $source_entities AS source_name
        UNWIND $target_entities AS target_name
        MATCH (source), (target)
        WHERE (source.name CONTAINS source_name OR source.nodeId = source_name)
          AND (target.name CONTAINS target_name OR target.nodeId = target_name)
          AND source <> target
        MATCH path = shortestPath((source)-[*1..{graph_query.max_depth}]-(target))
        WITH path, source, target, relationships(path) AS rels, nodes(path) AS path_nodes
        RETURN path_nodes, rels, length(path) AS path_len, 1.0 / length(path) AS relevance
        ORDER BY relevance DESC
        LIMIT $limit
        """
        records = session.run(
            cypher_query,
            {
                "source_entities": graph_query.source_entities,
                "target_entities": graph_query.target_entities,
                "limit": graph_query.max_nodes,
            },
        )
        return [path for record in records if (path := self._parse_neo4j_path(record)) is not None]

    def _fallback_subgraph_extraction(self, graph_query: GraphQuery) -> KnowledgeSubgraph:
        """降级子图提取"""
        return KnowledgeSubgraph(
            central_nodes=[],
            connected_nodes=[],
            relationships=[],
            graph_metrics={},
            reasoning_chains=[],
        )

    def close(self):
        """关闭资源连接"""
        if hasattr(self, "driver") and self.driver:
            self.driver.close()
            logger.info("图RAG检索系统已关闭")
