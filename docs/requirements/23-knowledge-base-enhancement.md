# 需求 23：知识库增强（预览与分块 / 重索引 / 格式扩展 / 重排）

> 状态：**草稿**（需求 21 批次 A2 的落地设计；评审后冻结实施）。
> 父需求：`docs/requirements/21-product-modules-completeness.md` §4-D / §5-A2。
> 飞书镜像：待同步。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 23 |
| 标题 | 知识库增强（预览与分块 / 重索引 / 格式扩展 / 重排） |
| 优先级 | P1 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待建 |
| 关联 PR | 待填 |
| 创建 / 更新 | 2026-10-03 |

## 2. 问题背景

需求 21 差距表 D 层：KB 已能「上传→异步摄取→检索引用」，但**不可管理、不可观察**——
上传后看不到原文与分块，解析失败原因要翻日志；文档列表只从 Qdrant 聚合出 3 个字段
（`doc_id/source/chunks`），状态/大小/时间全无；检索重排能力写进了配置却从未接线；
格式仅 4 类。市面对标（RAGFlow 解析可视化、open-notebook 多格式 Sources）已把 KB 作为一等公民页面。

## 3. 需求分析

**目标**：把 KB 从「上传通道」升级为「可预览、可整理、可重建、可信赖」的文档管理层。

**量化定义**：

- 预览首屏（首 20 分块）< 1s（PG 直读，不经 Qdrant）；
- 文档列表 100% 呈现 `status/size/chunks/created_at/display_name`，失败原因可见；
- 支持格式 6 → 9（+pptx / xlsx / html），全部走既有 magic bytes 校验；
- rerank 接线后，用既有 eval 体系给出开启前后检索命中率/引用准确率对比，达标（命中率不降、引用不降）才默认开；
- 重索引幂等：重复执行不产生重复向量（`delete_by_doc` + upsert）。

## 4. 当前设计（现状与痛点）

### 4.1 上传与解析

- 白名单 `{.pdf,.docx,.md,.markdown,.txt,.text}`（`web/backend/upload_guard.py:24`），
  magic bytes + DOCX ZIP 结构校验（:82-105）。
- 限额 `DR_RAG_MAX_FILE_MB` 默认 10.0（`main.py:306`），上传限流 10 次/分（:307-308），入口 `POST /api/rag/ingest`（:1566）。
- 解析：`research_engine/rag/ingest.py:47` `parse_file` —— pdf → `pypdf`（:66）、docx → `python-docx`（:87）、
  md/txt → UTF-8 文本（:102-106）；分块/嵌入/upsert 入口 `ingest_file`（:147）。
- 解析限额（`config.py:88-100`）：`chunk_size=800` / `chunk_overlap=100` / `top_k=5` / `use_rerank=False` /
  `max_pages=200` / `max_chars=2e6` / `max_chunks=2000` / `parse_timeout=60s`。

### 4.2 摄取管线与表

- 状态机 `pending → processing → ready | rejected | deleted`（`migrations/0006:23-24`）；
  `FOR UPDATE SKIP LOCKED` + 租约领取（`web/backend/store.py:1683-1708`）。
- 失败重试：指数退避 `30*2^(n-1)` 上限 900s，3 次耗尽 `rejected`（`web/backend/ingestion.py:31-33、154-162`）。
- 隔离区文件在终态删除（`ingestion.py:122-161`），保留期清扫 90 天（:168-189）。
- 表 `rag_ingestions`（`migrations/0006:15-36`）：ingestion_id / doc_id / user_id / source / sha256 / size_bytes /
  stored_name / status / chunks / attempts / next_attempt_at / lease_expires_at / claimed_by / scan_status /
  last_error / created_at / updated_at / processed_at；活跃唯一 `(COALESCE(user_id,''),doc_id) WHERE status<>'deleted'`（0017:37-39）。
- **分块只存 Qdrant payload**（`ingest.py:167-178`），PG 无文本快照。

### 4.3 检索与删除

- `HybridRetriever`（`research_engine/rag/retriever.py:23`）：向量（:93-110）+ BM25（:112-133），
  融合去重取 top_k（:135-152）；`researcher.py:146` 传 5。
- **`use_rerank` 仅在 `config.py:94` 定义，全仓无引用（未接线）**。
- 删除 `DELETE /api/rag/docs`（`main.py:1690`）：Qdrant `delete_by_doc(wait=True)` + count 验证归零（:1713-1716）
  → PG 置 `deleted` 并清 `stored_name`（:1723）→ 删隔离区文件（:1724-1730）；Qdrant 失败返回 503（:1710、1718）。
- 列表 `GET /api/rag/docs`（:1734）：只从 Qdrant `scroll_all` 聚合，返回 `{doc_id, source, chunks}`（:1749-1756），
  **缺 status/size/时间/失败原因**；Qdrant 不可用 503（:1744）。

### 4.4 前端与依赖

- UI 在 `web/frontend/src/components/AccountPanel.tsx` + `features/knowledge-base/useUploads.ts`；
  testid：`kb-toggle` / `kb-panel` / `kb-refresh` / `rag-upload-input` / `upload-queue` / `upload-item` /
  `upload-progress` / `upload-retry|cancel|remove` / `upload-state` / `kb-error` / `kb-doc-item` / `kb-delete*`。
- 依赖：`beautifulsoup4` 已有；**无 `python-pptx` / `openpyxl`**——扩展格式需新增依赖并更新 lock。

### 4.5 痛点

1. 上传后是黑盒：不知道解析成多少块、有没有失败、失败为什么；
2. 列表数据源与权威库错位（Qdrant 聚合 ≠ PG 台账），状态不可见；
3. 想换嵌入模型/修复索引只能删了重传；
4. 生产 RAG 标配的混合+重排只完成一半（重排未接线）。

## 5. 优化方案

### 5.1 数据模型（迁移；编号顺延，与需求 22 的 0018 互斥则取 0019）

```sql
ALTER TABLE rag_ingestions ADD COLUMN display_name text;
ALTER TABLE rag_ingestions ADD COLUMN tags jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE rag_chunks (
  doc_id      text NOT NULL,
  chunk_index int  NOT NULL,
  content     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (doc_id, chunk_index)
);
```

> `rag_chunks` 为解析产物文本快照（受 `max_chars=2e6` 约束，单文档 ≤ ~2MB），用于预览/分块查看/重索引；
> 原始文件仍按现行终态删除策略执行（不长期留存原文件）。`rag_chunks` 随 `DELETE /api/rag/docs` 一并清理。

### 5.2 API 变更

| 方法与路径 | 变更 | 语义与约束 |
|---|---|---|
| `GET /api/rag/docs` | **数据源改 PG 台账** | 返回 `doc_id/source/display_name/tags/status/chunks/size_bytes/created_at/last_error`；Qdrant 只做检索 |
| `GET /api/rag/docs/{doc_id}/chunks?offset=&limit=` | 新增 | 分页读 `rag_chunks`，供预览/分块查看 |
| `PATCH /api/rag/docs` | 新增，body `{doc_id, display_name?, tags?}` | 仅本人；`display_name` ≤ 120 字；审计 `rag_doc_updated` |
| `POST /api/rag/docs/reindex` | 新增，body `{doc_id}` | 从 `rag_chunks` 重嵌入 → `delete_by_doc(wait=True)` + 重新 upsert；幂等；审计 `rag_reindexed` |
| `GET /api/rag/usage` | 新增 | 本人 `SUM(size_bytes)`（`status <> 'deleted'`）+ 配额（`DR_RAG_TOTAL_MB`，默认 0=不限） |

### 5.3 摄取管线改动

- 解析完成后同事务写 `rag_chunks`（批插，单文档 ≤ 2000 行）；
- 删除流程追加「清 `rag_chunks`」；
- 保留期清扫追加「删文档时级联清 chunks」；
- `reindex` 复用 ingestion 租约防并发（同一 doc_id 加活跃唯一约束外的新锁或状态位）。

### 5.4 格式扩展

| 格式 | 解析器 | 依赖 | 备注 |
|---|---|---|---|
| pptx | `python-pptx` 抽取每页文本框 | 新增（lock 更新） | OOXML（ZIP）magic 已在 docx 分支处理，需注册表扩展 |
| xlsx | `openpyxl` `read_only` 逐 sheet 抽单元格 | 新增（lock 更新） | 行数/单元格限额沿用 `max_chars` |
| html | `beautifulsoup4`（已有）取正文 | 无 | 本地文件；网页 URL 抓取留后续（复用 `research_engine/net/safe_fetch.py`） |

- 解析器改为**注册表**（ext → parser），`ingest.py` 分支改为查表；
- `ALLOWED_EXT`、错误码文档、`.env.example` 说明同步更新。

### 5.5 检索重排（接线）

- 新增 `DR_RAG_RERANK=1` + `DR_RAG_RERANK_MODEL=gte-rerank`（DashScope 百炼 rerank，复用现有 `DASHSCOPE_API_KEY`）；
- 链路：混合检索取 top-20 → rerank → 取 top-5 入 prompt；
- **fail-open**：重排调用失败/超时回落混合排序并记指标（与限流 fail-open 同口径）；
- 标定：用既有 eval 体系（`docs/eval-report.md` 口径）跑开启/关闭对照，命中率与引用准确率不降才默认开；
- 延迟/成本写入 metrics，便于观察窗口决策。

### 5.6 前端

- KB 面板升级：文档行显示 状态徽章 / 大小 / 块数 / 时间 / 失败原因（`last_error` 摘要）；
- 操作：预览（分块列表，命中高亮第 N 块）、重命名、标签、重索引、删除（保留两步确认）；
- 容量：面板顶部「已用 x MB（/ 配额）」；
- 新增 testid：`kb-doc-status`、`kb-doc-size`、`kb-doc-preview`、`kb-chunk-list`、`kb-rename`、
  `kb-reindex`、`kb-usage`。

## 6. 设计策略

1. **PG 权威、Qdrant 派生**：列表/预览/管理全部走 PG；Qdrant 只负责向量检索，删除顺序沿用「先向量后台账」；
2. **重索引不重解析**：`rag_chunks` 是重嵌入的唯一输入，换 embedding 模型只需 reindex；
3. **不引重型依赖**：不做本地 cross-encoder（`sentence-transformers/torch` 在 4GB 机型不现实），用云 rerank；
4. **原文件不留存**：隐私最小化口径不变，只保留解析文本与向量（保留期同政策）；
5. **格式扩展走注册表**：每个解析器独立可测，新增格式不动主管线；
6. **回退路径**：rerank 开关默认关，删除/重索引全部幂等可重放。

## 7. 验收标准（DoD）

- [ ] 迁移幂等可重跑 + schema 自检（`rag_chunks` / 新列 / 索引）
- [ ] 列表改 PG 台账：状态/大小/时间/失败原因可见；Qdrant 不可用时列表仍可用（预览不依赖 Qdrant）
- [ ] 预览/分块接口分页正确；删除与保留期清扫级联清 `rag_chunks`
- [ ] 重索引幂等（重复执行向量数不增）；并发重索引有互斥
- [ ] pptx/xlsx/html 三格式上传-解析-检索-引用全链路通过；lock 文件更新
- [ ] rerank 接线 + fail-open + eval 对照报告（达标后默认开）
- [ ] 前端新 testid E2E 全绿；`tsc`/`build`/`ruff` 全绿
- [ ] 回填需求 21 §10 与 `.env.example`

## 8. 影响范围与风险

| 风险 | 对策 |
|---|---|
| PG 文本体积 | `max_chars=2e6` 上限 + 单文档 ≤ 2000 块；粗估 100 文档 ≤ 200MB，4C 机型可承受 |
| 新依赖体积/安全 | `python-pptx`/`openpyxl` 均为纯 Python 小型库；进 lock 并跑 CI |
| rerank 延迟/费用 | fail-open + metrics；eval 不达标不默认开 |
| 重索引并发 | 复用租约/状态位；同时只允许一个 reindex |
| 旧文档无 chunks | 迁移后旧文档预览为空 → 提供「重索引」前先提示；不做自动回填（避免批量重解析） |
| 删除一致性 | 沿用「Qdrant 失败 503 不继续」口径，避免台账先行造成向量残留 |

## 9. 测试策略

- 单测：三新格式解析器（含损坏文件）、chunks 写入/清理、reindex 幂等、rerank fail-open；
- 集成（真实 PG+Qdrant，CI `infra` job 模式）：摄取→列表→预览→重索引→删除 全链路；
- E2E：预览/重命名/重索引/容量视图；
- eval：rerank 开启前后对照（命中率/引用准确率/延迟），出报告后决定默认值。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-03 | 新建 | 需求 21 批次 A2 立项 | 初稿：现状核实（解析/管线/检索/删除/前端）+ `rag_chunks` + 重索引 + 3 格式 + rerank 接线 + DoD | 本文档 |
