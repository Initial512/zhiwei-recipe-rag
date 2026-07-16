# 知味（ZhiWei）代码审查标准与流程

> 配套文档：`code-review-report.md`（一次性审查发现）、`code-fix-plan.md` / `agent-fix-plan.md`（修复排期）
> 版本：v1.0 ｜ 生效日期：2026-07-16 ｜ 适用范围：所有对 `Rag/`、`zhiwei-web/`、`data/`、`docker-compose.yml`、`Dockerfile`、CI 配置的 PR
> 维护者：项目 Maintainer

---

## 0. 为什么需要这份文档

之前的 `code-review-report.md` 暴露了**系统性**而非偶发的问题：

- 语义检索被一个方法名拼写错误 + 吞异常**静默废掉**（核心能力失效 8 个月无人察觉）；
- 后端所有接口**无认证、无限流**，多个接口直接触发付费 LLM，存在成本滥用与 DoS 风险；
- `rag_modules/` 下大量**遗留/实验代码**未接入运行链路，却藏着潜在的 Cypher 注入；
- 密钥、CORS、弱口令、前端容错等隐患分散在多处。

**结论**：光做"一次性审查"不够，必须把这些教训固化为**团队红线 + 流程门禁 + CI 卡点**，否则同样的 bug 会反复出现。本文件就是把"临时修复"升级为"永久标准"。

---

## 1. 角色与职责

| 角色 | 责任 |
|---|---|
| **作者（Author）** | 自审、本地跑通 lint/test/build、为关键路径补测试、完整填写 PR 模板、主动回应每条审查意见 |
| **审查者（Reviewer）** | 至少 1 人；**安全 / 核心检索链路 / 公共接口**改动需 ≥2 人且含 1 名资深；用本文严重度标注问题；不默认通过 |
| **守门人（Maintainer）** | 拥有合并权；合并前确认 **CI 全绿 + 红线无违反 + 必要审批数达标 + 无未解决 🔴** |

> 小改动（文档、样式微调）可 1 人审查；涉及 `api.py`、检索链路、认证、密钥、Docker 暴露面的 PR 一律按"核心改动"处理。

---

## 2. 严重程度定义（与审查报告一致）

| 标记 | 名称 | 含义 | 合并策略 |
|---|---|---|---|
| 🔴 | **阻断（Blocker / 高）** | 功能失效、安全漏洞、数据风险、破坏对外契约 | **必须修复**，禁止带 🔴 合并 |
| 🟡 | **应修复（Should / 中）** | 性能、稳健性、安全隐患 | 合并前尽量修；或建 issue 跟踪并在 PR 注明"将跟进 #issue" |
| 💭 | **建议（Nit / 低）** | 风格、小改进、文档 | 可后续处理，不阻塞合并 |

> 一条意见只能有一个严重度。当"应修复"与合并冲突时，优先用 issue 跟踪并在 PR 描述里链接，避免 PR 无限期挂起。

---

## 3. 团队红线（Red Lines / Zero Tolerance）

以下 9 条是**非协商底线**，违反任意一条的 PR 直接打回，不等商量。它们都来自真实事故或高风险点。

- **RL1 注入零容忍**：任何 Cypher / SQL / Milvus filter 必须**参数化**（`$param` / 占位符），禁止 f-string / `%` / `+` 拼接用户输入。CI 用 ruff `S608` 卡点。（对应报告 #5、#1）
- **RL2 付费/写接口先认证**：触发付费 LLM 或写操作的端点，合并前必须有**认证 + 限流**（至少 API Key + 每 IP QPS/并发上限）。不允许"先上线后加"。（报告 #2）
- **RL3 密钥红线**：仓库内只允许 `.env.example`（占位）；真实密钥只来自 secret 管理器 / 部署平台。CI 必须跑 `gitleaks` / `trufflehog`，密钥提交即失败。（报告 #13）
- **RL4 异常不外泄**：内部异常（`str(exc)`、堆栈、内部路径、SDK 细节）**不得回传客户端**，只返回通用文案 + `trace_id`，详情仅入日志。（报告 #4）
- **RL5 死代码即债务**：未接入运行链路的模块必须标 `# EXPERIMENTAL: not wired into the running pipeline` 或整体移入 `Rag/experimental/`；**禁止对死代码写"看起来有用"的测试**。（报告 #3）
- **RL6 核心链路必须有集成测试**：`hybrid_search` 的 `semantic`/`combined` 分支、Neo4j 导入、SSE 流式输出等关键链路，改动后至少一条**集成/契约测试**兜底；纯 mock 测试不算（本次拼写 bug 正是只测 mock 而漏网）。（报告 #1）
- **RL7 外部调用必须超时 + 可取消**：所有 LLM / DB / HTTP 调用必须带超时（`request_timeout`/`httpx timeout`）；流式端点必须有**客户端断开即取消**的生成端逻辑。（报告 #12）
- **RL8 前端安全基线**：LLM 输出保持以**文本节点**渲染（天然防 DOM XSS，勿改）；`.npmrc` 不得 `audit=false`；`nginx.conf` 必须带 `X-Content-Type-Options` / `X-Frame-Options` / 受限 `CSP`。（报告 #8、#9）
- **RL9 数据权威一致性**：运行时权威数据源是 `data/graph/cypher/` 的 Neo4j 图谱资产。增删改菜谱必须同步更新图谱 CSV / 导入脚本，**不能只改 Markdown**。（README 已明确）

---

## 4. 审查清单（Checklist）

审查者逐项勾选；作者提交前也应自审一遍。

### 4.1 通用（所有 PR）

- [ ] PR 模板填写完整，关联了对应 issue（如源自审查报告）
- [ ] 自审通过：`pre-commit` 已跑（格式 / 大文件 / 密钥 / eslint）
- [ ] 关键路径有测试；核心链路改动满足 **RL6**
- [ ] 无密钥 / 内部信息泄露（gitleaks 绿、无 `str(exc)` 回传）
- [ ] 无死代码混入运行链路（满足 **RL5**）
- [ ] CI 全绿（lint + test + build + secret 扫描）

### 4.2 后端 Python（`Rag/`）

- [ ] **输入校验**：FastAPI 端点用 Pydantic 约束（参考现有 `api.py` 的 `ChatRequest`），长度 / 类型 / 范围明确
- [ ] **认证与限流**：付费 / 写端点挂 `Depends(verify_api_key)` + `@limiter.limit(...)`（**RL2**）
- [ ] **注入防护**：Cypher / SQL / Milvus filter 全部参数化，无拼接（**RL1**）
- [ ] **异常处理**：仅"非核心降级"的 `except` 允许吞错，且必须 `logger.exception`；核心失败不得静默返回空（**报告 #1 教训**）
- [ ] **LLM 调用**：`ChatOpenAI` 带 `request_timeout` + `max_retries`；助手类提示有边界护栏防提示注入（**RL7**、报告 #14）
- [ ] **流式输出**：SSE 正确设置 `X-Accel-Buffering: no`；客户端断开能取消
- [ ] **配置**：密钥走 `os.getenv`，不在代码写死；生产域名走 `CORS_ORIGINS` 环境变量

### 4.3 前端 React（`zhiwei-web/`）

- [ ] **XSS 防护**：LLM / 接口文本以 `{text}` 文本节点渲染，不用 `dangerouslySetInnerHTML`（**RL8**）
- [ ] **SSE 容错**：`JSON.parse(data)` 包 `try/catch`，坏帧 `continue` 跳过，不中断读取循环（报告 #10）
- [ ] **失败显式提示**：搜索 / 请求失败显式报错，**不静默回退**其他模式（报告 #10）
- [ ] **输入约束**：聊天 / 搜索输入长度限制与后端 `ChatRequest` 一致（报告 #10）
- [ ] **依赖安全**：`npm ci` 锁定；`npm audit` 无高危；`.npmrc` 无 `audit=false`（**RL8**）
- [ ] **代码质量**：`npm run lint`（ESLint 9）无 error；`npm run build` 通过
- [ ] **外链校验**：`image_url` 等外链做白名单域名校验（报告 #10）

### 4.4 基础设施 / Docker / CI

- [ ] **Dockerfile**：以非 root `USER` 运行（报告 nit）
- [ ] **.dockerignore**：含 `.env`、`node_modules`、测试文件（已满足，勿回退）
- [ ] **docker-compose**：生产不为 Milvus(19530) / MinIO(9000) 映射宿主机端口；MinIO 改强口令（报告 #7）
- [ ] **nginx**：带 `X-Content-Type-Options` / `X-Frame-Options` / `Content-Security-Policy`（**RL8**）
- [ ] **CI 门禁**：含 ruff `S608` + gitleaks + `pytest` + 前端 `npm test` + `npm audit`（见第 7 节，关闭现有 gap）

---

## 5. 审查流程（Process）

```
作者自审 ──► 开 PR（填模板）──► CI 自动门禁 ──► 人工审查（≥1 / 核心≥2）
                                                          │
                                   🔴 必须改 ◄── 意见分类 ─┤ 🟡 改 or 建 issue
                                                          │ 💭 可后续
                                                          ▼
                                     合并门槛：CI绿 + 红线无违反 + 审批达标
                                                          ▼
                                                 合并 + 删分支 + 更新文档
```

1. **作者自审**：本地跑 `ruff check . && ruff format --check . && pytest -q`（后端）、`npm run lint && npm run build && npm test`（前端）；确认 pre-commit 通过。
2. **开 PR**：填写 PR 模板，关联 issue（如源自 `code-review-report.md` 的某条）。PR 标题体现改动性质（fix / feat / refactor / security）。
3. **CI 自动门禁**：push / PR 触发 CI（见第 7 节）。CI 不绿不得进入人工审查。
4. **人工审查**：
   - 普通改动 ≥1 审批；安全 / 核心链路 / 公共接口 ≥2 审批（含 1 资深）。
   - 审查者用 🔴 / 🟡 / 💭 标注，每条说明"为什么"和"建议怎么改"（像导师，不像门卫）。
5. **处理意见**：作者逐条回应；🔴 必须修；🟡 修或建 issue 并在 PR 注明；💭 可后续。
6. **合并门槛**（守门人核对）：CI 全绿 ✅ + 9 条红线无违反 ✅ + 必要审批数 ✅ + 无未解决 🔴 ✅。
7. **合并后**：删除功能分支；涉及架构 / 激活链路 / 接口契约的改动，同步更新 `README.md` 或架构文档。

---

## 6. PR 模板（建议落地为 `.github/PULL_REQUEST_TEMPLATE.md`）

已在仓库提供同名文件，开 PR 时自动带出。要点：

- **改动类型 / 关联 issue**
- **改了什么、为什么**（对照红线说明安全性影响）
- **如何测试**（含集成测试证据，满足 RL6）
- **安全自查**：是否触及认证 / 密钥 / 注入 / 外部调用超时（RL1/2/3/4/7）
- **审查者 checklist**：已自审的 4 类清单

---

## 7. CI 加固清单（把标准变成门禁）

现有 `.github/workflows/ci.yml` 已跑 ruff / pytest / eslint / build，但存在以下 **gap**，需补齐才能让第 3 节红线自动生效：

| # | 缺口 | 动作 | 对应红线 |
|---|---|---|---|
| C1 | ruff 未启用注入规则 | `pyproject.toml` 的 `select` 增加 `S`（至少 `S608`），拼接 Cypher/SQL 的 PR 直接失败 | RL1 |
| C2 | CI 未跑密钥扫描 | CI 增加 `gitleaks detect`（pre-commit 已有，但 CI 没跑，靠人工 commit 才拦） | RL3 |
| C3 | 前端测试未进 CI | `frontend` job 增加 `npm test`（目前只 lint+build，漏掉 `node --test` 用例） | RL6 |
| C4 | 前端依赖漏洞未扫 | `frontend` job 增加 `npm audit --audit-level=high`；并先修 `.npmrc` 的 `audit=false` | RL8 |
| C5 | 分支保护未设审批数 | 仓库设置：main 分支保护，要求 ≥1 审批 + CI 通过；核心目录（`Rag/api.py`、`docker-compose.yml`）可加 CODEOWNERS 强制资深审批 | 流程第 4 步 |
| C6 | ruff 版本落后 | pre-commit `ruff-pre-commit` rev 与 CI `pip install ruff` 对齐到同一版本，避免本地修、CI 又报 | 一致性 |

> 推荐：`pre-commit` 已在本地拦格式 / 大文件 / 密钥，CI 再跑一遍是双保险。C2 尤其重要——密钥一旦进 git 历史，轮换成本极高。

---

## 8. 与既有审查报告的衔接

| 报告发现 | 已落地为红线 / 流程 |
|---|---|
| #1 语义检索静默失效 | RL6（集成测试兜底）+ 4.2 异常处理规则 |
| #2 无认证 / 限流 | RL2（付费接口先认证）+ PR 模板安全自查 |
| #3 遗留 / 实验代码 | RL5（死代码标注 / 移出） |
| #5 Cypher 注入 | RL1（参数化零容忍）+ C1（ruff S608） |
| #4 异常回传 | RL4（异常不外泄） |
| #8 / #9 前端安全 | RL8（npmrc / nginx 安全头） |
| #12 LLM 无超时 | RL7（外部调用超时 + 可取消） |
| #13 密钥治理 | RL3（密钥红线）+ C2（CI gitleaks） |

---

## 9. 反面案例（用于团队培训，避免重蹈覆辙）

**案例 A — 静默失效的语义检索**
`Rag/main.py` 调用了不存在的 `self.milvus.search_similar_documents(...)`，真实方法名是 `similarity_search`。异常被 `except` 吞掉 → 向量检索永远返回空，"混合检索"实际只剩图检索 + 关键词。测试只 mock 了 `GraphHybridRetrieval`，从未真正走到 Milvus 调用，bug 漏网 8 个月。
*教训*：核心失败**不能吞**；**纯 mock 测试兜不住集成链路**（RL6）。

**案例 B — 潜在 Cypher 注入**
`graph_rag_retrieval.py` 用 f-string 把 LLM 返回的 `max_depth` 拼进 Cypher：`MATCH path = (source)-[*1..{max_depth}]-(target)`。若 LLM 返回字符串即构成 Cypher 注入。因该模块未接线而暂不触发，但属于"定时炸弹"。
*教训*：所有查询语言一律参数化（RL1），遗留模块也不得留注入口。

---

## 10. 附则

- 本标准为**最低底线**，不取代深度人工审查；审查者仍应对设计、可读性、可维护性负责。
- 标准随项目演进，由 Maintainer 每季度回顾一次，结合新审查发现更新红线。
- 新人 onboarding 必须读 `code-review-report.md` + 本文，理解"为什么我们有这些红线"。
