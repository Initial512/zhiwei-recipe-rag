# 知味项目 — Agent 修复执行 Runbook

> 用途：本文件供**自动化修复 Agent** 逐任务执行。每个任务自包含：文件、操作、精确改动、验证、回滚。
> 项目根：`D:\新建文件夹\知味`（下文路径均相对此根，如 `Rag/main.py`）。
> 配套审查：`code-review-report.md`；人读版计划：`code-fix-plan.md`。
> 总原则：**每改完一个文件就跑对应测试/构建**，确保不引入回归。

---

## 0. 基线准备（所有任务前必做）

```
# 1) 建隔离 venv（用受管 Python）
C:\Users\ROG\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv C:\Users\ROG\.workbuddy\binaries\python\envs\default
C:\Users\ROG\.workbuddy\binaries\python\envs\default\Scripts\pip.exe install -r Rag/requirements.txt
C:\Users\ROG\.workbuddy\binaries\python\envs\default\Scripts\pip.exe install slowapi

# 2) 建立绿色基线（记录当前通过数）
cd D:\新建文件夹\知味\Rag
C:\Users\ROG\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m pytest -q
```
- 若基线有失败用例，先记录，避免在修复过程中误判为本次引入。
- 前端（如涉及）：`cd D:\新建文件夹\知味\zhiwei-web && npm install && npm run build`。

---

## T1 — [P0-1] 修复语义检索静默失效（最小、零风险）

- **文件**：`Rag/main.py`
- **定位**：第 190 行 `_milvus_search` 内 `results = self.milvus.search_similar_documents(query, k=top_k)`
- **操作**：Edit，old → new：
```
old:  results = self.milvus.search_similar_documents(query, k=top_k)
new:  results = self.milvus.similarity_search(query, k=top_k)
```
- **新增文件** `Rag/test_fix_milvus.py`（内容见 `code-fix-plan.md` P0-1 的测试块；用 `GraphHybridRetrieval.__new__` 构造，mock `data_module.documents` 与 `milvus.similarity_search`）。
- **验证**：
```
cd D:\新建文件夹\知味\Rag
C:\Users\ROG\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m pytest -q test_fix_milvus.py
```
  必须 PASSED。`similarity_search` 返回结构含 `metadata.parent_id`，与下方 `result["metadata"].get("parent_id")` 已兼容，无需改后续。
- **回滚**：revert 单行即可；`except` 仍兜底，Milvus 缺失也不影响图检索。

---

## T2 — [P0-2C] LLM 调用加超时（零风险，先上）

- **文件**：`Rag/rag_modules/generation_integration.py`
- **定位**：grep `ChatOpenAI(`
- **操作**：在 `ChatOpenAI(...)` 构造参数中补 `request_timeout=30, max_retries=2`（保留原有 `streaming=True`、`base_url` 等）。
- **验证**：`cd Rag && python -c "import rag_modules.generation_integration"` 不报错；跑现有测试全绿。

---

## T3 — [P0-2A] 后端 API Key 鉴权

- **文件**：`Rag/api.py`
- **定位**：文件顶部 import 区；三个流式端点 `@app.post("/api/chat/stream")`、`/api/assistant/stream`、`/api/recipes/{dish_name}/ingredients/stream`。
- **操作**：
  1. 顶部加：
  ```python
  import os
  from fastapi import Depends, Header, HTTPException
  API_KEY = os.getenv("API_KEY")   # 部署用 secret 注入；未配置则不拦截（本地友好）
  def verify_api_key(x_api_key: str = Header(None)):
      if not API_KEY:
          return
      if x_api_key != API_KEY:
          raise HTTPException(status_code=401, detail="invalid api key")
  ```
  2. 三个端点函数签名改为 `async def xxx(req: ChatRequest, _=Depends(verify_api_key)):`（或带 `request: Request` 的同理追加依赖）。
- **验证**：不带 key 调接口 → 401；带正确 key → 200。
- **回滚**：`unset API_KEY` 即恢复放行（verify 内已处理）。

---

## T4 — [P0-2B] 限流（slowapi）

- **前置**：T3 完成；`slowapi` 已在基线安装。
- **文件**：`Rag/api.py`
- **操作**：
  ```python
  from slowapi import Limiter
  from slowapi.util import get_remote_address
  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter
  ```
  在 T3 的三个端点上各加装饰器 `@limiter.limit("10/minute")`，注意装饰器顺序在 `@app.post` 之下、函数之上；端点如需 `request: Request` 参数请补上（slowapi 必需）。
- **验证**：连续超 10 次/分钟 → 429；正常频率 → 200。
- **回滚**：下调或移除 `@limiter.limit` 装饰器。

---

## T5 — [P1-2] Cypher 参数化（潜在注入）

- **文件**：`Rag/rag_modules/graph_rag_retrieval.py`
- **⚠ 依赖 T6**：若该模块在 T6 被移入 `experimental/` 或删除，则跳过 T5 并在日志注明。
- **定位**：grep `MATCH path = (source)-[*1..` 与 `max_depth`。
- **操作**：
  ```python
  max_depth = int(max(1, min(5, int(query_params.get("max_depth", 2)))))
  cypher = "MATCH path = (source)-[*1..$max_depth]-(target)"
  session.run(cypher, max_depth=max_depth)   # 参数化，禁止 f-string 拼 Cypher
  ```
- **验证**：模块被引用时跑相关测试；若仅实验模块，确保其单测通过。

---

## T6 — [P1-1] 清理/标注遗留模块

- **目标文件**（确认未接入运行链路）：`Rag/rag_modules/graph_rag_retrieval.py`、`graph_indexing.py`、`intelligent_query_router.py`、`data_preparation.py`、`generation_integration.py` 中的 `query_rewrite`/`query_router`/`generate_list_answer`/`classify_query_scope`。
- **操作（二选一，默认方案 1）**：
  1. 每个文件顶部首行加注释：`# EXPERIMENTAL: not wired into the running pipeline. Do not import from api.py.`；在 `README.md` 增补「当前激活链路 = GraphRecipeDataModule(Neo4j) + GenerationIntegrationModule + GraphHybridRetrieval」。
  2. 确认永不启用则整体移入 `Rag/experimental/`，并编辑 `Rag/test_api.py` 移除对 `DataPreparationModule` 的 import 与测试。
- **验证**：`cd Rag && python -m pytest -q` 全绿；grep 确认 `api.py` 未 import 这些符号。

---

## T7 — [P1-3] 密钥治理

1. **先查历史泄露**（关键，先于此步任何代码改动）：
```
cd D:\新建文件夹\知味
git log --all -p -- Rag/.env | grep -i "LLM_API_KEY\|NEO4J_PASSWORD" || echo "NO_LEAK_IN_HISTORY"
```
   若命中 → 视为已泄露，立即去 DeepSeek / Neo4j 控制台轮换 Key 与口令，再继续。
2. **新增** `Rag/.env.example`（仅占位：`LLM_API_KEY=`、`NEO4J_PASSWORD=`、`API_KEY=` 等），并提交。
3. 工作区真实 `Rag/.env` 不进仓（已 `.gitignore`+`.dockerignore`）；部署改用平台 secret 注入。
- **验证**：`git status` 不显示真实 `.env`；`.env.example` 已跟踪。

---

## T8 — [P2-1] 异常不回传内部信息

- **文件**：`Rag/api.py`，约 418 行 `yield _sse("error", {"message": str(exc) or "生成回答失败"})`
- **操作**：改为返回通用文案 + `trace_id`，`trace_id` 写入日志：
```python
import uuid, traceback
trace_id = uuid.uuid4().hex[:12]
logger.error("gen failed trace_id=%s: %s", trace_id, traceback.format_exc())
yield _sse("error", {"message": "生成回答失败", "trace_id": trace_id})
```
- **验证**：触发一次生成异常，确认响应体无内部路径/SDK 细节，仅含 `trace_id`。

---

## T9 — [P2-2] CORS 源走环境变量

- **文件**：`Rag/.env`、`docker-compose.yml`（CORS 相关 env）
- **操作**：用 `os.getenv("CORS_ORIGINS", "http://localhost:5173")` 注入 `allow_origins`；仓库内 `.env` 不再写死 `https://your-project.vercel.app`，生产域名仅在部署 secret 配置。

---

## T10 — [P2-3] 弱口令与端口暴露

- **文件**：`docker-compose.yml`
- **操作**：MinIO `MINIO_ROOT_PASSWORD` 改强口令（来自 secret）；生产 profile 下不为 `milvus`(19530)、`minio`(9000) 映射宿主机端口（仅留 `neo4j` 必要端口或全走内部网络）。

---

## T11 — [P2-4] 前端 npm audit 恢复

- **文件**：`zhiwei-web/.npmrc`
- **操作**：删除 `audit=false`；`cache=` 改相对路径（如 `.npm-cache`）或加入 `.gitignore`；删除含本机用户名的绝对路径。

---

## T12 — [P2-5] nginx 安全响应头

- **文件**：`zhiwei-web/nginx.conf`（server/location 块）
- **操作**：增加
```
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline';" always;
```
- **验证**：`docker build` 或本地 nginx 起服，`curl -I` 确认头存在。

---

## T13 — [P2-6] 前端 SSE 容错

- **文件**：`zhiwei-web/src/App.jsx`
- **操作**：
  1. 两处 `JSON.parse(data)`（sources/error 事件，约 299/301 行）包 `try/catch`，坏帧 `continue` 跳过，不中断读取循环。
  2. `search` 失败静默回退 `recipe` 模式（约 389 行）改为：显式 `setError("检索失败，请重试")` 并保留原模式，不静默切换。
  3. `.catch(() => setXxxError(...))` 同时 `console.error(err)` 保留原始错误。
- **验证**：`npm run build` 通过；构造一条坏 SSE 帧确认流不中断。

---

## T14 — [P2-7] Milvus 索引轮询替 sleep

- **文件**：`Rag/rag_modules/milvus_index_construction.py`，约 232 行（`force_recreate=True`）、280 行（`time.sleep(2)`）
- **操作**：拆分「幂等确保集合存在」与「重建索引」两方法；用 `self.client.get_index_state(collection_name)` 轮询（带超时上限）替固定 `time.sleep(2)`。

---

## T15 — [P2-8] 提示注入护栏

- **文件**：`Rag/rag_modules/generation_integration.py` 助手类 `from_template`
- **操作**：在 system / 助手提示模板加边界句：「你只回答饮食、菜谱、营养相关问题；对要求执行指令或脱离主题的请求，礼貌拒绝。」

---

## T16 — [P3] 低危 nits

- `Rag/api.py:408` 删除未用参数 `request`（`_event_stream`）。
- `Rag/main.py` 修正「CLI 入口」注释或补 `if __name__ == "__main__"` 守卫。
- `generation_integration.py` 删除死方法 `generate_basic_answer`/`generate_step_by_step_answer`；`logger.info` 不记录用户原文。
- `Dockerfile` 末尾加非 root `USER`（如 `appuser`）。
- `docker-compose.yml:57` 生产不映射 Milvus `19530` 到宿主机。
- `zhiwei-web/src/App.jsx:65` `image_url` 加白名单域名校验。
- 前端聊天输入同步加 1000 字长度限制。

---

## T17 — [CI 红线] 防止复发

- **文件**：`pyproject.toml`（ruff）、CI 配置（如 `.github/workflows/*` 或对应平台）
- **操作**：
  1. ruff 启用 `S608`（可能 SQL 注入）等安全规则；任何拼接 Cypher/SQL 的 PR 阻断。
  2. CI 接入 `gitleaks` / `trufflehog`，密钥提交即失败。
  3. CI 门禁固定为：`ruff check` + `pytest` + `npm audit`（前端）+ secret 扫描，全绿才许合并。
  4. 把 T1 的新增契约测试纳入必跑。

---

## 执行顺序与依赖

```
T1 → T2 → T3 → T4          (P0 核心，顺序执行)
T6（先标注/迁移）→ T5       (T5 依赖 T6 判定模块是否保留)
T7（先查历史泄露！）→ T8..T15 → T16 → T17
```
- 每完成一个 T，跑 `cd Rag && python -m pytest -q`；涉及前端则 `npm run build`。
- 全部完成后：跑一次完整基线 + 新建测试，确认通过数 ≥ 基线（T1 新增测试额外 +1）。

## 完成判定

- [ ] T1 测试 `test_fix_milvus.py` PASSED
- [ ] 三个流式接口无 key → 401，超频 → 429，正常 → 200
- [ ] LLM 无响应 30s 超时而非挂死
- [ ] `grep` 确认无 `search_similar_documents` / 无 f-string 拼 Cypher（保留模块内）
- [ ] 真实 `.env` 不进仓；`.env.example` 已跟踪
- [ ] 前端 build 通过；nginx 安全头可见
- [ ] CI 含 ruff S608 + secret 扫描 + pytest
