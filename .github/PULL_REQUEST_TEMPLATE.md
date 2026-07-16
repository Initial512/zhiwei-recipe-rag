<!--
知味代码审查 PR 模板
提交前请完整填写；关联 issue（如源自 code-review-report.md 的某条发现）。
审查者将依据 CODE_REVIEW_STANDARD.md 的 🔴/🟡/💭 严重度给出意见。
-->

## 改动概述

- **类型**：<!-- fix / feat / refactor / security / docs / infra -->
- **关联 issue / 审查报告条目**：<!-- 例：code-review-report.md #2；或 #123 -->
- **改了什么**：
- **为什么**：

## 测试与验证

- [ ] 后端自测：`cd Rag && ruff check . && ruff format --check . && pytest -q`
- [ ] 前端自测：`cd zhiwei-web && npm run lint && npm run build && npm test`
- [ ] **集成 / 契约测试证据**（核心链路改动必填，对应 RL6）：
  <!-- 例：新增 test_fix_milvus.py，断言 hybrid_search 在 semantic 策略下返回 Milvus 结果 -->

## 安全自查（涉及以下任一项请逐项确认，对应 CODE_REVIEW_STANDARD.md 红线）

- [ ] **RL1 注入**：Cypher / SQL / Milvus filter 全部参数化，无 f-string / `%` / `+` 拼接
- [ ] **RL2 认证**：付费 LLM / 写接口已加认证 + 限流
- [ ] **RL3 密钥**：无真实密钥提交；仅 `.env.example` 占位
- [ ] **RL4 异常**：未把 `str(exc)` / 堆栈 / 内部路径回传客户端
- [ ] **RL5 死代码**：遗留 / 实验模块已标 `# EXPERIMENTAL` 或移入 `experimental/`，未给死代码补"看起来有用"的测试
- [ ] **RL7 超时**：LLM / DB / HTTP 调用带超时；流式端点可取消
- [ ] **RL8 前端安全**：LLM 输出仍作文本节点；`.npmrc` 无 `audit=false`；nginx 带安全响应头
- [ ] **RL9 数据权威**：菜谱改动已同步 `data/graph/cypher/` 图谱资产

## 审查者 Checklist（作者已自审）

- [ ] 通用：PR 模板完整、CI 全绿、无密钥泄露、无死代码混入
- [ ] 后端（如涉及）：输入校验 / 认证限流 / 注入防护 / 异常不吞核心失败 / LLM 超时
- [ ] 前端（如涉及）：XSS 防护 / SSE 容错 / 失败显式提示 / 依赖安全
- [ ] 基础设施（如涉及）：Dockerfile 非 root / 端口不暴露 / nginx 安全头 / CI 门禁

## 备注 / 后续 issue

<!-- 未在本 PR 处理的 🟡 项，在此链接跟进 issue -->
