# 知味食谱——智能菜谱推荐助手

知味 AI 是一个面向中文菜谱的智能美食推荐应用，支持菜谱浏览、关键词搜索、自然语言推荐和流式 AI 问答，可根据菜名、口味、菜品类型或食材帮助用户找到合适的菜谱。

项目已由传统的“Markdown 文档 + 本地向量索引”RAG 升级为 GraphRAG：Neo4j 保存菜谱、分类、食材与步骤之间的关系，Milvus 提供语义向量检索，大模型基于图谱与召回结果生成回答。

![知味 AI 首页](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/home.png)

## ✨ 功能特点

- **智能推荐**：用自然语言描述需求，例如“推荐几道清淡的菜”或“我想吃鸡蛋”。
- **菜谱搜索**：支持精确菜名、模糊菜名、食材、口味和菜品类型搜索。
- **分类浏览**：覆盖荤菜、素菜、汤品、甜品、早餐、主食、水产、调料、饮品和半成品。
- **详细步骤**：展示原料、用量、烹饪步骤、难度和实用提示。
- **流式回答**：AI 内容逐步返回，减少长回答的等待时间。
- **GraphRAG 混合检索**：将 Neo4j 图谱候选与 Milvus 语义候选结合，优先使用本地菜谱知识回答问题。
- **响应式界面**：支持桌面端和移动端访问。

## 🖥️ 界面预览

### 今日推荐

首页会从当前菜谱库中随机展示推荐菜品；用户可以换一组结果，或直接进入菜谱详情。

![知味 AI 今日推荐](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/recommendations.png)

### 分类菜谱

菜谱页面支持按分类浏览和搜索，并展示菜品图片、难度和详情入口。详情页可进一步查看食材、步骤与 AI 辅助说明。

![知味 AI 甜品分类](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/dessert-category.png)

## 📊 数据概况

- 322 个 Markdown 菜谱源文件
- 322 张 WebP 菜品图片（800 × 800）
- 10 个实际菜谱分类
- Neo4j 菜谱知识图谱：节点、关系和导入脚本位于 data/graph/cypher/
- Milvus 菜谱语义向量 collection：首次运行时根据图谱数据初始化
- 中文嵌入模型：BAAI/bge-small-zh-v1.5
- 对话模型：通过环境变量接入 OpenAI 兼容服务

## 🛠️ 技术栈

### 前端

- React 19
- Vite 6
- Phosphor Icons
- 原生 CSS 响应式布局

### 后端

- Python 3.11
- FastAPI
- Uvicorn
- LangChain
- OpenAI 兼容模型 API

### GraphRAG

- Neo4j 5：菜谱、分类、食材和制作步骤的关系图谱
- Milvus 2：菜谱语义向量检索
- 图谱候选与语义候选混合召回
- BAAI/bge-small-zh-v1.5 中文嵌入
- 菜名、食材、口味、难度和菜品类型的结构化查询解析

## 🏗️ 运行架构

~~~mermaid
flowchart TD
    browser["浏览器"] --> frontend["React + Vite / Nginx<br/>localhost:80"]
    frontend -->|"/api/*"| backend["FastAPI<br/>localhost:7860"]
    frontend -->|"/recipe-images/*"| backend
    backend --> neo4jStore["Neo4j<br/>菜谱知识图谱"]
    backend --> milvusStore["Milvus<br/>语义向量检索"]
    backend --> llmService["OpenAI 兼容 LLM"]
    neo4jStore --> graphAssets["图谱导入资产<br/>data/graph/cypher"]
~~~

Docker Compose 会启动 Neo4j、Milvus 及其依赖、FastAPI 后端和 Nginx 前端。首次运行时，系统仅在空 Neo4j 数据库中导入图谱，并为初始 Milvus collection 创建向量索引；后续启动复用数据卷。

当前激活链路为 GraphRecipeDataModule（Neo4j）→ GraphHybridRetrieval（Neo4j + Milvus）→ GenerationIntegrationModule（LLM）。`Rag/rag_modules/` 中标为 experimental 的旧模块未接入运行中的 API。

## 📁 项目结构

~~~text
.
├── Rag/
│   ├── api.py                       # FastAPI 接口与 SSE 流式响应
│   ├── main.py                      # RecipeRAGSystem 与图谱/向量编排
│   ├── config.py                    # 后端运行配置
│   ├── rag_modules/
│   │   ├── generation_integration.py    # LLM 回答生成
│   │   ├── graph_rag_retrieval.py       # Neo4j 图谱检索
│   │   ├── milvus_index_construction.py # Milvus 索引与搜索
│   │   ├── intelligent_query_router.py  # 查询解析与路由
│   │   └── recipe_metadata.py           # 菜谱元数据推断
│   ├── requirements.txt
│   └── test_*.py
├── zhiwei-web/
│   ├── src/                          # React 页面、搜索逻辑与样式
│   ├── package.json
│   └── vite.config.mjs
├── data/
│   ├── dishes/                       # 原始 Markdown 菜谱资料
│   ├── graph/cypher/                 # Neo4j 节点、关系和导入脚本
│   └── 图片/                          # 网页使用的菜品 WebP 图片
├── docker-compose.yml                # 完整 GraphRAG 服务编排
├── Dockerfile                        # FastAPI 镜像构建
└── README.md
~~~

## 🚀 获取源码后开始运行

以下 Docker 运行方式均在项目根目录执行。

### 1. 从 GitHub 克隆项目

请先确认电脑已安装 Git：

~~~powershell
git --version
~~~

在准备存放项目的目录中执行：

~~~powershell
git clone https://github.com/Initial512/zhiwei-recipe-rag.git
cd zhiwei-recipe-rag
~~~

执行后续命令前，请确认终端当前位于 zhiwei-recipe-rag 项目根目录。

### 2. 检查源码是否完整

源码中应包含以下目录：

~~~text
data/dishes
data/图片
data/graph/cypher
Rag
zhiwei-web
~~~

Windows PowerShell：

~~~powershell
(Get-ChildItem data\dishes -Recurse -Filter *.md -File).Count
(Get-ChildItem data\图片 -Filter *.webp -File).Count
~~~

当前两个结果均应为 322。若目录不存在或数量为 0，说明源码缺少菜谱或图片数据；如果 data/graph/cypher 缺失，GraphRAG 后端无法初始化图谱。

### 3. 安装运行环境

推荐使用 Docker Desktop，它会启动全部依赖服务。请先安装并启动：

- Docker Desktop（含 Docker Compose）
- Git

确认环境：

~~~powershell
docker --version
docker compose version
git --version
~~~

如需在本机运行测试或前端开发，还需要 Python 3.11、Node.js 20 或更高版本及 npm。

### 4. 配置模型服务

在 Rag/.env 中填写模型服务和 Neo4j 密码。若文件不存在，请新建它：

~~~dotenv
LLM_BASE_URL=https://your-openai-compatible-endpoint
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
NEO4J_PASSWORD=replace-with-a-strong-password
MINIO_ACCESS_KEY=replace-with-a-minio-username
MINIO_SECRET_KEY=replace-with-a-strong-minio-password
~~~

| 变量 | 用途 |
| --- | --- |
| LLM_BASE_URL | 模型服务的 OpenAI 兼容接口地址 |
| LLM_MODEL | 服务商支持的模型名称 |
| LLM_API_KEY | 服务商签发的 API Key |
| NEO4J_PASSWORD | Docker 中 Neo4j 使用的密码 |
| MINIO_ACCESS_KEY | Milvus 依赖的 MinIO 用户名 |
| MINIO_SECRET_KEY | Milvus 依赖的 MinIO 强密码 |

LLM_BASE_URL、LLM_MODEL 和 LLM_API_KEY 缺少任一项，后端均无法启动。不要将真实密钥提交到 Git；项目已忽略 Rag/.env。

### 5. 启动完整服务

~~~powershell
docker compose --env-file Rag/.env up --build
~~~

首次构建会安装依赖并下载嵌入模型，耗时取决于网络速度。服务就绪后访问：

- 应用首页：http://localhost
- 健康检查：http://localhost:7860/api/health
- API 文档：http://localhost:7860/docs

健康检查返回 ready: true 表示检索与生成模块已完成初始化。

### 6. 前端本地开发（可选）

先通过 Docker Compose 启动 Neo4j、Milvus 和后端，保持后端可访问；再打开另一个终端：

~~~powershell
cd zhiwei-web
npm ci
$env:VITE_API_TARGET = "http://127.0.0.1:7860"
npm run dev
~~~

浏览器访问 http://127.0.0.1:5173。若后端部署在其他地址，可改用 VITE_API_BASE_URL 指定完整后端地址。

## 💬 使用示例

可以在搜索框或 AI 对话区域输入：

~~~text
宫保鸡丁怎么做
推荐几道辣菜
我想喝汤
鸡蛋可以做什么
推荐适合早餐的菜
~~~

系统会先解析查询意图，再执行精确菜谱查找、图谱与语义混合检索、条件推荐或饮食助手回答。

## 🔌 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | /api/health | 后端健康检查 |
| GET | /api/categories | 获取菜谱分类 |
| GET | /api/recipes | 按分类获取菜谱列表 |
| GET | /api/search | 搜索菜谱或推荐结果 |
| GET | /api/search/recipes | 仅按本地菜名搜索，不调用 LLM |
| GET | /api/recipes/{dish_name} | 获取菜谱详情 |
| GET | /api/recommendations | 获取随机推荐结果 |
| POST | /api/query/classify | 判断查询类型 |
| POST | /api/chat/stream | 流式菜谱问答 |
| POST | /api/assistant/stream | 流式饮食助手回答 |
| POST | /api/recipes/{dish_name}/ingredients/stream | 流式询问指定菜品食材 |

完整请求参数和响应结构以 http://localhost:7860/docs 为准。

## 🧪 测试

后端测试：

~~~powershell
.\.venv\Scripts\python.exe -m pytest Rag -q
~~~

前端检查与生产构建：

~~~powershell
Push-Location zhiwei-web
npm ci
npm run lint
npm run build
Pop-Location
~~~

Docker Compose 配置检查：

~~~powershell
docker compose --env-file Rag/.env config --quiet
~~~

当前测试覆盖查询解析、推荐过滤、API 响应、SSE 流式数据和前端搜索路由等核心行为。

## 📝 修改菜谱

data/dishes/<分类>/<菜名>.md 保留原始菜谱资料，网页图片位于 data/图片/<菜名>.webp。Markdown 文件名和 WebP 文件名应保持一致，否则网页无法找到对应图片。

当前运行时的权威数据源是 data/graph/cypher/ 中的 Neo4j 图谱导入资产。因此新增、删除或修改菜谱时，需要同步更新节点 CSV、关系 CSV 和导入脚本，而不只是修改 Markdown 文件。

在开发环境中，要让修改后的图谱重新导入，可先停止服务并删除本项目的 Docker 数据卷，然后重新执行启动命令。该操作会清除本地 Neo4j 与 Milvus 数据，请勿用于需要保留数据的环境：

~~~powershell
docker compose down -v
docker compose --env-file Rag/.env up --build
~~~

## ❓ 常见问题

### 后端提示缺少模型环境变量

确认文件位置为 Rag/.env，且同时存在有效的：

~~~dotenv
LLM_BASE_URL=...
LLM_MODEL=...
LLM_API_KEY=...
~~~

本项目通过 ChatOpenAI 调用模型，因此所选服务必须提供 OpenAI 兼容接口。

### 首次启动时间较长

首次构建会下载 BAAI/bge-small-zh-v1.5，首次服务启动还会导入 Neo4j 图谱并创建 Milvus collection。请确认 Docker 有网络访问权限，并等待各服务通过健康检查。

### 前端打开后没有数据

先确认后端健康检查可访问：http://localhost:7860/api/health。若使用 Vite 开发服务器，再确认 VITE_API_TARGET 指向可访问的后端地址。

### 端口被占用

默认仅暴露前端 80 和后端 7860；Neo4j、Milvus 与 MinIO 只在 Docker Compose 内部网络中可访问。例如查看后端端口：

~~~powershell
Get-NetTCPConnection -LocalPort 7860
~~~

结束冲突进程，或在 docker-compose.yml 中调整端口映射后重新启动。

### Neo4j 或 Milvus 需要重新初始化

仅在确认可丢弃本地数据时执行：

~~~powershell
docker compose down -v
docker compose --env-file Rag/.env up --build
~~~

此操作会删除本项目的 Docker 数据卷，并在下一次启动时重新导入图谱与创建向量索引。

## 📄 数据说明

菜谱内容整理自开源菜谱资料，原始 Markdown 结构被保留以便维护和溯源；运行时使用 Neo4j 图谱与 Milvus 向量数据提供 GraphRAG 检索。使用或再分发数据前，请同时确认原始菜谱数据对应的许可要求。
