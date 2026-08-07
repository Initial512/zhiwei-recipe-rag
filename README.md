# 知味食谱——智能菜谱推荐助手

知味 AI 是一个面向中文菜谱的智能美食推荐应用，支持菜谱浏览、关键词搜索、自然语言推荐与流式 AI 问答。系统结合本地菜谱知识库、Neo4j 图谱、Milvus 向量检索和 OpenAI 兼容模型，为用户提供可追溯的菜谱与饮食建议。

![知味 AI 首页](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/home.png)

## ✅ 功能特点

- **菜谱浏览**：按荤菜、素菜、汤品、甜品、早餐、主食、水产、调料、饮品和半成品浏览。
- **菜名搜索**：本地菜名匹配不调用大模型，快速跳转至菜谱详情。
- **智能推荐**：根据口味、食材、用餐场景和菜品类型筛选推荐结果。
- **完整菜谱详情**：展示图片、难度、食材分组、制作步骤、烹饪提示与可选时间信息。
- **流式问答**：围绕菜谱或开放式饮食问题逐段返回回答，并展示关联菜谱来源。
- **响应式界面**：适配桌面与移动端；窄屏首页的“灵感”快捷按钮会自动换行显示。

## 🖼️ 界面预览

### 今日推荐

首页会从当前菜谱库中随机挑选菜品；可更换一组，也可直接进入详情。

![知味 AI 今日推荐](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/recommendations.png)

### 分类菜谱

分类页支持按类别浏览与过滤，并展示菜品图片、难度和详情入口。

![知味 AI 甜品分类](https://raw.githubusercontent.com/Initial512/zhiwei-recipe-rag/main/docs/images/dessert-category.png)

## 📊 数据概况

- 322 份 Markdown 菜谱源文件与对应 WebP 图片。
- 10 个实际展示的菜谱分类。
- Neo4j 图谱导入资产位于 `data/graph/cypher/`。
- Milvus 在首次启动时构建菜谱文本向量索引。
- 默认中文嵌入模型：`BAAI/bge-small-zh-v1.5`。
- 对话模型通过环境变量接入 OpenAI 兼容服务。

## 🛠️ 技术栈

### 前端

- React 19、Vite 6、Phosphor Icons
- 原生 CSS 响应式布局
- Nginx 生产静态托管

### 后端与 GraphRAG

- Python 3.11、FastAPI、Uvicorn
- LangChain 与 OpenAI 兼容模型 API
- Neo4j 5：菜谱、分类、食材和步骤关系图谱
- Milvus 2：菜谱语义向量检索
- BM25、图谱检索与向量检索混合召回

## 🧭 运行架构

~~~mermaid
flowchart TD
    browser["浏览器"] --> frontend["React + Nginx<br/>localhost:80"]
    frontend -->|"/api/*"| backend["FastAPI<br/>localhost:7860"]
    frontend -->|"/recipe-images/*"| backend
    backend --> neo4jStore["Neo4j<br/>菜谱知识图谱"]
    backend --> milvusStore["Milvus<br/>语义向量检索"]
    backend --> llmService["OpenAI 兼容 LLM"]
    neo4jStore --> graphAssets["图谱导入资产<br/>data/graph/cypher"]
~~~

Docker Compose 负责启动 Neo4j、Milvus、FastAPI 与 Nginx。Neo4j 数据为空时会导入图谱；Milvus collection 缺失或文档数量变化时会重建向量索引。

当前查询链路为：`GraphDataPreparationModule` → `HybridRetrievalModule` / `GraphRAGRetrieval` → `IntelligentQueryRouter` → `GenerationIntegrationModule`。

## 📁 项目结构

~~~text
.
├── Rag/
│   ├── api.py                         # FastAPI 路由、SSE、菜谱目录索引
│   ├── main.py                        # RecipeRAGSystem 生命周期与模块编排
│   ├── config.py                      # 延迟读取环境变量的运行配置
│   ├── recipe_metadata.py             # 菜名、条件与推荐查询解析
│   ├── requirements.txt               # 后端依赖
│   ├── pytest.ini
│   ├── test_llm_configuration.py      # 模型客户端配置测试
│   ├── test_replacement_rag.py        # 配置、生命周期、索引与解析测试
│   └── rag_modules/
│       ├── graph_data_preparation.py  # 从 Neo4j 加载并构建菜谱文档
│       ├── graph_indexing.py          # 图谱键值索引
│       ├── graph_rag_retrieval.py     # 图谱检索与子图推理
│       ├── hybrid_retrieval.py        # BM25、图谱与向量混合检索
│       ├── intelligent_query_router.py# 查询分析与检索路由
│       ├── milvus_index_construction.py # Milvus collection 与向量索引
│       └── generation_integration.py  # OpenAI 兼容模型回答生成
├── zhiwei-web/
│   ├── src/
│   │   ├── App.jsx                    # 前端页面、路由状态与请求交互
│   │   ├── main.jsx                   # React 入口
│   │   ├── styles.css                 # 页面与移动端响应式样式
│   │   ├── answerFormatting.js        # 流式回答文本格式化
│   │   ├── homeInspiration.js         # 首页灵感问题池
│   │   └── assets/                    # 首页视觉资源
│   ├── package.json
│   ├── vite.config.mjs
│   ├── nginx.conf
│   └── Dockerfile
├── data/
│   ├── dishes/                        # 原始 Markdown 菜谱
│   ├── 图片/                           # 菜品 WebP 图片
│   └── graph/cypher/                  # Neo4j CSV 与导入脚本
├── docker-compose.yml                 # 全部服务编排
├── Dockerfile                         # 后端镜像
├── pyproject.toml                     # Ruff 配置
└── .github/workflows/ci.yml           # CI：密钥扫描、测试、构建
~~~


## 🚀 快速开始

以下 Docker 命令都在项目根目录执行。

### 1. 获取代码与检查环境

~~~powershell
git clone https://github.com/Initial512/zhiwei-recipe-rag.git
cd zhiwei-recipe-rag

docker --version
docker compose version
git --version
~~~

推荐使用 Docker Desktop。若需要运行本地测试或前端开发，还需要 Python 3.11、Node.js 20+ 与 npm。

### 2. 配置模型服务

在 `Rag/.env` 中填写以下变量。

~~~dotenv
LLM_BASE_URL=https://your-openai-compatible-endpoint
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
NEO4J_PASSWORD=replace-with-a-strong-password
MINIO_ACCESS_KEY=replace-with-a-minio-username
MINIO_SECRET_KEY=replace-with-a-minio-password
~~~

| 变量 | 说明 |
| --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容模型服务地址 |
| `LLM_MODEL` | 可用模型名称 |
| `LLM_API_KEY` | 模型服务 API Key |
| `NEO4J_PASSWORD` | Neo4j 密码 |
| `MINIO_ACCESS_KEY` | Milvus 依赖的 MinIO 用户名 |
| `MINIO_SECRET_KEY` | Milvus 依赖的 MinIO 密码 |

`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 缺少任一项时，后端不会启动。

### 3. 启动服务

~~~powershell
docker compose --env-file Rag/.env up --build --detach
docker compose --env-file Rag/.env ps
~~~

服务就绪后访问：

- 应用首页：[http://localhost](http://localhost)
- 健康检查：[http://localhost:7860/api/health](http://localhost:7860/api/health)
- OpenAPI 文档：[http://localhost:7860/docs](http://localhost:7860/docs)

当健康检查返回 `{"status":"ok","ready":true}` 时，检索与生成模块已初始化完成。

### 4. 重启与查看日志

~~~powershell
docker compose --env-file Rag/.env restart backend frontend
docker compose --env-file Rag/.env logs -f backend
docker compose --env-file Rag/.env ps
~~~

首次启动可能需要等待嵌入模型加载、Neo4j 图谱导入和 Milvus 索引创建。

### 5. 前端本地开发（可选）

先确保 Docker 后端可访问，再执行：

~~~powershell
cd zhiwei-web
npm ci
$env:VITE_API_TARGET = "http://127.0.0.1:7860"
npm run dev
~~~

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。生产容器的前端通过同源 `/api` 请求后端，无需设置该变量。

## 💡 使用示例

可在首页输入框、详情页问答框或回答页继续提问：

~~~text
宫保鸡丁怎么做？
推荐几道辣菜
我想喝汤
鸡蛋可以做什么？
推荐适合早餐的菜
~~~

系统会先解析意图：菜名问题优先检索本地菜谱；推荐问题按条件筛选；其余问题使用检索结果辅助大模型回答。

## 🔌 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 后端健康检查 |
| GET | `/api/categories` | 获取分类及菜谱数量 |
| GET | `/api/recipes` | 按分类获取菜谱，支持 `query` |
| GET | `/api/search` | 菜谱检索或条件推荐 |
| GET | `/api/search/recipes` | 本地菜名匹配，不调用 LLM |
| GET | `/api/recipes/{dish_name}` | 获取完整菜谱详情 |
| GET | `/api/recommendations` | 获取随机推荐 |
| POST | `/api/query/classify` | 解析查询意图 |
| POST | `/api/chat/stream` | 流式菜谱问答 |
| POST | `/api/assistant/stream` | 流式饮食助手回答 |
| POST | `/api/recipes/{dish_name}/ingredients/stream` | 流式询问指定菜品食材 |

完整请求参数与响应结构以 [OpenAPI 文档](http://localhost:7860/docs) 为准。

## 🧪 测试与质量检查

### 后端

~~~powershell
..venvScriptspython.exe -m ruff check Rag
..venvScriptspython.exe -m ruff format --check Rag
..venvScriptspython.exe -m pytest Rag -q
~~~

### 前端

~~~powershell
Push-Location zhiwei-web
npm ci
npm run lint
npm test
npm run build
Pop-Location
~~~

### Docker Compose

~~~powershell
docker compose --env-file Rag/.env config --quiet
~~~

CI 会执行密钥扫描、后端测试和格式检查、前端 lint、构建与依赖审计。

## 📚 维护菜谱与图谱

Markdown 菜谱位于 `data/dishes/<分类>/<菜名>.md`，对应图片位于 `data/图片/<菜名>.webp`。菜名应保持一致，否则详情页无法定位图片。

运行时的权威数据源是 `data/graph/cypher/`。新增、删除或修改菜谱时，需要同步更新节点 CSV、关系 CSV 与导入脚本；仅修改 Markdown 不会改变 Neo4j 图谱中的检索数据。

仅在确认可以丢弃本地 Neo4j 与 Milvus 数据后，才执行：

~~~powershell
docker compose down -v
docker compose --env-file Rag/.env up --build --detach
~~~

该操作会删除项目的 Docker 数据卷，并在下次启动时重新导入图谱和创建向量索引。

## ❓ 常见问题

### 后端启动后立刻退出，提示无法连接 Milvus

Milvus 启动后需要短暂时间才能监听端口。等待片刻后重新启动后端与前端：

~~~powershell
docker compose --env-file Rag/.env restart backend frontend
~~~

### 前端页面没有数据或请求失败

先访问健康检查确认后端已就绪；本地 Vite 开发时，确认 `VITE_API_TARGET` 指向可访问的后端地址。

### 移动端看不到首页“灵感”按钮

请强制刷新浏览器缓存。Windows 可使用 `Ctrl + Shift + R`。当前样式会在窄屏自动显示并换行这些按钮。

### 端口被占用

默认仅暴露前端 80 与后端 7860。可用以下命令检查后端端口：

~~~powershell
Get-NetTCPConnection -LocalPort 7860
~~~

如有冲突，请停止占用进程或调整 `docker-compose.yml` 中的端口映射。

## 📄 数据说明

菜谱内容整理自开源菜谱资料。使用或再分发数据前，请确认对应原始资料的许可要求。
