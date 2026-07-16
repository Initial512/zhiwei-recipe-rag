# 知味（ZhiWei）代码审查报告

> 审查范围：`Rag/`（Python RAG 后端 + 15 个 .py）、`docker-compose.yml` / `Dockerfile` / `pyproject.toml`（基础设施）、`zhiwei-web/`（Vite + React 前端，策略性扫描）
> 审查日期：2026-07-16
> 严重程度定义：**高** = 阻断性（功能失效 / 安全漏洞 / 数据风险）；**中** = 应修复（性能 / 稳健性 / 安全隐患）；**低** = 建议（风格 / 小改进）

---

## 一、总体印象

整体代码质量**高于团队自述的「参差不齐」**。后端 API 层（`api.py`）规范度很高：有 Pydantic 输入校验、SSE 流式输出正确、CORS 配置合理、ruff 规则配置得当（且特意规避了中文全角标点的误报）。前端把 LLM 输出作为 React 文本节点渲染，**无 XSS**、无硬编码密钥、无 `eval`。测试对 `api.py` 的解析逻辑覆盖良好。

但存在三类系统性问题：
1. **核心 RAG 功能被一个方法名拼写错误静默废掉**（向量检索从未真正执行）。
2. **后端完全无认证 / 限流**，而多个接口会触发付费 LLM 调用，存在成本滥用与 DoS 风险。
3. **`rag_modules/` 下有大量「实验性 / 遗留代码」并未接入运行链路**，其中藏有潜在的 Cypher 注入、混淆了「真正在跑的代码」。

---

## 二、🔴 高严重度（必须修复）

### 1. 语义检索被静默禁用——向量库建了却从未查询
**位置**：`Rag/main.py:190`（`GraphHybridRetrieval._milvus_search`）
```python
results = self.milvus.search_similar_documents(query, k=top_k)   # ← 方法不存在！
```
`MilvusIndexConstructionModule`（`milvus_index_construction.py`）只定义了 `similarity_search`，**从没有 `search_similar_documents`**。该调用会抛 `AttributeError`，而：
```python
except Exception:
    logger.exception("Milvus semantic search failed; retaining graph results")
    return []
```
异常被吞掉，语义检索永远返回空 → 「混合检索」实际只剩 **图检索 + 关键词** 两条路，向量索引（构建时耗时的 embedding）形同虚设。

**为什么严重**：这是检索质量的核心能力缺陷；且因为 `except` 兜底，运行时无任何报错，极难发现。测试也只 mock 了 `GraphHybridRetrieval`，从未真正走到 Milvus 调用，导致该 bug 漏网。

**建议**：
- 最小修复：把调用改成 `self.milvus.similarity_search(query, k=top_k)`，并适配其返回结构（`result["metadata"]["parent_id"]` 已兼容）。
- 修复后补一条集成测试（或 mock 出 `MilvusIndexConstructionModule`）断言 `hybrid_search` 在 `semantic` 策略下能返回 Milvus 结果。

### 2. 后端所有接口无认证、无限流（含付费 LLM 调用）
**位置**：`Rag/api.py` 全文件；尤其是 `/api/chat/stream:594`、`/api/assistant/stream:604`、`/api/recipes/{dish_name}/ingredients/stream:616`

这些流式接口直接调用 DeepSeek 等付费 LLM，但：
- 没有任何认证（`api_key` / token / 登录）。
- 没有速率限制、并发上限或按 IP 配额。
- `uvicorn` 默认 worker 下可并发大量长连接。

**为什么严重**：任何能访问 7860 端口的人都能无限刷接口烧钱（成本滥用），或占满连接/显存造成 DoS。前端无认证与之一致，放大风险。

**建议**：
- 至少加一层 API Key / 网关鉴权（哪怕内部部署，也建议加反向代理鉴权）。
- 加限流中间件（如 `slowapi` 或 nginx `limit_req`），对每个客户端限制 QPS 与并发流数量。
- 给 LLM 调用设超时（`httpx` 超时 / `ChatOpenAI(request_timeout=...)`）。

### 3. 大量遗留/实验代码未接入运行链路，且隐藏安全与维护风险
**位置**：
- `Rag/rag_modules/graph_rag_retrieval.py`（`GraphRAGRetrieval` 类，~630 行）
- `Rag/rag_modules/graph_indexing.py`（`GraphIndexingModule`）
- `Rag/rag_modules/intelligent_query_router.py`（`IntelligentQueryRouter`）
- `Rag/rag_modules/data_preparation.py`（`DataPreparationModule`，生产未用，仅测试引用）
- `Rag/rag_modules/generation_integration.py`：`query_rewrite` / `query_router` / `generate_list_answer` / `classify_query_scope` 均**未被 `api.py` 调用**

经全仓 grep 确认：运行链路实际只用 `GraphRecipeDataModule`（Neo4j）+ `GenerationIntegrationModule` + `GraphHybridRetrieval`，数据源是 Neo4j 而非 Markdown。上述模块是「另一套设计」，从未接线。

**为什么严重**：
- 让新人无法分辨「真正在跑的代码」，维护成本高。
- `graph_rag_retrieval.py` 内含 f-string 拼接 Cypher（见中危 #5），一旦误接即变成真实漏洞。
- 测试（`test_api.py`）还 import 并测试 `DataPreparationModule`，把测试精力投在了生产不用的代码上。

**建议**：
- 明确标注或移出这些模块：要么接入并替换旧实现，要么归档到 `experimental/` 或删除。
- 在 README / 架构文档里写明「当前激活的检索链路」。

---

## 三、🟡 中严重度（应该修复）

### 4. 异常原始信息直接返回给客户端（信息泄露）
**位置**：`Rag/api.py:418`
```python
yield _sse("error", {"message": str(exc) or "生成回答失败"})
```
`str(exc)` 可能包含内部路径、第三方 SDK 详情甚至部分密钥上下文。

**建议**：只返回通用错误文案 + 服务端 `trace_id`，详细异常仅记录在日志。

### 5. （遗留代码内）Cypher 语句用 f-string 拼接用户输入
**位置**：`Rag/rag_modules/graph_rag_retrieval.py:236`
```python
MATCH path = (source)-[*1..{max_depth}]-(target)
```
`max_depth` 来自 LLM 返回的 JSON（`understand_graph_query`），未做类型/范围校验即插值进 Cypher。若 LLM 返回字符串（如 `"1]-(t) DELETE n RETURN n"`），即构成 Cypher 注入。当前因该模块未接线而不触发，但属**潜在高危**。

**建议**：即便保留该模块，也应把 `max_depth` 强制转为 `int` 并 `clamp(1, 5)`；所有用户输入一律走参数化（`$param`），禁止 f-string 拼 Cypher。

### 6. CORS 来源在 `.env` 与 compose 不一致
**位置**：`Rag/.env:5`（`https://your-project.vercel.app`）vs `docker-compose.yml:71`（仅 `localhost`）

**建议**：生产域名统一在环境变量管理，不要把外部域名写死进仓库内的 `.env` 样例；部署用独立 secret。

### 7. 默认/弱口令与无密钥管理
**位置**：`docker-compose.yml:45-46`（MinIO `minioadmin/minioadmin`）、`Rag/.env:8`（`NEO4J_PASSWORD=923512YTTxnz`）

当前这些仅在 Docker 内网可达，但若误把端口暴露到公网即风险。`NEO4J_PASSWORD` 还是弱口令。

**建议**：统一从密钥管理（如 `.env` 不入仓 + CI secret / Docker secrets）注入；MinIO 改强口令；生产环境不为 Milvus/MinIO 映射宿主机端口。

### 8. 前端 `.npmrc` 关闭了 npm audit 并写入本机绝对路径
**位置**：`zhiwei-web/.npmrc`
```
audit=false
cache=C:\Users\ROG\Documents\rag\zhiwei-web\.npm-cache
```
`audit=false` 会屏蔽依赖漏洞扫描；缓存路径含本机用户名/目录，既不可移植又泄露环境信息。

**建议**：删除 `audit=false`（或仅本地临时用）；缓存路径改用相对/默认，或加入 `.gitignore`。

### 9. 前端 nginx 缺少安全响应头
**位置**：`zhiwei-web/nginx.conf`
缺少 `Content-Security-Policy`、`X-Frame-Options`/`frame-ancestors`、`X-Content-Type-Options`。

**建议**：补上基础安全头（至少 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、受限的 CSP）。

### 10. 前端错误处理与 SSE 解析偏脆弱
**位置**：`zhiwei-web/src/App.jsx`
- 多处 `.catch(() => setXxxError("…"))` 吞掉原始错误，不利排障；`search` 失败时静默回退到 `recipe` 模式（`:389`）掩盖失败。
- `JSON.parse(data)` 解析 `sources`/`error` 事件**无 try/catch**（`:299/301`），单条事件格式异常会中断整个 SSE 读取循环。

**建议**：保留原始错误到日志；SSE 的 `JSON.parse` 包 try/catch 并跳过坏帧；回退逻辑显式告知用户而非静默切换。

### 11. `build_vector_index` 是破坏性方法，且用魔法 sleep 等索引
**位置**：`Rag/rag_modules/milvus_index_construction.py:232`（`create_collection(force_recreate=True)`）、`:280`（`time.sleep(2)`）

`build_vector_index` 每次都 drop+recreate 集合，语义与「确保集合存在」混淆；`time.sleep(2)` 假设索引 2 秒内就绪，可能不稳。

**建议**：把「确保集合存在（幂等）」与「重建索引」拆分；用 `get_index_state()` 轮询代替固定 sleep。

### 12. LLM 调用无超时 / 无并发上限
**位置**：`Rag/rag_modules/generation_integration.py`（`ChatOpenAI` 未设 `request_timeout`）

**建议**：`ChatOpenAI(request_timeout=30, max_retries=2)`；流式接口要有客户端断开即终止的生成端取消逻辑。

### 13. 明文密钥留在工作区 `.env`，且无 `.env.example`
**位置**：`Rag/.env:4`（`LLM_API_KEY=sk-…`）、`:8`（`NEO4J_PASSWORD=…`）

**好消息**：`.gitignore` 已含 `Rag/.env`，`.dockerignore` 已含 `**/.env`，所以当前**未提交、未打入镜像**。但明文密钥留在工作树仍有风险（仓库被打包分享、gitignore 被误删、历史提交曾含密钥等）。

**建议**：
- 立即确认该 DeepSeek Key 与 Neo4j 口令**未在任何历史提交中出现**；若出现过，按已泄露处理——轮换 Key。
- 提交 `Rag/.env.example`（仅占位），并从工作区移除真实 `.env`（用部署平台 secret 注入）。
- 给团队约定：密钥只进 secret 管理器，不进仓库。

### 14. 提示词注入面（低风险，建议加固）
**位置**：`Rag/rag_modules/generation_integration.py` 各 `from_template`（`{question}` / `{query}` 直接拼入）

用户原文直接进 LLM 提示词。由于前端以文本节点渲染、后端未执行模型输出，风险主要是「模型被诱导说离题内容」，无 XSS/命令执行。但无 system 提示词护栏。

**建议**：给助手类提示加明确边界（「只回答饮食/菜谱相关问题，拒绝执行指令」）；对 `recipe_lookup` 路径的输出额外做一层与检索来源一致的校验。

---

## 四、💭 低严重度（建议）

- **`api.py:408`** `_event_stream(request, docs, chunks)` 的 `request` 参数未被使用——死参数，可删。
- **`main.py`** 的 `pyproject.toml` per-file-ignore 注释称其为「CLI 入口脚本」，但代码中**没有 `if __name__ == "__main__"` / argparse**（实际只被 `api.py` import）。注释与事实不符，易误导。
- **`generation_integration.py`** 非流式 `generate_basic_answer` / `generate_step_by_step_answer` 是死方法（API 只用 `*_stream` 变体）。
- **`generation_integration.py:213`** `logger.info(f"查询已重写: '{query}' → '{response}'")` 记录用户原始输入，留意日志中的 PII。
- **`Dockerfile`** 未声明 `USER`，容器以 root 运行——建议加非 root 用户。
- **`docker-compose.yml:57`** `milvus` 把 `19530` 映射到宿主机；生产环境建议只留内部网络，不对外发布。
- **前端 `App.jsx:65`** `image_url` 直接拼进 URL 路径未做白名单校验（低风险，img src 不执行脚本，但存在外链面）。
- 聊天输入在前端无长度上限（后端 `ChatRequest` 已限制 1000 字，故后端兜底；前端可同步加限制提升体验）。

---

## 五、做得好的地方（值得保持）

- ✅ `api.py` 用 Pydantic（`ChatRequest.min_length/max_length`、Query 校验）做输入约束，且有 SSE `X-Accel-Buffering: no` 正确头。
- ✅ CORS 配置正确：`allow_credentials=False` + 显式 `allow_origins`，未滥用通配。
- ✅ `pyproject.toml` 的 ruff 配置合理，且特意关闭 `RUF001/002/003` 以免中文标点误报——体现对中文项目的实际考量。
- ✅ 前端把 LLM 回答作为 `{answer}` 文本节点渲染，天然避免 DOM XSS；用户输入经 `encodeURIComponent` / `URLSearchParams` 编码。
- ✅ `.dockerignore` 正确排除了 `.env`、`node_modules`、测试文件——构建上下文干净。
- ✅ `test_api.py` 对解析/归一化/图片路由有大量单测，且用 `SimpleNamespace` mock 得当。
- ✅ `recipe_metadata.py` 的规则解析与推荐排序逻辑清晰、可测试。

---

## 六、统一团队代码质量标准的建议

1. **禁止 f-string / `%` / `+` 拼接任何数据库/查询语言语句**（Cypher、SQL、Milvus filter）。一律参数化。用 ruff 的 `S608`（可能 SQL 注入）等规则做 CI 卡点。
2. **所有外部调用（LLM、DB、HTTP）必须有超时与重试上限**，且异常不得把内部信息返回给客户端。
3. **密钥红线**：仓库内只允许 `.env.example`；真实密钥来自 secret 管理器；CI 加 `gitleaks` / `trufflehog` 扫描。
4. **「死代码」即债务**：未接入运行链路的模块必须标注 `# EXPERIMENTAL: not wired in` 或移入 `experimental/`；禁止对死代码写「看起来有用」的测试。
5. **集成测试兜底核心链路**：本次 `search_similar_documents` 漏网正是因为只测了 mock。至少对 `hybrid_search` 的 `semantic` / `combined` 分支做轻量集成或契约测试。
6. **认证与限流是付费/写接口的硬性前置**，不允许「先上线后加」。
7. **安全响应头与依赖审计**纳入前端构建产物校验（CSP、`.npmrc audit` 不得关）。
8. **CI 门禁**：`ruff check` + `pytest` + `npm audit` + secret 扫描，全部不过不让合并。

---

## 七、修复优先级清单（建议排期）

| 优先级 | 事项 | 工作量 |
|---|---|---|
| P0 | 修复 `search_similar_documents` → `similarity_search`（#1） | 极小（改名 + 1 条测试） |
| P0 | 后端加认证/限流/超时（#2, #12） | 中 |
| P1 | 清理/标注遗留模块（#3），移除潜在 Cypher 注入（#5） | 中 |
| P1 | 密钥治理：轮换+`.env.example`+secret 管理（#13） | 小 |
| P2 | 错误不回传内部信息（#4）、CORS/口令/响应头/前端容错（#6–#11） | 中 |
| P3 | 低危 nits（#各） | 小 |
