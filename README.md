# 知味 AI

知味是一个保持现有 React 前端不变的菜谱 GraphRAG 应用。后端使用 FastAPI，菜谱图谱存储在 Neo4j，语义向量存储在 Milvus；菜品图片始终从本项目的 `data/图片` 提供。

## 数据与架构

- `data/graph/cypher/`：已迁入的图谱资产（`nodes.csv`、`relationships.csv`、`neo4j_import.cypher`）。首次启动时只会向空 Neo4j 导入一次。
- `data/dishes/`：历史 Markdown 菜谱，不参与 GraphRAG 运行时构建。
- `data/图片/`：前端展示使用的本地 WebP 图片。
- `Rag/`：FastAPI、Neo4j 图检索、Milvus 语义检索和 LLM 回答生成。

前端继续调用原有 `/api/*` 和 `/recipe-images/*` 接口，无需改动。

## 启动

1. 复制 `.env.example` 的变量到 `Rag/.env`，填写 LLM 配置并设置强密码 `NEO4J_PASSWORD`。
2. 在项目根目录运行：

```powershell
docker compose --env-file Rag/.env up --build
```

首次启动会创建 Neo4j、Milvus 和向量 collection；之后会复用 Docker 数据卷，不会重新导入图谱。浏览器访问 `http://localhost`。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pytest Rag -q
docker compose --env-file Rag/.env config --quiet
```
